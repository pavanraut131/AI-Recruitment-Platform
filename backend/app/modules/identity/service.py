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

**Team, invitations and password reset (E2-07, spec §4.3, §5.2).**

- *Who may do what* is the permission matrix (``app.modules.identity.permissions``, E2-06):
  the routes declare ``team:read`` / ``team:manage``, every team use case checks it again
  (:func:`check_permission`), and :func:`can_manage` adds ``team:manage_privileged`` for owners
  and admins (as the target's old or new role, or an invitation's). Guard rails: nobody changes their own role or
  deactivates themselves (403), and the agency always keeps an active owner (409; the active
  owners are row-locked first, so two owners demoting each other at once cannot both succeed).
- *Emailed tokens* (invitations: 7 days; reset links: 30 minutes, single use) are 256-bit
  random values in the link's URL fragment; only their SHA-256 is stored. Expired, revoked,
  replaced and used tokens are all :class:`TokenGone` (410). Their rows are row-locked when used,
  so a token is used once even by two requests at the same moment.
- *No enumeration.* :func:`request_password_reset` answers the same way, after the same rate
  limits and the same audit write, whether or not the address has an account; the email (when
  there is one) goes out from a Celery task after the commit (``identity.tasks``).
- *Accepting an invitation and resetting a password sign the user in or out like a password
  change:* accepting rotates into a new session with the agency active; a reset revokes every
  session of the user.
- The team use cases take the request's session bound to the principal's agency (the composed
  ``/team`` endpoints, ``app.api.v2.team``, also check client ids through hiring there) and
  filter every query by ``principal.agency_id`` themselves: identity's tables are global.

**Sign in with Google and Microsoft (E2-08, spec §4.3).** :func:`start_oidc_login` and
:func:`complete_oidc_login` run the OpenID Connect flow; the protocol (state cookie, PKCE, the
code exchange and the ID-token checks) is ``app.modules.identity.oidc``. An identity is found by
``(provider, issuer, subject)`` in ``oidc_accounts`` and linked to a user only through an
invitation token or an address the provider vouches for (:func:`_oidc_sign_in`), never an
unverified email. Every failure is an :class:`OidcFailed` (the login page's ``error``), audited.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import ipaddress
import re
import secrets
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.identity import emails, oidc, passwords, repository, tasks
from app.modules.identity.models import (
    Agency,
    AgencyStatus,
    AuditEvent,
    Invitation,
    Membership,
    MembershipStatus,
    OidcAccount,
    PasswordResetToken,
    User,
    UserSession,
    UserStatus,
)
from app.modules.identity.passwords import PasswordPolicyError
from app.modules.identity.permissions import (
    Permission,
    denied_detail,
    has_permission,
    permissions_of,
)
from app.modules.identity.schemas import (
    ActiveMembership,
    AgencySummary,
    ClientAccess,
    InvitationStatus,
    MemberStatus,
    OidcLoginError,
    OidcProvider,
    Principal,
    Role,
    SignedIn,
    TeamInvitation,
    TeamMember,
    TeamUser,
)
from app.modules.identity.schemas_v2 import (
    Agency as AgencyResource,
)
from app.modules.identity.schemas_v2 import (
    AgencyRef,
    ClientScope,
    InvitationPreview,
    Me,
    MeUser,
)
from app.modules.identity.schemas_v2 import (
    Membership as MembershipResource,
)
from app.platform.config import settings
from app.platform.email import EmailMessage
from app.platform.ratelimit import Limit, RateLimitStore, hashed

TOUCH_INTERVAL: Final = timedelta(minutes=5)
"""``last_seen_at`` / ``idle_expires_at`` are written at most this often per session."""

USER_AGENT_MAX: Final = 500
SLUG_MAX: Final = 100
DEFAULT_AGENCY_SLUG: Final = "default"
"""The slug of the "Default agency", the one the E2-01 migration made of the data that existed
before E2 (EPIC-02 spec §7). An ordinary agency since E2-12 (its fixed id is gone with
``/api/v1``); the commands that work on it look it up by this slug: ``cli create-owner``, the
development seed and the tenancy backfill."""


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


class ConflictError(Exception):
    """The request conflicts with the current state (409): already a member, the last active
    owner, an account that must sign in first."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class TokenGone(Exception):
    """An emailed token that is unknown, expired, revoked, replaced or already used (410)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


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
        permissions=permissions_of(active.role) if active else [],
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
    (other browsers are signed out) and start a new one here; returns its cookie value. A
    "your password was changed" email is queued after the commit (E2-07).

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
    notice = emails.password_changed(to=user.email, display_name=user.display_name)
    await db.commit()
    await store.reset(email_key)
    tasks.enqueue_email(notice)  # spec §10: a password change is notified by email (E2-07)
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


async def get_agency_by_slug(db: AsyncSession, slug: str) -> AgencySummary | None:
    """The agency with this slug, or ``None``."""
    agency = await repository.get_agency_by_slug(db, slug)
    return None if agency is None else AgencySummary.model_validate(agency)


def agency_id_by_slug(db: Session, slug: str) -> uuid.UUID | None:
    """The id of the agency with this slug, or ``None`` (synchronous: for the operator commands
    and the seed; ``agencies`` is global, so any session reads it)."""
    return repository.agency_id_by_slug_sync(db, slug)


async def get_agency_v2(db: AsyncSession, principal: Principal) -> AgencyResource:
    agency = await repository.get_agency(db, principal.agency_id)
    if agency is None:
        raise NotFoundError("Agency not found")
    return AgencyResource.model_validate(agency)


async def update_agency_v2(
    db: AsyncSession, principal: Principal, name: str, client: ClientInfo
) -> AgencyResource:
    """Rename the principal's agency. The route requires ``agency:manage`` (spec §5.2)."""
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


# ── Team permissions (spec §5.2) ──────────────────────────────
# The team rows of the permission matrix (app.modules.identity.permissions, E2-06). The routes
# declare team:read / team:manage (require_permission); the use cases check them again, and
# decide team:manage_privileged, which depends on the roles involved (can_manage).
PRIVILEGED_ROLES: Final = frozenset({Role.owner, Role.admin})
"""Roles only ``team:manage_privileged`` may grant, change or remove (and that always have
every client)."""


def can_manage(principal: Principal, target_role: Role) -> bool:
    """Whether the principal may invite, change, deactivate or reactivate someone with
    ``target_role``, or resend and revoke their invitation: ``team:manage`` for recruiters and
    viewers, ``team:manage_privileged`` for owners and admins."""
    needed = (
        Permission.team_manage_privileged
        if target_role in PRIVILEGED_ROLES
        else Permission.team_manage
    )
    return has_permission(principal.role, needed)


def check_permission(principal: Principal, permission: Permission) -> None:
    """:class:`PermissionDenied` (403) unless the principal's role has ``permission``."""
    if not has_permission(principal.role, permission):
        raise PermissionDenied(denied_detail(permission))


def _require_can_manage(principal: Principal, *roles: Role) -> None:
    for role in roles:
        if not can_manage(principal, role):
            raise PermissionDenied(denied_detail(Permission.team_manage_privileged))


# ── Emailed tokens (E2-07) ────────────────────────────────────
INVITATION_GONE: Final = (
    "This invitation has expired or is no longer valid. Ask for a new invitation."
)
RESET_GONE: Final = "This link has expired or was already used. Ask for a new one."
LAST_OWNER: Final = "The agency needs at least one active owner"

# Invitee addresses: a plain local part (RFC 5322 dot-atom) and a dotted domain. Stricter than
# the operator commands' check: the address goes into an email header.
_INVITEE_EMAIL = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$"
)


