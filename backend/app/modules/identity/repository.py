"""identity data access: every query against identity's tables.

Private to the identity module (only ``service.py`` calls it). The tables are global (spec
§6.4), so nothing here filters by the session's tenant: every query names the user, session or
agency it is about.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.identity.models import (
    Agency,
    AgencyStatus,
    AuditEvent,
    Membership,
    MembershipClient,
    MembershipStatus,
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


# ── Audit ─────────────────────────────────────────────────────
def add_audit_event(db: AsyncSession, event: AuditEvent) -> None:
    db.add(event)
