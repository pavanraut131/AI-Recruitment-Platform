"""identity data access: every query against identity's tables.

Private to the identity module (only ``service.py`` and ``tasks.py`` call it). The tables are
global (spec §6.4), so nothing here filters by the session's tenant: every query names the user,
session, token or agency it is about, and the team queries take the agency explicitly.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Row,
    and_,
    delete,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.identity.models import (
    Agency,
    AgencyStatus,
    AuditEvent,
    Invitation,
    Membership,
    MembershipClient,
    MembershipStatus,
    PasswordResetToken,
    User,
    UserSession,
)


# ── Agencies ──────────────────────────────────────────────────
async def get_agency(db: AsyncSession, agency_id: uuid.UUID) -> Agency | None:
    result = await db.execute(select(Agency).where(Agency.id == agency_id))
    return result.scalar_one_or_none()


async def get_agency_by_slug(db: AsyncSession, slug: str) -> Agency | None:
    result = await db.execute(select(Agency).where(Agency.slug == slug))
    return result.scalar_one_or_none()


def agency_ids_sync(db: Session) -> set[uuid.UUID]:
    return set(db.execute(select(Agency.id)).scalars())


# ── Users and memberships ─────────────────────────────────────
async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def get_membership(
    db: AsyncSession, agency_id: uuid.UUID, user_id: uuid.UUID
) -> Membership | None:
    result = await db.execute(
        select(Membership).where(Membership.agency_id == agency_id, Membership.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def usable_memberships(db: AsyncSession, user_id: uuid.UUID) -> Sequence[Row[Any]]:
    """``(Membership, Agency)`` for each **active** membership of the user in an **active**
    agency, most recently used first (never-used ones last), then by agency name."""
    result = await db.execute(
        select(Membership, Agency)
        .join(Agency, Agency.id == Membership.agency_id)
        .where(
            Membership.user_id == user_id,
            Membership.status == MembershipStatus.active.value,
            Agency.status == AgencyStatus.active.value,
        )
        .order_by(Membership.last_active_at.desc().nulls_last(), Agency.name, Agency.id)
    )
    return result.all()


async def touch_membership(db: AsyncSession, membership_id: uuid.UUID, now: datetime) -> None:
    await db.execute(
        update(Membership).where(Membership.id == membership_id).values(last_active_at=now)
    )


# ── Sessions ──────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class SessionRow:
    """A session with its user and, when it has an active agency, that membership and agency
    (either may be missing or no longer active: the service decides)."""

    session: UserSession
    user: User
    membership: Membership | None
    agency: Agency | None
    client_ids: list[uuid.UUID]


async def load_session(db: AsyncSession, id_hash: bytes) -> SessionRow | None:
    """The session, its user, the membership in its active agency, that agency and the
    membership's client list: **one** query (an index lookup on each table), run on every
    authenticated request."""
    client_ids = (
        select(func.array_agg(MembershipClient.client_id))
        .where(MembershipClient.membership_id == Membership.id)
        .correlate(Membership)
        .scalar_subquery()
    )
    result = await db.execute(
        select(UserSession, User, Membership, Agency, client_ids)
        .join(User, User.id == UserSession.user_id)
        .outerjoin(
            Membership,
            (Membership.user_id == UserSession.user_id)
            & (Membership.agency_id == UserSession.active_agency_id),
        )
        .outerjoin(Agency, Agency.id == UserSession.active_agency_id)
        .where(UserSession.id_hash == id_hash)
    )
    row = result.one_or_none()
    if row is None:
        return None
    session, user, membership, agency, ids = row
    return SessionRow(session, user, membership, agency, list(ids or []))


async def touch_session(
    db: AsyncSession, id_hash: bytes, now: datetime, idle_expires_at: datetime
) -> None:
    await db.execute(
        update(UserSession)
        .where(UserSession.id_hash == id_hash)
        .values(last_seen_at=now, idle_expires_at=idle_expires_at)
    )


async def set_active_agency(db: AsyncSession, id_hash: bytes, agency_id: uuid.UUID) -> None:
    await db.execute(
        update(UserSession).where(UserSession.id_hash == id_hash).values(active_agency_id=agency_id)
    )


async def revoke_session(db: AsyncSession, id_hash: bytes, now: datetime) -> None:
    await db.execute(
        update(UserSession)
        .where(UserSession.id_hash == id_hash, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


async def revoke_user_sessions(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> int:
    """Revoke every open session of the user; returns how many."""
    result = await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return int(getattr(result, "rowcount", 0) or 0)


# ── Team (E2-07) ──────────────────────────────────────────────
def _client_ids_of_membership() -> Any:
    """The membership's client ids as one array (empty when it has none), correlated to the
    ``Membership`` of the enclosing query."""
    return (
        select(func.coalesce(func.array_agg(MembershipClient.client_id), text("'{}'::uuid[]")))
        .where(MembershipClient.membership_id == Membership.id)
        .correlate(Membership)
        .scalar_subquery()
    )


async def list_members(
    db: AsyncSession, agency_id: uuid.UUID, offset: int, limit: int
) -> tuple[Sequence[Row[Any]], int]:
    """A page of ``(Membership, User, client_ids)`` of the agency (deactivated members
    included), by display name, then email; and the total."""
    total = await db.scalar(
        select(func.count()).select_from(Membership).where(Membership.agency_id == agency_id)
    )
    result = await db.execute(
        select(Membership, User, _client_ids_of_membership())
        .join(User, User.id == Membership.user_id)
        .where(Membership.agency_id == agency_id)
        .order_by(func.lower(User.display_name), User.email, Membership.id)
        .offset(offset)
        .limit(limit)
    )
    return result.all(), int(total or 0)


async def get_member(
    db: AsyncSession, agency_id: uuid.UUID, membership_id: uuid.UUID, *, lock: bool = False
) -> tuple[Membership, User] | None:
    """The agency's membership with this id and its user; ``lock`` takes the membership's row
    lock (``FOR UPDATE``) for the rest of the transaction."""
    query = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.id == membership_id, Membership.agency_id == agency_id)
    )
    if lock:
        query = query.with_for_update(of=Membership)
    row = (await db.execute(query)).one_or_none()
    return None if row is None else (row[0], row[1])


async def membership_client_ids(db: AsyncSession, membership_id: uuid.UUID) -> list[uuid.UUID]:
    result = await db.execute(
        select(MembershipClient.client_id)
        .where(MembershipClient.membership_id == membership_id)
        .order_by(MembershipClient.client_id)
    )
    return list(result.scalars())


async def set_membership_clients(
    db: AsyncSession, membership_id: uuid.UUID, client_ids: Sequence[uuid.UUID]
) -> None:
    """Replace the membership's client list."""
    await db.execute(
        delete(MembershipClient).where(MembershipClient.membership_id == membership_id)
    )
    if client_ids:
        await db.execute(
            insert(MembershipClient),
            [{"membership_id": membership_id, "client_id": client_id} for client_id in client_ids],
        )