def new_token() -> str:
    """32 random bytes (256 bits), URL-safe: an invitation or reset link's token."""
    return secrets.token_urlsafe(32)


def reset_limits() -> tuple[Limit, Limit]:
    """``(per IP, per email)`` password-reset requests, from the settings."""
    window = settings.PASSWORD_RESET_RATE_LIMIT_WINDOW
    return (
        Limit("reset_ip", settings.PASSWORD_RESET_MAX_PER_IP, window),
        Limit("reset_email", settings.PASSWORD_RESET_MAX_PER_EMAIL, window),
    )


def token_limit() -> Limit:
    """Attempts per IP at the endpoints that take an emailed token."""
    return Limit("token_ip", settings.TOKEN_MAX_ATTEMPTS_PER_IP, settings.TOKEN_RATE_LIMIT_WINDOW)


def invitation_limit() -> Limit:
    """Invitation emails (new and resent) per agency per day."""
    return Limit(
        "invitations_agency", settings.INVITATIONS_MAX_PER_AGENCY_PER_DAY, timedelta(days=1)
    )


async def _count_or_refuse(
    db: AsyncSession, store: RateLimitStore, limit: Limit, identifier: str, client: ClientInfo
) -> None:
    """Count one hit of ``limit`` for ``identifier``; :class:`RateLimited` once over it."""
    key = limit.key(hashed(identifier))
    window = await store.hit(key, limit.seconds)
    if window.count > limit.max_hits:
        await _rate_limited(db, store, limit, key, client)
        raise RateLimited(window.retry_after)


def _expired(moment: datetime, now: datetime) -> bool:
    return now >= moment


# ── Team: members (E2-07) ─────────────────────────────────────
def _team_user(user: User) -> TeamUser:
    return TeamUser(id=user.id, email=user.email, display_name=user.display_name)


def _team_member(membership: Membership, user: User, client_ids: Sequence[uuid.UUID]) -> TeamMember:
    return TeamMember(
        id=membership.id,
        user=_team_user(user),
        role=Role(membership.role),
        all_clients=membership.all_clients,
        client_ids=() if membership.all_clients else tuple(sorted(client_ids, key=str)),
        status=MemberStatus(membership.status),
        last_active_at=membership.last_active_at,
        joined_at=membership.created_at,
        deactivated_at=membership.deactivated_at,
    )


def client_access(
    role: Role,
    all_clients: bool | None,
    client_ids: Sequence[uuid.UUID] | None,
    current: ClientAccess | None = None,
) -> ClientAccess:
    """The client access a membership or invitation gets: what the request says
    (``None``: unchanged from ``current``). Owners and admins always have every client; a
    recruiter or viewer limited to some clients needs at least one. :class:`InvalidInput`
    (422, naming the field) otherwise. The ids themselves are checked by the caller (hiring:
    active clients of the agency)."""
    current = current or ClientAccess(all_clients=True)
    if role in PRIVILEGED_ROLES:
        if all_clients is False:
            raise InvalidInput("all_clients", "Owners and admins always have every client")
        if client_ids:
            raise InvalidInput("client_ids", "Owners and admins always have every client")
        return ClientAccess(all_clients=True)
    every = current.all_clients if all_clients is None else all_clients
    ids = current.client_ids if client_ids is None else tuple(dict.fromkeys(client_ids))
    if every:
        if client_ids:
            raise InvalidInput("client_ids", "Leave the client list empty for every client")
        return ClientAccess(all_clients=True)
    if not ids:
        raise InvalidInput("client_ids", "Choose at least one client")
    return ClientAccess(all_clients=False, client_ids=tuple(sorted(ids, key=str)))


async def list_members(
    db: AsyncSession, principal: Principal, offset: int, limit: int
) -> tuple[list[TeamMember], int]:
    """A page of the agency's members (deactivated ones included) and the total
    (``team:read``)."""
    check_permission(principal, Permission.team_read)
    rows, total = await repository.list_members(db, principal.agency_id, offset, limit)
    return [_team_member(m, user, ids) for m, user, ids in rows], total


async def _member_for_change(
    db: AsyncSession, principal: Principal, membership_id: uuid.UUID, what: str
) -> tuple[Membership, User]:
    """The agency's membership, row-locked (404 for anything else), that the principal is not
    (403: nobody changes their own access)."""
    row = await repository.get_member(db, principal.agency_id, membership_id, lock=True)
    if row is None:
        raise NotFoundError("Member not found")
    membership, user = row
    if principal.user_id is not None and membership.user_id == principal.user_id:
        raise PermissionDenied(f"You can't {what} yourself")
    return membership, user


