"""Development sign-in accounts for the seed (EPIC-02 spec §11.4): two agencies and one user per
role in each, all with the same **development-only** password.

``python -m app.devtools.seed`` calls :func:`seed_users` first, on every run (``--if-empty`` and
``--reset`` included): it is idempotent (existing agencies, users and memberships are kept, and an
existing user's password is never changed) and touches nothing else. The seeded campaigns,
People and Applications stay in the default agency; the second agency starts empty.

======================================  =========  ======================================
email                                   role       agency
======================================  =========  ======================================
``owner@default.example``               owner      Default agency (the existing data)
``admin@default.example``               admin      Default agency
``recruiter@default.example``           recruiter  Default agency
``viewer@default.example``              viewer     Default agency
``owner@northbank.example``             owner      Northbank Talent (slug ``northbank``)
``admin@northbank.example``             admin      Northbank Talent
``recruiter@northbank.example``         recruiter  Northbank Talent
``viewer@northbank.example``            viewer     Northbank Talent
======================================  =========  ======================================

Password for all of them: :data:`DEV_PASSWORD` (``hireflow-dev-password``). Refused unless
``APP_ENV`` is ``development`` or ``test``: a known password must never reach a deployment.

Until E2-06 gives each agency its own clients, a campaign created in Northbank Talent still gets
the default agency's client and fails; sign in to the default agency to work with campaigns.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.identity import service as identity
from app.modules.identity.schemas import Role
from app.platform.config import settings
from app.platform.tenancy import DEFAULT_AGENCY_ID

DEV_PASSWORD: Final = "hireflow-dev-password"
SECOND_AGENCY_NAME: Final = "Northbank Talent"
SECOND_AGENCY_SLUG: Final = "northbank"
ROLES: Final = (Role.owner, Role.admin, Role.recruiter, Role.viewer)


class SeedUsersRefused(RuntimeError):
    """Development accounts are not created outside development and test."""


@dataclass(frozen=True, slots=True)
class SeededUser:
    email: str
    role: Role
    agency_name: str
    created: bool


async def seed_users(db: AsyncSession) -> list[SeededUser]:
    """Create (if missing) the second agency and the eight accounts; flushes, the caller
    commits."""
    if not settings.is_development:
        raise SeedUsersRefused(
            f"refusing to create development accounts: APP_ENV={settings.APP_ENV}"
        )
    default = await identity.get_agency(db, DEFAULT_AGENCY_ID)
    if default is None:
        raise SeedUsersRefused("the default agency is missing: run the migrations first")
    second = await identity.ensure_agency(db, SECOND_AGENCY_NAME, SECOND_AGENCY_SLUG)
    seeded: list[SeededUser] = []
    agencies: tuple[tuple[uuid.UUID, str, str], ...] = (
        (default.id, default.name, "default"),
        (second.id, second.name, SECOND_AGENCY_SLUG),
    )
    for agency_id, agency_name, domain in agencies:
        for role in ROLES:
            user = await identity.ensure_user(
                db,
                f"{role.value}@{domain}.example",
                display_name=f"{role.value.capitalize()} ({agency_name})",
                password=DEV_PASSWORD,
            )
            created = await identity.ensure_membership(db, agency_id, user.id, role)
            seeded.append(SeededUser(user.email, role, agency_name, user.created or created))
    return seeded


async def run() -> list[SeededUser]:
    """:func:`seed_users` in its own transaction on ``DATABASE_URL``."""
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            seeded = await seed_users(db)
            await db.commit()
            return seeded
    finally:
        await engine.dispose()


def describe(seeded: list[SeededUser]) -> list[str]:
    created = sum(1 for user in seeded if user.created)
    lines = [
        f"seed: sign-in accounts ({created} new), development password {DEV_PASSWORD!r}:",
    ]
    lines += [f"seed:   {u.email:<30} {u.role.value:<10} {u.agency_name}" for u in seeded]
    return lines
