"""identity public interface (EPIC-02 spec §3.2, §4): agencies, users and memberships, sign-in
and server-side sessions, the audit log.

**Sessions (ADR 0010, spec §4.1).** Sign-in creates a ``user_sessions`` row keyed by the SHA-256
of a 256-bit random id; the id itself goes to the browser in the session cookie and is never
stored. :func:`authenticate` runs on every authenticated request: one query loads the session,
its user, the membership in the session's active agency, that agency and the membership's
clients, and everything is checked **live** (revoked, idle or absolute expiry, user disabled,
membership deactivated, agency suspended), so a change takes effect on the next request. Activity
moves the idle deadline, but ``last_seen_at`` / ``idle_expires_at`` are written at most every
:data:`TOUCH_INTERVAL`, so most reads stay reads.

**Rotation (session fixation).** Sign-in and a password change always create a new session id
and revoke the session the request came with; an agency switch keeps the id (same user, same
authentication).

**No account enumeration (spec §4.3).** Unknown email, wrong password, a user without a password
(OIDC only, E2-08) and a disabled user all get the same :class:`InvalidCredentials`, after the
same Argon2 work (a dummy hash for unknown users). Only the account holder, with the right
password, can learn that they have no agency access (:class:`NoAgencyAccess`).

**Throttling (spec §4.6).** Before any password work: at most ``LOGIN_MAX_ATTEMPTS_PER_IP``
attempts per client IP and ``LOGIN_MAX_FAILURES_PER_EMAIL`` failures per email per
``LOGIN_RATE_LIMIT_WINDOW``, counted in Redis under keyed hashes (``app.platform.ratelimit``).
Over a limit is :class:`RateLimited` (429 with ``Retry-After``), audited once per window. Nothing
locks an account: the counters expire with their window.

**Audit (spec §4.7).** :func:`record_audit` adds an ``audit_events`` row in the caller's
transaction, so the event exists exactly when the change commits; failed sign-ins and rate
limits commit their event on their own (the request then fails).

Operator commands (``app.modules.identity.cli``) and the development seed use
:func:`create_agency`, :func:`rename_agency`, :func:`ensure_user` and :func:`ensure_membership`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import ipaddress
import re
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.identity import passwords, repository
from app.modules.identity.models import (
    Agency,
    AgencyStatus,
    AuditEvent,
    Membership,
    MembershipStatus,
    User,
    UserSession,
    UserStatus,
)
from app.modules.identity.passwords import PasswordPolicyError
from app.modules.identity.schemas import (
    ActiveMembership,
    AgencySummary,
    Principal,
    Role,
    SignedIn,
)
from app.modules.identity.schemas_v2 import (
    Agency as AgencyResource,
)
from app.modules.identity.schemas_v2 import (
    AgencyRef,
    ClientScope,
    Me,
    MeUser,
)
from app.modules.identity.schemas_v2 import (
    Membership as MembershipResource,
)
from app.platform.config import settings
from app.platform.ratelimit import Limit, RateLimitStore, hashed

TOUCH_INTERVAL: Final = timedelta(minutes=5)
"""``last_seen_at`` / ``idle_expires_at`` are written at most this often per session."""

USER_AGENT_MAX: Final = 500
SLUG_MAX: Final = 100


# ── Errors ────────────────────────────────────────────────────
class InvalidCredentials(Exception):
    """Unknown email, wrong password or disabled user: one answer for all (401)."""

    detail = "Invalid email or password"


class NoAgencyAccess(Exception):
    """Right password, but no active membership in an active agency (403)."""

    detail = "Your account has no active agency access"


class RateLimited(Exception):
    """Over a rate limit (429); ``retry_after`` in seconds."""

    detail = "Too many attempts. Try again later."

    def __init__(self, retry_after: int) -> None:
        super().__init__(self.detail)
        self.retry_after = retry_after


class NotFoundError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PermissionDenied(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class InvalidInput(Exception):
    """A request field the service rejects (422 with the field's location)."""

    def __init__(self, field: str, detail: str) -> None:
        super().__init__(detail)
        self.field = field
        self.detail = detail


class OperatorError(Exception):
    """An operator command (CLI, seed) cannot do what it was asked."""


# ── Small helpers ─────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ClientInfo:
    """Where a request came from, for sessions and audit events (never trusted for anything
    else). ``ip`` comes from the socket (spec §4.6)."""

    ip: str | None = None
    user_agent: str | None = None

    @classmethod
    def of(cls, host: str | None, user_agent: str | None) -> ClientInfo:
        ip: str | None = None
        if host:
            try:
                ip = str(ipaddress.ip_address(host))
            except ValueError:
                ip = None
        return cls(ip, (user_agent or None) and user_agent[:USER_AGENT_MAX])


def utcnow() -> datetime:
    return datetime.now(UTC)


def normalise_email(email: str) -> str:
    """How emails are stored and looked up: trimmed and lower-cased."""
    return email.strip().lower()


def new_session_token() -> str:
    """32 random bytes (256 bits), URL-safe: the session cookie's value."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    """What ``user_sessions.id_hash`` stores for a session cookie value."""
    return hashlib.sha256(token.encode()).digest()


def login_limits() -> tuple[Limit, Limit]:
    """``(per IP: attempts, per email: failures)`` from the settings."""
    window = settings.LOGIN_RATE_LIMIT_WINDOW
    return (
        Limit("login_ip", settings.LOGIN_MAX_ATTEMPTS_PER_IP, window),
        Limit("login_email", settings.LOGIN_MAX_FAILURES_PER_EMAIL, window),
    )


# ── Audit ─────────────────────────────────────────────────────
async def record_audit(
    db: AsyncSession,
    action: str,
    *,
    agency_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: object | None = None,
    client: ClientInfo | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Add an audit event to the caller's transaction (spec §4.7). ``details`` must never hold a
    password, token, session id or raw email of someone who failed to sign in."""
    repository.add_audit_event(
        db,
        AuditEvent(
            action=action,
            agency_id=agency_id,
            actor_user_id=actor_user_id,
            target_type=target_type,
            target_id=None if target_id is None else str(target_id),
            ip=client.ip if client else None,
            user_agent=client.user_agent if client else None,
            details=dict(details or {}),
        ),
    )
    await db.flush()


async def _rate_limited(
    db: AsyncSession, store: RateLimitStore, limit: Limit, key: str, client: ClientInfo
) -> None:
    if await store.mark_once(key, limit.seconds):
        await record_audit(db, "auth.rate_limited", client=client, details={"limit": limit.name})
        await db.commit()


# ── Sessions ──────────────────────────────────────────────────
async def authenticate(
    db: AsyncSession, token: str, *, now: datetime | None = None
) -> SignedIn | None:
    """The session whose cookie value is ``token``, if it is valid now; ``None`` otherwise
    (unknown, revoked, idle or absolutely expired, or its user disabled).

    Writes ``last_seen_at`` (and moves the idle deadline) at most every :data:`TOUCH_INTERVAL`;
    the caller commits.
    """
    now = now or utcnow()
    id_hash = hash_token(token)
    row = await repository.load_session(db, id_hash)
    if row is None:
        return None
    session, user = row.session, row.user
    if (
        session.revoked_at is not None
        or now >= session.idle_expires_at
        or now >= session.absolute_expires_at
        or user.status != UserStatus.active.value
    ):
        return None

    membership: ActiveMembership | None = None
    if (
        row.membership is not None
        and row.agency is not None
        and row.membership.status == MembershipStatus.active.value
        and row.agency.status == AgencyStatus.active.value
    ):
        membership = ActiveMembership(
            id=row.membership.id,
            agency_id=row.agency.id,
            agency_name=row.agency.name,
            role=Role(row.membership.role),
            all_clients=row.membership.all_clients,
            client_ids=frozenset(row.client_ids),
        )

    if now - session.last_seen_at >= TOUCH_INTERVAL:
        idle = min(now + settings.SESSION_IDLE_TIMEOUT, session.absolute_expires_at)
        await repository.touch_session(db, id_hash, now, idle)
        if membership is not None:
            await repository.touch_membership(db, membership.id, now)

    return SignedIn(
        token=token,
        id_hash=id_hash,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        active_agency_id=session.active_agency_id,
        absolute_expires_at=session.absolute_expires_at,
        membership=membership,
    )


async def _start_session(
    db: AsyncSession,
    user_id: uuid.UUID,
    active_agency_id: uuid.UUID | None,
    client: ClientInfo,
    now: datetime,
) -> str:
    token = new_session_token()
    absolute = now + settings.SESSION_ABSOLUTE_TIMEOUT
    db.add(
        UserSession(
            id_hash=hash_token(token),
            user_id=user_id,
            active_agency_id=active_agency_id,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=min(now + settings.SESSION_IDLE_TIMEOUT, absolute),
            absolute_expires_at=absolute,
            ip=client.ip,
            user_agent=client.user_agent,
        )
    )
    await db.flush()
    return token


async def login(
    db: AsyncSession,
    store: RateLimitStore,
    *,
    email: str,
    password: str,
    carried_token: str | None,
    client: ClientInfo,
) -> str:
    """Sign in with email and password (spec §4.3); returns the **new** session's cookie value.

    Raises :class:`RateLimited`, :class:`InvalidCredentials` or :class:`NoAgencyAccess`. On
    success the session the request carried (``carried_token``, any user's) is revoked, a new
    one is created with the active agency chosen (the only membership, else the most recently
    used, else none), and the transaction is committed.
    """
    now = utcnow()
    address = normalise_email(email)
    ip_limit, email_limit = login_limits()
    ip_key = ip_limit.key(hashed(client.ip or "unknown"))
    email_key = email_limit.key(hashed(address))

    # 1. Limits first, before any password work.
    attempts = await store.hit(ip_key, ip_limit.seconds)
    if attempts.count > ip_limit.max_hits:
        await _rate_limited(db, store, ip_limit, ip_key, client)
        raise RateLimited(attempts.retry_after)
    failures = await store.peek(email_key)
    if failures.count >= email_limit.max_hits:
        await _rate_limited(db, store, email_limit, email_key, client)
        raise RateLimited(failures.retry_after)

    # 2. The same work whether or not the account exists.
    user = await repository.get_user_by_email(db, address) if address else None
    if user is None or user.password_hash is None:
        await passwords.verify_dummy(password)
        verified = False
    else:
        verified = await passwords.verify_password(user.password_hash, password)

    if not verified or user is None or user.status != UserStatus.active.value:
        await store.hit(email_key, email_limit.seconds)
        reason = (
            "unknown_email"
            if user is None
            else "no_password"
            if user.password_hash is None
            else "wrong_password"
            if not verified
            else "user_disabled"
        )
        await record_audit(
            db,
            "auth.login_failed",
            client=client,
            details={"email_hash": hashed(address), "reason": reason},
        )
        await db.commit()  # the event survives the failed request
        raise InvalidCredentials()

    # 3. The right password: upgrade an old hash, then the agencies.
    assert user.password_hash is not None
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = await passwords.hash_password(password)
    memberships = await repository.usable_memberships(db, user.id)
    if not memberships:
        await record_audit(
            db,
            "auth.login_failed",
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            client=client,
            details={"reason": "no_agency_access"},
        )
        await db.commit()
        raise NoAgencyAccess()
    await store.reset(email_key)

    active: Membership | None = None
    if len(memberships) == 1 or memberships[0][0].last_active_at is not None:
        active = memberships[0][0]

    # 4. Rotate: the carried session (if any) ends, a new id starts.
    if carried_token:
        await repository.revoke_session(db, hash_token(carried_token), now)
    token = await _start_session(db, user.id, active.agency_id if active else None, client, now)
    user.last_login_at = now
    if active is not None:
        active.last_active_at = now
    await record_audit(
        db,
        "auth.login_succeeded",
        agency_id=active.agency_id if active else None,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        client=client,
        details={"method": "password"},
    )
    await db.commit()
    return token


async def logout(db: AsyncSession, signed_in: SignedIn, client: ClientInfo) -> None:
    """Revoke the session (replaying its cookie is then a 401) and commit."""
    await repository.revoke_session(db, signed_in.id_hash, utcnow())
    await record_audit(
        db,
        "auth.logout",
        agency_id=signed_in.active_agency_id,
        actor_user_id=signed_in.user_id,
        target_type="user",
        target_id=signed_in.user_id,
        client=client,
    )
    await db.commit()


# ── The signed-in user ────────────────────────────────────────
async def me(db: AsyncSession, signed_in: SignedIn, csrf_token: str) -> Me:
    """The ``Me`` resource: the user, their usable agencies, the active one and its scope."""
    rows = await repository.usable_memberships(db, signed_in.user_id)
    memberships = sorted(
        (
            MembershipResource(agency=AgencyRef(id=agency.id, name=agency.name), role=m.role)
            for m, agency in rows
        ),
        key=lambda item: (item.agency.name.lower(), str(item.agency.id)),
    )
    active = signed_in.membership
    return Me(
        user=MeUser(
            id=signed_in.user_id, email=signed_in.email, display_name=signed_in.display_name
        ),
        memberships=memberships,
        active_agency=AgencyRef(id=active.agency_id, name=active.agency_name) if active else None,
        role=active.role if active else None,
        client_scope=(
            ClientScope(
                all_clients=active.all_clients,
                client_ids=[] if active.all_clients else sorted(active.client_ids, key=str),
            )
            if active
            else None
        ),
        csrf_token=csrf_token,
    )


async def update_me(db: AsyncSession, signed_in: SignedIn, display_name: str) -> SignedIn:
    user = await repository.get_user(db, signed_in.user_id)
    if user is None:  # deleted between the session check and now
        raise NotFoundError("User not found")
    user.display_name = display_name
    await db.commit()
    return dataclasses.replace(signed_in, display_name=display_name)


async def change_password(
    db: AsyncSession,
    store: RateLimitStore,
    signed_in: SignedIn,
    *,
    current_password: str,
    new_password: str,
    client: ClientInfo,
) -> str:
    """Check the current password, set the new one, revoke **every** session of the user
    (other browsers are signed out) and start a new one here; returns its cookie value.

    Wrong current passwords count towards the per-email failure limit, like sign-in."""
    now = utcnow()
    _, email_limit = login_limits()
    email_key = email_limit.key(hashed(signed_in.email))
    failures = await store.peek(email_key)
    if failures.count >= email_limit.max_hits:
        await _rate_limited(db, store, email_limit, email_key, client)
        raise RateLimited(failures.retry_after)

    user = await repository.get_user(db, signed_in.user_id)
    if user is None:
        raise NotFoundError("User not found")
    if user.password_hash is None:
        await passwords.verify_dummy(current_password)
        verified = False
    else:
        verified = await passwords.verify_password(user.password_hash, current_password)
    if not verified:
        await store.hit(email_key, email_limit.seconds)
        raise InvalidInput("current_password", "Your current password is not correct")
    try:
        passwords.check_policy(new_password, email=user.email)
    except PasswordPolicyError as exc:
        raise InvalidInput("new_password", str(exc)) from exc

    user.password_hash = await passwords.hash_password(new_password)
    await repository.revoke_user_sessions(db, user.id, now)
    token = await _start_session(db, user.id, signed_in.active_agency_id, client, now)
    await record_audit(
        db,
        "auth.password_changed",
        agency_id=signed_in.active_agency_id,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        client=client,
    )
    await db.commit()
    await store.reset(email_key)
    return token


async def set_active_agency(
    db: AsyncSession, signed_in: SignedIn, agency_id: uuid.UUID, client: ClientInfo
) -> None:
    """Make ``agency_id`` the session's active agency. It must be an active membership in an
    active agency; anything else is :class:`NotFoundError` (a 404 that does not say whether the
    agency exists)."""
    rows = await repository.usable_memberships(db, signed_in.user_id)
    match = next((m for m, agency in rows if agency.id == agency_id), None)
    if match is None:
        raise NotFoundError("Agency not found")
    now = utcnow()
    await repository.set_active_agency(db, signed_in.id_hash, agency_id)
    match.last_active_at = now
    await record_audit(
        db,
        "auth.agency_switched",
        agency_id=agency_id,
        actor_user_id=signed_in.user_id,
        target_type="agency",
        target_id=agency_id,
        client=client,
        details={"from_agency_id": str(signed_in.active_agency_id)}
        if signed_in.active_agency_id
        else {},
    )
    await db.commit()


# ── The active agency ─────────────────────────────────────────
async def get_agency(db: AsyncSession, agency_id: uuid.UUID) -> AgencySummary | None:
    """The agency with this id, or ``None``."""
    agency = await repository.get_agency(db, agency_id)
    return None if agency is None else AgencySummary.model_validate(agency)


def agency_ids(db: Session) -> set[uuid.UUID]:
    """The id of every agency (synchronous: for the verification command)."""
    return repository.agency_ids_sync(db)


async def get_agency_v2(db: AsyncSession, principal: Principal) -> AgencyResource:
    agency = await repository.get_agency(db, principal.agency_id)
    if agency is None:
        raise NotFoundError("Agency not found")
    return AgencyResource.model_validate(agency)


async def update_agency_v2(
    db: AsyncSession, principal: Principal, name: str, client: ClientInfo
) -> AgencyResource:
    """Rename the principal's agency: owners only (``agency:manage``, spec §5.2; E2-06 moves the
    check into the permission matrix)."""
    if principal.role != Role.owner:
        raise PermissionDenied("You don't have permission to manage the agency")
    agency = await repository.get_agency(db, principal.agency_id)
    if agency is None:
        raise NotFoundError("Agency not found")
    old_name = agency.name
    if name != old_name:
        agency.name = name
        await record_audit(
            db,
            "agency.updated",
            agency_id=agency.id,
            actor_user_id=principal.user_id,
            target_type="agency",
            target_id=agency.id,
            client=client,
            details={"name": {"old": old_name, "new": name}},
        )
    await db.commit()
    await db.refresh(agency)
    return AgencyResource.model_validate(agency)


# ── Operator commands and the development seed ───────────────
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def check_email(email: str) -> str:
    """The normalised address, or :class:`OperatorError` if it does not look like one."""
    address = normalise_email(email)
    if len(address) > 320 or not _EMAIL.match(address):
        raise OperatorError(f"not an email address: {email!r}")
    return address


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return (slug or "agency")[: SLUG_MAX - 8]


async def create_agency(db: AsyncSession, name: str, *, slug: str | None = None) -> AgencySummary:
    """A new active agency; the slug is derived from the name (made unique) unless given."""
    name = name.strip()
    if not 1 <= len(name) <= 200:
        raise OperatorError("the agency name must be 1 to 200 characters")
    base = slug or slugify(name)
    candidate, n = base, 1
    while await repository.get_agency_by_slug(db, candidate) is not None:
        if slug is not None:
            raise OperatorError(f"an agency with the slug {slug!r} exists")
        n += 1
        candidate = f"{base}-{n}"
    agency = Agency(name=name, slug=candidate, status=AgencyStatus.active.value)
    db.add(agency)
    await db.flush()
    await db.refresh(agency)
    await record_audit(
        db,
        "agency.created",
        agency_id=agency.id,
        target_type="agency",
        target_id=agency.id,
        details={"by": "operator"},
    )
    return AgencySummary.model_validate(agency)


async def ensure_agency(db: AsyncSession, name: str, slug: str) -> AgencySummary:
    """The agency with this slug, created with this name if there is none."""
    agency = await repository.get_agency_by_slug(db, slug)
    if agency is not None:
        return AgencySummary.model_validate(agency)
    return await create_agency(db, name, slug=slug)


async def rename_agency(db: AsyncSession, agency_id: uuid.UUID, name: str) -> AgencySummary:
    agency = await repository.get_agency(db, agency_id)
    if agency is None:
        raise OperatorError(f"no agency {agency_id}")
    name = name.strip()
    if not 1 <= len(name) <= 200:
        raise OperatorError("the agency name must be 1 to 200 characters")
    if agency.name != name:
        old = agency.name
        agency.name = name
        await record_audit(
            db,
            "agency.updated",
            agency_id=agency.id,
            target_type="agency",
            target_id=agency.id,
            details={"name": {"old": old, "new": name}, "by": "operator"},
        )
        await db.flush()
        await db.refresh(agency)
    return AgencySummary.model_validate(agency)


async def user_exists(db: AsyncSession, email: str) -> bool:
    return await repository.get_user_by_email(db, check_email(email)) is not None


@dataclass(frozen=True, slots=True)
class EnsuredUser:
    id: uuid.UUID
    email: str
    created: bool


async def ensure_user(
    db: AsyncSession, email: str, *, display_name: str | None = None, password: str | None = None
) -> EnsuredUser:
    """The user with this email, created (active, email verified by the operator) if missing.
    A new user needs a ``password`` (checked against the policy); an existing user's password is
    never changed here."""
    address = check_email(email)
    user = await repository.get_user_by_email(db, address)
    if user is not None:
        return EnsuredUser(user.id, user.email, created=False)
    if password is None:
        raise OperatorError(f"{address} has no account yet: a password is needed to create it")
    try:
        passwords.check_policy(password, email=address)
    except PasswordPolicyError as exc:
        raise OperatorError(f"password: {exc}") from exc
    user = User(
        email=address,
        email_verified_at=utcnow(),
        password_hash=await passwords.hash_password(password),
        display_name=(display_name or address.split("@")[0]).strip()[:200],
        status=UserStatus.active.value,
    )
    db.add(user)
    await db.flush()
    return EnsuredUser(user.id, user.email, created=True)


async def ensure_membership(
    db: AsyncSession, agency_id: uuid.UUID, user_id: uuid.UUID, role: Role
) -> bool:
    """Give the user ``role`` in the agency (active, every client). Returns whether a membership
    was created; an existing one gets the role and is reactivated."""
    membership = await repository.get_membership(db, agency_id, user_id)
    created = membership is None
    if membership is None:
        membership = Membership(agency_id=agency_id, user_id=user_id)
        db.add(membership)
    old_role = membership.role
    membership.role = role.value
    membership.all_clients = True
    membership.status = MembershipStatus.active.value
    membership.deactivated_at = None
    await db.flush()
    if created or old_role != role.value:
        await record_audit(
            db,
            "team.member_added" if created else "team.member_role_changed",
            agency_id=agency_id,
            target_type="user",
            target_id=user_id,
            details={"role": role.value, "by": "operator"},
        )
    return created