async def _keep_an_owner(db: AsyncSession, agency_id: uuid.UUID, losing: uuid.UUID) -> None:
    """:class:`ConflictError` if membership ``losing`` is the agency's last active owner.
    Expects :func:`repository.lock_active_owners` to have run in this transaction."""
    owners = await repository.lock_active_owners(db, agency_id)
    if losing in owners and len(owners) == 1:
        raise ConflictError(LAST_OWNER)


async def update_member(
    db: AsyncSession,
    principal: Principal,
    membership_id: uuid.UUID,
    *,
    role: Role | None,
    all_clients: bool | None,
    client_ids: Sequence[uuid.UUID] | None,
    client: ClientInfo,
) -> TeamMember:
    """Change a member's role and client access (``team:manage``; owners and admins, as the
    old or the new role, need ``team:manage_privileged``). Audited as
    ``team.member_role_changed`` with the old and new values; commits."""
    check_permission(principal, Permission.team_manage)
    # Owners first, in id order, then the member: the same lock order in every request.
    await repository.lock_active_owners(db, principal.agency_id)
    membership, user = await _member_for_change(
        db, principal, membership_id, "change the role or client access of"
    )
    old_role = Role(membership.role)
    new_role = role or old_role
    _require_can_manage(principal, old_role, new_role)
    old_ids = tuple(await repository.membership_client_ids(db, membership.id))
    old = ClientAccess(membership.all_clients, () if membership.all_clients else old_ids)
    new = client_access(new_role, all_clients, client_ids, current=old)
    if (
        old_role == Role.owner
        and new_role != Role.owner
        and membership.status == MembershipStatus.active.value
    ):
        await _keep_an_owner(db, principal.agency_id, membership.id)

    if (old_role, old) != (new_role, new):
        membership.role = new_role.value
        membership.all_clients = new.all_clients
        await db.flush()
        await repository.set_membership_clients(db, membership.id, new.client_ids)
        await record_audit(
            db,
            "team.member_role_changed",
            agency_id=principal.agency_id,
            actor_user_id=principal.user_id,
            target_type="membership",
            target_id=membership.id,
            client=client,
            details={
                "user_id": str(user.id),
                "role": {"old": old_role.value, "new": new_role.value},
                "all_clients": {"old": old.all_clients, "new": new.all_clients},
                "client_ids": {
                    "old": [str(i) for i in old.client_ids],
                    "new": [str(i) for i in new.client_ids],
                },
            },
        )
    await db.commit()
    await db.refresh(membership)
    return _team_member(membership, user, new.client_ids)


async def set_member_status(
    db: AsyncSession,
    principal: Principal,
    membership_id: uuid.UUID,
    *,
    active: bool,
    client: ClientInfo,
) -> TeamMember:
    """Deactivate (``active=False``: their access to the agency ends with their next request)
    or reactivate a member (``team:manage``; owners and admins need
    ``team:manage_privileged``). The last active owner cannot be deactivated (409). Doing it
    twice changes nothing. Audited as ``team.member_deactivated`` / ``team.member_reactivated``;
    commits."""
    check_permission(principal, Permission.team_manage)
    if not active:
        await repository.lock_active_owners(db, principal.agency_id)
    membership, user = await _member_for_change(
        db, principal, membership_id, "deactivate" if not active else "reactivate"
    )
    role = Role(membership.role)
    _require_can_manage(principal, role)
    status = MembershipStatus.active if active else MembershipStatus.deactivated
    if membership.status != status.value:
        if not active and role == Role.owner:
            await _keep_an_owner(db, principal.agency_id, membership.id)
        membership.status = status.value
        membership.deactivated_at = None if active else utcnow()
        await record_audit(
            db,
            "team.member_reactivated" if active else "team.member_deactivated",
            agency_id=principal.agency_id,
            actor_user_id=principal.user_id,
            target_type="membership",
            target_id=membership.id,
            client=client,
            details={"user_id": str(user.id), "role": role.value},
        )
    client_ids = await repository.membership_client_ids(db, membership.id)
    await db.commit()
    await db.refresh(membership)
    return _team_member(membership, user, client_ids)


# ── Team: invitations (E2-07) ─────────────────────────────────
def _invitee_email(email: str) -> str:
    address = normalise_email(email)
    if len(address) > 320 or not _INVITEE_EMAIL.match(address):
        raise InvalidInput("email", "Enter a valid email address")
    return address