async def lock_active_owners(db: AsyncSession, agency_id: uuid.UUID) -> list[uuid.UUID]:
    """The ids of the agency's active owner memberships, row-locked in id order (two requests
    that change owners queue up instead of each removing the other)."""
    result = await db.execute(
        select(Membership.id)
        .where(
            Membership.agency_id == agency_id,
            Membership.role == "owner",
            Membership.status == MembershipStatus.active.value,
        )
        .order_by(Membership.id)
        .with_for_update()
    )
    return list(result.scalars())


async def is_active_member(db: AsyncSession, agency_id: uuid.UUID, email: str) -> bool:
    """Whether the user with this (normalised) email is an active member of the agency."""
    result = await db.execute(
        select(Membership.id)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.agency_id == agency_id,
            Membership.status == MembershipStatus.active.value,
            User.email == email,
        )
    )
    return result.first() is not None


# ── Invitations (E2-07) ───────────────────────────────────────
def _open() -> ColumnElement[bool]:
    """Neither accepted nor revoked (expired ones included)."""
    return and_(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None))


async def open_invitation_for_email(
    db: AsyncSession, agency_id: uuid.UUID, email: str
) -> Invitation | None:
    """The agency's open invitation for this address (row-locked), if any."""
    result = await db.execute(
        select(Invitation)
        .where(Invitation.agency_id == agency_id, Invitation.email == email, _open())
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def get_open_invitation(
    db: AsyncSession, agency_id: uuid.UUID, invitation_id: uuid.UUID
) -> Invitation | None:
    """The agency's open invitation with this id (row-locked), if any."""
    result = await db.execute(
        select(Invitation)
        .where(Invitation.id == invitation_id, Invitation.agency_id == agency_id, _open())
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def list_open_invitations(
    db: AsyncSession, agency_id: uuid.UUID, offset: int, limit: int
) -> tuple[Sequence[Row[Any]], int]:
    """A page of ``(Invitation, inviter User | None)``: the agency's open invitations, newest
    first; and the total."""
    where = and_(Invitation.agency_id == agency_id, _open())
    total = await db.scalar(select(func.count()).select_from(Invitation).where(where))
    result = await db.execute(
        select(Invitation, User)
        .outerjoin(User, User.id == Invitation.invited_by_user_id)
        .where(where)
        .order_by(Invitation.created_at.desc(), Invitation.id)
        .offset(offset)
        .limit(limit)
    )
    return result.all(), int(total or 0)


async def invitation_by_token(
    db: AsyncSession, token_hash: bytes, *, lock: bool = False
) -> Invitation | None:
    """The invitation whose token hashes to ``token_hash``, in any state."""
    query = select(Invitation).where(Invitation.token_hash == token_hash)
    if lock:
        query = query.with_for_update()
    return (await db.execute(query)).scalar_one_or_none()


# ── Password reset (E2-07) ────────────────────────────────────
async def delete_unused_reset_tokens(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(
        delete(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None)
        )
    )


async def reset_token_by_hash(db: AsyncSession, token_hash: bytes) -> PasswordResetToken | None:
    """The reset token whose value hashes to ``token_hash`` (row-locked), in any state."""
    result = await db.execute(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == token_hash)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def use_reset_tokens(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    """Mark every unused reset token of the user used."""
    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )


# ── Purge (sync: the identity.purge_expired task, E2-07) ──────
def purge_expired_sync(db: Session, *, now: datetime, ended_before: datetime) -> dict[str, int]:
    """Delete sessions that ended (idle or absolute expiry, or revocation) before
    ``ended_before``, reset tokens that are used or expired at ``now``, and invitations that
    expired before ``ended_before``; returns how many of each. The caller commits."""

    def count(result: object) -> int:
        return int(getattr(result, "rowcount", 0) or 0)

    sessions = db.execute(
        delete(UserSession).where(
            or_(
                UserSession.idle_expires_at < ended_before,
                UserSession.absolute_expires_at < ended_before,
                UserSession.revoked_at < ended_before,
            )
        )
    )
    tokens = db.execute(
        delete(PasswordResetToken).where(
            or_(PasswordResetToken.used_at.is_not(None), PasswordResetToken.expires_at <= now)
        )
    )
    invitations = db.execute(delete(Invitation).where(Invitation.expires_at < ended_before))
    return {
        "sessions": count(sessions),
        "password_reset_tokens": count(tokens),
        "invitations": count(invitations),
    }


# ── Audit ─────────────────────────────────────────────────────
def add_audit_event(db: AsyncSession, event: AuditEvent) -> None:
    db.add(event)