def _team_invitation(invitation: Invitation, inviter: User | None, now: datetime) -> TeamInvitation:
    return TeamInvitation(
        id=invitation.id,
        email=invitation.email,
        role=Role(invitation.role),
        all_clients=invitation.all_clients,
        client_ids=tuple(sorted(invitation.client_ids, key=str)),
        invited_by=_team_user(inviter) if inviter is not None else None,
        status=InvitationStatus.expired
        if _expired(invitation.expires_at, now)
        else InvitationStatus.pending,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


async def list_invitations(
    db: AsyncSession, principal: Principal, offset: int, limit: int
) -> tuple[list[TeamInvitation], int]:
    """A page of the agency's open invitations (pending and expired), newest first, and the
    total (``team:read``)."""
    check_permission(principal, Permission.team_read)
    rows, total = await repository.list_open_invitations(db, principal.agency_id, offset, limit)
    now = utcnow()
    return [_team_invitation(invitation, inviter, now) for invitation, inviter in rows], total


async def _invitation_quota(
    db: AsyncSession, store: RateLimitStore, agency_id: uuid.UUID, client: ClientInfo
) -> str:
    """:class:`RateLimited` when the agency has sent its invitations for the day; returns the
    counter's key (hit once the email is queued)."""
    limit = invitation_limit()
    key = limit.key(hashed(str(agency_id)))
    used = await store.peek(key)
    if used.count >= limit.max_hits:
        await _rate_limited(db, store, limit, key, client)
        raise RateLimited(used.retry_after)
    return key


async def _invitation_email(
    db: AsyncSession, invitation: Invitation, inviter: User | None, token: str
) -> EmailMessage:
    agency = await repository.get_agency(db, invitation.agency_id)
    assert agency is not None  # a foreign key
    return emails.invitation(
        to=invitation.email,
        agency_name=agency.name,
        inviter_name=inviter.display_name if inviter is not None else None,
        role=Role(invitation.role),
        token=token,
        expires_at=invitation.expires_at,
    )


async def _send(store: RateLimitStore, quota_key: str, message: EmailMessage) -> None:
    await store.hit(quota_key, invitation_limit().seconds)
    tasks.enqueue_email(message)


async def create_invitation(
    db: AsyncSession,
    store: RateLimitStore,
    principal: Principal,
    *,
    email: str,
    role: Role,
    all_clients: bool,
    client_ids: Sequence[uuid.UUID],
    client: ClientInfo,
) -> TeamInvitation:
    """Invite ``email`` to the principal's agency (``team:manage``; owners and admins need
    ``team:manage_privileged``) and email the link (``{FRONTEND_URL}/accept-invite#token=…``,
    valid ``INVITATION_TTL``).

    An active member's address is :class:`ConflictError` (409). An open invitation for the
    same address is revoked and replaced (its link stops working). At most
    ``INVITATIONS_MAX_PER_AGENCY_PER_DAY`` emails a day (:class:`RateLimited`). The client ids
    must have been checked by the caller (hiring). Audited ``team.invitation_created``;
    commits, then queues the email."""
    check_permission(principal, Permission.team_manage)
    _require_can_manage(principal, role)
    address = _invitee_email(email)
    access = client_access(role, all_clients, client_ids)
    quota_key = await _invitation_quota(db, store, principal.agency_id, client)
    if await repository.is_active_member(db, principal.agency_id, address):
        raise ConflictError(f"{address} is already a member of this agency")

    now = utcnow()
    replaced = await repository.open_invitation_for_email(db, principal.agency_id, address)
    if replaced is not None:
        _require_can_manage(principal, Role(replaced.role))
        replaced.revoked_at = now
        await db.flush()  # before the new one: one open invitation per address

    token = new_token()
    invitation = Invitation(
        agency_id=principal.agency_id,
        email=address,
        role=role.value,
        all_clients=access.all_clients,
        client_ids=list(access.client_ids),
        token_hash=hash_token(token),
        invited_by_user_id=principal.user_id,
        expires_at=now + settings.INVITATION_TTL,
        created_at=now,
    )
    try:
        async with db.begin_nested():
            db.add(invitation)
    except IntegrityError as exc:  # the same address invited by a parallel request
        raise ConflictError(f"An invitation for {address} was just sent") from exc
    await record_audit(
        db,
        "team.invitation_created",
        agency_id=principal.agency_id,
        actor_user_id=principal.user_id,
        target_type="invitation",
        target_id=invitation.id,
        client=client,
        details={
            "email": address,
            "role": role.value,
            "all_clients": access.all_clients,
            "client_ids": [str(i) for i in access.client_ids],
            **({"replaced_invitation_id": str(replaced.id)} if replaced is not None else {}),
        },
    )
    inviter = await repository.get_user(db, principal.user_id) if principal.user_id else None
    message = await _invitation_email(db, invitation, inviter, token)
    await db.commit()
    await _send(store, quota_key, message)
    return _team_invitation(invitation, inviter, now)


async def _open_invitation_to_manage(
    db: AsyncSession, principal: Principal, invitation_id: uuid.UUID
) -> Invitation:
    invitation = await repository.get_open_invitation(db, principal.agency_id, invitation_id)
    if invitation is None:
        raise NotFoundError("Invitation not found")
    _require_can_manage(principal, Role(invitation.role))
    return invitation


async def resend_invitation(
    db: AsyncSession,
    store: RateLimitStore,
    principal: Principal,
    invitation_id: uuid.UUID,
    *,
    client: ClientInfo,
) -> TeamInvitation:
    """Email an open invitation again with a **new** token and a new expiry (the old link stops
    working); an expired one is renewed this way. Same permissions, conflict and daily limit as
    :func:`create_invitation`. Audited ``team.invitation_resent``; commits, then queues."""
    check_permission(principal, Permission.team_manage)
    invitation = await _open_invitation_to_manage(db, principal, invitation_id)
    quota_key = await _invitation_quota(db, store, principal.agency_id, client)
    if await repository.is_active_member(db, principal.agency_id, invitation.email):
        raise ConflictError(f"{invitation.email} is already a member of this agency")
    now = utcnow()
    token = new_token()
    invitation.token_hash = hash_token(token)
    invitation.expires_at = now + settings.INVITATION_TTL
    await record_audit(
        db,
        "team.invitation_resent",
        agency_id=principal.agency_id,
        actor_user_id=principal.user_id,
        target_type="invitation",
        target_id=invitation.id,
        client=client,
        details={"email": invitation.email, "role": invitation.role},
    )
    # The email names the original inviter (as the list does), or whoever resends it when that
    # account is gone.
    inviter = (
        await repository.get_user(db, invitation.invited_by_user_id)
        if invitation.invited_by_user_id
        else None
    )
    sender = await repository.get_user(db, principal.user_id) if principal.user_id else None
    message = await _invitation_email(db, invitation, inviter or sender, token)
    await db.commit()
    await _send(store, quota_key, message)
    return _team_invitation(invitation, inviter, now)


async def revoke_invitation(
    db: AsyncSession, principal: Principal, invitation_id: uuid.UUID, *, client: ClientInfo
) -> None:
    """Revoke an open invitation (its link stops working). Audited ``team.invitation_revoked``;
    commits."""
    check_permission(principal, Permission.team_manage)
    invitation = await _open_invitation_to_manage(db, principal, invitation_id)
    invitation.revoked_at = utcnow()
    await record_audit(
        db,
        "team.invitation_revoked",
        agency_id=principal.agency_id,
        actor_user_id=principal.user_id,
        target_type="invitation",
        target_id=invitation.id,
        client=client,
        details={"email": invitation.email, "role": invitation.role},
    )
    await db.commit()


# ── Accepting an invitation (E2-07) ───────────────────────────
async def _usable_invitation(
    db: AsyncSession, token: str, now: datetime, *, lock: bool
) -> tuple[Invitation, Agency]:
    """The open, unexpired invitation of an active agency this token belongs to; :class:`TokenGone`
    (410) for anything else."""
    return await _usable_invitation_by_hash(db, hash_token(token), now, lock=lock)


async def _usable_invitation_by_hash(
    db: AsyncSession, token_hash: bytes, now: datetime, *, lock: bool
) -> tuple[Invitation, Agency]:
    """:func:`_usable_invitation` by the token's SHA-256 (what an OIDC flow carries, E2-08)."""
    invitation = await repository.invitation_by_token(db, token_hash, lock=lock)
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or _expired(invitation.expires_at, now)
    ):
        raise TokenGone(INVITATION_GONE)
    agency = await repository.get_agency(db, invitation.agency_id)
    if agency is None or agency.status != AgencyStatus.active.value:
        raise TokenGone(INVITATION_GONE)
    return invitation, agency


async def preview_invitation(
    db: AsyncSession, store: RateLimitStore, token: str, *, client: ClientInfo
) -> InvitationPreview:
    """What the invitation offers: the agency, inviter, role, address, and whether an account
    with that address exists (then the invitee signs in before accepting). Only the holder of
    the token, who controls the invited inbox, learns it."""
    await _count_or_refuse(db, store, token_limit(), client.ip or "unknown", client)
    invitation, agency = await _usable_invitation(db, token, utcnow(), lock=False)
    inviter = (
        await repository.get_user(db, invitation.invited_by_user_id)
        if invitation.invited_by_user_id
        else None
    )
    account = await repository.get_user_by_email(db, invitation.email)
    return InvitationPreview(
        agency=AgencyRef(id=agency.id, name=agency.name),
        invited_by=inviter.display_name if inviter is not None else None,
        email=invitation.email,
        role=Role(invitation.role),
        account_exists=account is not None,
        expires_at=invitation.expires_at,
    )


async def _new_invitee(
    db: AsyncSession, email: str, display_name: str | None, password: str | None, now: datetime
) -> User:
    if await repository.get_user_by_email(db, email) is not None:
        raise ConflictError("An account with this email exists. Sign in, then accept.")
    if not display_name:
        raise InvalidInput("display_name", "Enter your name")
    if not password:
        raise InvalidInput("password", "Choose a password")
    try:
        passwords.check_policy(password, email=email)
    except PasswordPolicyError as exc:
        raise InvalidInput("password", str(exc)) from exc
    user = User(
        email=email,
        email_verified_at=now,  # the token proves the inbox
        password_hash=await passwords.hash_password(password),
        display_name=display_name,
        status=UserStatus.active.value,
    )
    try:
        async with db.begin_nested():
            db.add(user)
    except IntegrityError as exc:  # created by a parallel request
        raise ConflictError("An account with this email exists. Sign in, then accept.") from exc
    return user


async def _join_agency(
    db: AsyncSession, invitation: Invitation, user: User, now: datetime
) -> Membership:
    """Accept ``invitation`` for ``user`` (who proved the invited inbox): the membership with the
    invitation's role and client access is created, or a deactivated one reactivated with them
    (:class:`ConflictError` for an active one); the invitation is marked accepted and the email
    verified. Shared by password sign-up / signed-in acceptance and the OIDC flow (E2-08)."""
    membership = await repository.get_membership(db, invitation.agency_id, user.id)
    if membership is not None and membership.status == MembershipStatus.active.value:
        raise ConflictError("You are already a member of this agency")
    if membership is None:
        membership = Membership(agency_id=invitation.agency_id, user_id=user.id)
        db.add(membership)
    membership.role = invitation.role
    membership.all_clients = invitation.all_clients
    membership.status = MembershipStatus.active.value
    membership.deactivated_at = None
    membership.invited_by_user_id = invitation.invited_by_user_id
    membership.last_active_at = now
    await db.flush()
    await repository.set_membership_clients(
        db, membership.id, [] if invitation.all_clients else list(invitation.client_ids)
    )

    invitation.accepted_at = now
    invitation.accepted_by_user_id = user.id
    if user.email_verified_at is None:
        user.email_verified_at = now
    return membership


async def accept_invitation(
    db: AsyncSession,
    store: RateLimitStore,
    *,
    token: str,
    signed_in: SignedIn | None,
    carried_token: str | None,
    display_name: str | None,
    password: str | None,
    client: ClientInfo,
) -> str:
    """Accept an invitation; returns the cookie value of a **new** session in its agency.

    - **Signed in** (``signed_in``): the session's email must be the invited one (403
      otherwise); the user gets the membership.
    - **Not signed in:** a new account (``display_name`` and ``password`` required; email
      verified, since the token proves the inbox). An existing account for the address is a
      409: sign in first.

    The membership (role, client access, ``invited_by``) is created, or a deactivated one
    reactivated with the invitation's access; an active one is a 409. The invitation is marked
    accepted, the session the request carried is revoked and a new one starts with the agency
    active (rotation). Audited ``team.invitation_accepted``; commits."""
    await _count_or_refuse(db, store, token_limit(), client.ip or "unknown", client)
    now = utcnow()
    invitation, agency = await _usable_invitation(db, token, now, lock=True)

    if signed_in is not None:
        if signed_in.email != invitation.email:
            raise PermissionDenied(
                "This invitation is for another email address. Sign out, or ask for an "
                "invitation to your address."
            )
        found = await repository.get_user(db, signed_in.user_id)
        if found is None:
            raise NotFoundError("User not found")
        user, created = found, False
    else:
        user, created = await _new_invitee(db, invitation.email, display_name, password, now), True

    await _join_agency(db, invitation, user, now)
    user.last_login_at = now
    if carried_token:
        await repository.revoke_session(db, hash_token(carried_token), now)
    session_token = await _start_session(db, user.id, agency.id, client, now)
    await record_audit(
        db,
        "team.invitation_accepted",
        agency_id=agency.id,
        actor_user_id=user.id,
        target_type="invitation",
        target_id=invitation.id,
        client=client,
        details={"role": invitation.role, "new_account": created},
    )
    await db.commit()
    return session_token


# ── Password reset (E2-07) ────────────────────────────────────
RESET_REQUESTED: Final = (
    "If an account exists for this address, we've sent it a link to choose a new password."
)


async def request_password_reset(
    db: AsyncSession, store: RateLimitStore, *, email: str, client: ClientInfo
) -> None:
    """Email a reset link (``{FRONTEND_URL}/reset-password#token=…``, single use, valid
    ``PASSWORD_RESET_TTL``) if an active user has this address; older unused links of the user
    stop working. Throttled per IP and per address (:class:`RateLimited`), counted whether or not
    the account exists.

    The caller answers the same way in every case (202, :data:`RESET_REQUESTED`): this does the
    same limits, lookup and audit write (``auth.password_reset_requested``, with a keyed hash of
    the address) either way, and the email goes out from a Celery task after the commit."""
    address = normalise_email(email)
    ip_limit, email_limit = reset_limits()
    await _count_or_refuse(db, store, ip_limit, client.ip or "unknown", client)
    await _count_or_refuse(db, store, email_limit, address, client)

    user = await repository.get_user_by_email(db, address) if address else None
    usable = user is not None and user.status == UserStatus.active.value
    details: dict[str, Any] = {"email_hash": hashed(address)}
    message: EmailMessage | None = None
    if user is not None and usable:
        await repository.delete_unused_reset_tokens(db, user.id)
        token = new_token()
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=utcnow() + settings.PASSWORD_RESET_TTL,
            )
        )
        message = emails.password_reset(to=user.email, display_name=user.display_name, token=token)
    else:
        details["reason"] = "unknown_email" if user is None else "user_disabled"
    await record_audit(
        db,
        "auth.password_reset_requested",
        target_type="user" if usable and user is not None else None,
        target_id=user.id if usable and user is not None else None,
        client=client,
        details=details,
    )
    await db.commit()
    if message is not None:
        tasks.enqueue_email(message)


async def confirm_password_reset(
    db: AsyncSession,
    store: RateLimitStore,
    *,
    token: str,
    new_password: str,
    client: ClientInfo,
) -> None:
    """Set a new password with a reset link's token (:class:`TokenGone` unless it is unused and
    unexpired, and its user active). A user without a password (OIDC only) gets one this way.
    The token and the user's other links are used up, **every** session of the user is revoked,
    and a "your password was changed" email is queued. Audited ``auth.password_reset_completed``;
    commits. A password the policy refuses is :class:`InvalidInput` and leaves the link usable."""
    await _count_or_refuse(db, store, token_limit(), client.ip or "unknown", client)
    now = utcnow()
    row = await repository.reset_token_by_hash(db, hash_token(token))
    if row is None or row.used_at is not None or _expired(row.expires_at, now):
        raise TokenGone(RESET_GONE)
    user = await repository.get_user(db, row.user_id)
    if user is None or user.status != UserStatus.active.value:
        raise TokenGone(RESET_GONE)
    try:
        passwords.check_policy(new_password, email=user.email)
    except PasswordPolicyError as exc:
        raise InvalidInput("new_password", str(exc)) from exc

    had_password = user.password_hash is not None
    user.password_hash = await passwords.hash_password(new_password)
    if user.email_verified_at is None:
        user.email_verified_at = now  # the token proves the inbox
    await repository.use_reset_tokens(db, user.id, now)
    revoked = await repository.revoke_user_sessions(db, user.id, now)
    await record_audit(
        db,
        "auth.password_reset_completed",
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        client=client,
        details={"sessions_revoked": revoked, "password_added": not had_password},
    )
    notice = emails.password_changed(to=user.email, display_name=user.display_name)
    await db.commit()
    tasks.enqueue_email(notice)


# ── Sign in with Google and Microsoft (E2-08) ─────────────────
OIDC_STATE_TTL: Final = oidc.STATE_TTL
PROVIDER_UNAVAILABLE: Final = "{} sign-in is unavailable right now. Try again in a few minutes."


class ProviderUnavailable(Exception):
    """The provider's discovery document cannot be fetched (502)."""

    def __init__(self, provider_name: str) -> None:
        self.detail = PROVIDER_UNAVAILABLE.format(provider_name)
        super().__init__(self.detail)


@dataclass(frozen=True, slots=True)
class OidcStarted:
    """Where to send the browser, and the value of the ``__Host-hf_oidc`` cookie to set."""

    authorization_url: str
    sealed_flow: str


@dataclass(frozen=True, slots=True)
class OidcSignedIn:
    """Signed in: the new session's cookie value and the frontend path to land on."""

    token: str
    next: str


@dataclass(frozen=True, slots=True)
class OidcFailed:
    """Nobody signed in, and why (the login page's ``error``)."""

    error: OidcLoginError


def oidc_limit(step: str) -> Limit:
    """Starts (``step="start"``) or callbacks (``"callback"``) of the OIDC flow per client IP."""
    return Limit(
        f"oidc_{step}_ip", settings.OIDC_MAX_ATTEMPTS_PER_IP, settings.OIDC_RATE_LIMIT_WINDOW
    )


def oidc_providers() -> list[tuple[OidcProvider, str]]:
    """``(provider, display name)`` of each configured provider, Google first."""
    return [(config.provider, config.name) for config in oidc.configured_providers()]


def _oidc_config(provider: OidcProvider) -> oidc.ProviderConfig:
    config = oidc.provider_config(provider)
    if config is None:  # hidden: as if it did not exist
        raise NotFoundError("Not Found")
    return config


async def start_oidc_login(
    db: AsyncSession,
    store: RateLimitStore,
    provider: OidcProvider,
    *,
    next_path: str | None,
    invitation_token: str | None,
    client: ClientInfo,
) -> OidcStarted:
    """Start signing in with ``provider`` (spec §4.3 steps 1-2): a new ``state``, ``nonce`` and
    PKCE verifier, sealed with ``next`` (a frontend path; anything else becomes ``/``) and the
    invitation token's hash into the cookie value, and the provider's authorization URL.

    :class:`NotFoundError` for a provider that is not configured, :class:`RateLimited` over
    ``OIDC_MAX_ATTEMPTS_PER_IP``, :class:`TokenGone` for an invitation that can no longer be
    accepted (checked again on the way back), :class:`ProviderUnavailable` when the provider's
    discovery document cannot be fetched. Writes nothing (except a rate-limit event)."""
    config = _oidc_config(provider)
    await _count_or_refuse(db, store, oidc_limit("start"), client.ip or "unknown", client)
    invitation_hash = None
    if invitation_token is not None:
        invitation_hash = hash_token(invitation_token)
        await _usable_invitation_by_hash(db, invitation_hash, utcnow(), lock=False)
    flow = oidc.new_flow(provider, next_path, invitation_hash)
    try:
        url = await oidc.authorization_url(config, flow)
    except oidc.OidcError as exc:
        raise ProviderUnavailable(config.name) from exc
    return OidcStarted(authorization_url=url, sealed_flow=oidc.seal(flow))


def _one_word(value: str) -> str:
    """A provider's ``error`` parameter, safe to keep in an audit event."""
    return re.sub(r"[^a-z0-9_.-]", "", value.lower())[:64] or "unknown"


async def complete_oidc_login(
    db: AsyncSession,
    store: RateLimitStore,
    provider: OidcProvider,
    *,
    sealed_flow: str | None,
    state: str | None,
    code: str | None,
    error: str | None,
    carried_token: str | None,
    client: ClientInfo,
) -> OidcSignedIn | OidcFailed:
    """The provider sent the browser back (spec §4.3 steps 3-6). Every outcome but an unknown
    provider (:class:`NotFoundError`) is an answer, not an exception: :class:`OidcSignedIn`
    (rotated into a new session; committed) or :class:`OidcFailed` (audited
    ``auth.login_failed`` with the reason, or ``auth.rate_limited``).

    1. At most ``OIDC_MAX_ATTEMPTS_PER_IP`` callbacks per IP and window.
    2. The flow comes from the encrypted cookie (``sealed_flow``): missing, altered or older than
       10 minutes is ``expired``; its ``state`` must equal the query's (login CSRF), and its
       provider the path's.
    3. The provider's own ``error`` (``access_denied``: ``cancelled``), else the code is exchanged
       with the PKCE verifier and the ID token validated (``app.modules.identity.oidc``).
    4. The account is resolved and signed in (:func:`_oidc_sign_in`).
    """
    config = _oidc_config(provider)
    limit = oidc_limit("callback")
    key = limit.key(hashed(client.ip or "unknown"))
    window = await store.hit(key, limit.seconds)
    if window.count > limit.max_hits:
        await _rate_limited(db, store, limit, key, client)
        return OidcFailed(OidcLoginError.rate_limited)

    flow = oidc.unseal(sealed_flow)
    if flow is None or flow.provider is not provider:
        reason = "no_state_cookie" if sealed_flow is None else "invalid_state_cookie"
        return await _oidc_failed(db, provider, OidcLoginError.expired, reason, client)
    if not state or not hmac.compare_digest(state.encode(), flow.state.encode()):
        return await _oidc_failed(
            db, provider, OidcLoginError.oidc_failed, "state_mismatch", client
        )
    if error:
        failure = (
            OidcLoginError.cancelled if error == "access_denied" else OidcLoginError.oidc_failed
        )
        return await _oidc_failed(
            db, provider, failure, "provider_error", client, details={"error": _one_word(error)}
        )
    if not code:
        return await _oidc_failed(db, provider, OidcLoginError.oidc_failed, "no_code", client)
    try:
        id_token = await oidc.exchange_code(config, code=code, code_verifier=flow.code_verifier)
        identity = await oidc.validate_id_token(config, id_token, nonce=flow.nonce)
    except oidc.OidcError as exc:
        return await _oidc_failed(db, provider, OidcLoginError.oidc_failed, exc.reason, client)
    return await _oidc_sign_in(
        db, config, identity, flow, carried_token=carried_token, client=client
    )


async def _oidc_failed(
    db: AsyncSession,
    provider: OidcProvider,
    error: OidcLoginError,
    reason: str,
    client: ClientInfo,
    *,
    user_id: uuid.UUID | None = None,
    details: Mapping[str, Any] | None = None,
) -> OidcFailed:
    """Undo the attempt's writes, record ``auth.login_failed`` on its own and commit it."""
    await db.rollback()
    await record_audit(
        db,
        "auth.login_failed",
        actor_user_id=user_id,
        target_type="user" if user_id else None,
        target_id=user_id,
        client=client,
        details={"method": provider.value, "reason": reason, **(details or {})},
    )
    await db.commit()
    return OidcFailed(error)


def _display_name(identity: oidc.Identity, email: str) -> str:
    return identity.name or email.split("@", 1)[0][:200] or email[:200]


async def _oidc_sign_in(
    db: AsyncSession,
    config: oidc.ProviderConfig,
    identity: oidc.Identity,
    flow: oidc.Flow,
    *,
    carried_token: str | None,
    client: ClientInfo,
) -> OidcSignedIn | OidcFailed:
    """Resolve the account (spec §4.3 step 5) and sign it in (step 6).

    1. An ``oidc_accounts`` row with this ``(provider, issuer, subject)``: its user. With an
       invitation, the user's email must be the invited one (``invitation_mismatch``).
    2. The flow carries an **invitation** (the token proved the invited inbox): the identity is
       linked to the invited address's account, created (no password, email verified) when
       there is none. An existing account is linked only when the provider vouches for that same
       address (``account_exists`` otherwise: sign in with the password, then accept), so a
       forwarded invitation link cannot attach a stranger's identity to an existing account.
       The invitation is then accepted (E2-07's :func:`_join_agency`).
    3. The provider vouches for the email (Google ``email_verified``, Microsoft ``xms_edov``) and
       an account has it: the identity is linked to it, and the account holder is emailed.
    4. Otherwise ``no_account`` (a vouched address without an account) or
       ``email_not_verified`` (an address the provider does not vouch for is never looked up).

    The account must be active with an active membership in an active agency (``no_access``).
    Then: the session the request carried is revoked and a new one starts (the invitation's
    agency active, else the only or the most recently used one), and ``auth.oidc_linked`` (when
    linked), ``team.invitation_accepted`` (with ``method``) and ``auth.login_succeeded``
    (``method: google|microsoft``) are recorded. Commits, then queues the "new sign-in method"
    email for a link to an account that existed before."""
    now = utcnow()
    provider = identity.provider
    email_hash = {"email_hash": hashed(identity.email)} if identity.email else {}

    async def failed(
        error: OidcLoginError, reason: str, user_id: uuid.UUID | None = None
    ) -> OidcFailed:
        details = email_hash if user_id is None else {}
        return await _oidc_failed(
            db, provider, error, reason, client, user_id=user_id, details=details
        )

    invitation: Invitation | None = None
    if flow.invitation_hash is not None:
        try:
            invitation, _ = await _usable_invitation_by_hash(
                db, flow.invitation_hash, now, lock=True
            )
        except TokenGone:
            return await failed(OidcLoginError.invitation_invalid, "invitation_gone")

    account = await repository.get_oidc_account(
        db, provider.value, identity.issuer, identity.subject
    )
    user: User | None
    linked_via: str | None = None
    created = False
    if account is not None:  # 1
        user = await repository.get_user(db, account.user_id)
        assert user is not None  # a foreign key, deleted with the user
        if invitation is not None and user.email != invitation.email:
            return await failed(
                OidcLoginError.invitation_mismatch, "invitation_for_another_email", user.id
            )
    elif invitation is not None:  # 2
        user = await repository.get_user_by_email(db, invitation.email)
        if user is None:
            user = User(
                email=invitation.email,
                email_verified_at=now,  # the invitation token proves the inbox
                password_hash=None,  # signs in with the provider (or resets a password)
                display_name=_display_name(identity, invitation.email),
                status=UserStatus.active.value,
            )
            try:
                async with db.begin_nested():
                    db.add(user)
            except IntegrityError:  # created by a parallel request
                return await failed(OidcLoginError.oidc_failed, "account_race")
            created = True
        elif not (identity.email_verified and identity.email == invitation.email):
            return await failed(OidcLoginError.account_exists, "invitation_account_exists")
        linked_via = "invitation"
    elif identity.email is None or not identity.email_verified:  # 4
        return await failed(OidcLoginError.email_not_verified, "email_not_verified")
    else:  # 3
        user = await repository.get_user_by_email(db, identity.email)
        if user is None:
            return await failed(OidcLoginError.no_account, "no_account")
        linked_via = "verified_email"

    if user.status != UserStatus.active.value:
        return await failed(OidcLoginError.no_access, "user_disabled", user.id)
    if invitation is not None:
        try:
            await _join_agency(db, invitation, user, now)
        except ConflictError:
            return await failed(OidcLoginError.already_member, "already_member", user.id)
    memberships = await repository.usable_memberships(db, user.id)
    if not memberships:
        return await failed(OidcLoginError.no_access, "no_agency_access", user.id)
    active: Membership | None = None
    if invitation is not None:
        active = next(m for m, _ in memberships if m.agency_id == invitation.agency_id)
    elif len(memberships) == 1 or memberships[0][0].last_active_at is not None:
        active = memberships[0][0]
    agency_id = active.agency_id if active is not None else None

    if linked_via is not None:
        try:
            async with db.begin_nested():
                db.add(
                    OidcAccount(
                        user_id=user.id,
                        provider=provider.value,
                        issuer=identity.issuer,
                        subject=identity.subject,
                        email_at_link=identity.email,
                        created_at=now,
                        last_used_at=now,
                    )
                )
        except IntegrityError:  # the same identity linked by a parallel request
            return await failed(OidcLoginError.oidc_failed, "link_race", user.id)
    else:
        assert account is not None
        account.last_used_at = now

    # Rotate: the carried session (if any) ends, a new id starts (session fixation).
    if carried_token:
        await repository.revoke_session(db, hash_token(carried_token), now)
    token = await _start_session(db, user.id, agency_id, client, now)
    user.last_login_at = now
    if active is not None:
        active.last_active_at = now
    if linked_via is not None:
        await record_audit(
            db,
            "auth.oidc_linked",
            agency_id=agency_id,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            client=client,
            details={
                "provider": provider.value,
                "issuer": identity.issuer,
                "via": linked_via,
                "new_account": created,
            },
        )
    if invitation is not None:
        await record_audit(
            db,
            "team.invitation_accepted",
            agency_id=invitation.agency_id,
            actor_user_id=user.id,
            target_type="invitation",
            target_id=invitation.id,
            client=client,
            details={"role": invitation.role, "new_account": created, "method": provider.value},
        )
    await record_audit(
        db,
        "auth.login_succeeded",
        agency_id=agency_id,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        client=client,
        details={"method": provider.value},
    )
    notice = (
        emails.sign_in_method_linked(
            to=user.email,
            display_name=user.display_name,
            provider_name=config.name,
            provider_email=identity.email,
        )
        if linked_via is not None and not created
        else None
    )
    await db.commit()
    if notice is not None:
        tasks.enqueue_email(notice)  # spec §4.3: a new sign-in method is notified by email
    return OidcSignedIn(token=token, next=flow.next)


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
