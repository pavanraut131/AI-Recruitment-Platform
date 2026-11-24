"""Row-level security (EPIC-02 spec §6.2, §11.2; ADR 0009): the database itself refuses another
agency's rows, whatever the code forgets.

- ``test_catalogue_*``: every table not on the global allowlist has RLS enabled and forced, the
  ``tenant_isolation`` and ``maintenance`` policies, and the grants of spec §6.2; the app role is
  neither a superuser nor ``BYPASSRLS`` and owns nothing.
- ``test_unbound_session_sees_nothing``: a session with no agency reads no row of any tenant
  table and can insert none.
- ``test_forgotten_filter_is_contained``: the backstop. Queries with **no** agency filter on a
  session bound to agency A return, change and delete only A's rows, and a row of B is refused.
- ``test_tenant_does_not_leak_across_pool_checkouts``: a pooled connection carries no agency into
  its next checkout, and the development guard catches one that does.

Every test runs as ``hireflow_app`` (``tests/postgres.py``), like the whole suite. Two agencies'
rows sit in every tenant table: the default agency's through ``db_session``, the other's through
a session bound to it (``other_agency.db``), on the same rolled-back connection.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, TextClause, create_engine, delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.modules.hiring.models import Application, Campaign, Client
from app.modules.ingestion.models import Resume
from app.modules.talent.models import IdentityConflict, Person, PersonIdentifier, PersonResume
from app.platform import rls
from app.platform.config import settings
from app.platform.tenancy import (
    DEFAULT_AGENCY_ID,
    DEFAULT_CLIENT_ID,
    TenantLeak,
    bind,
    guard_pool_checkouts,
)
from tests import postgres
from tests.factories import make_campaign, make_candidate, make_resume
from tests.tenancy.conftest import OtherAgency, SessionFor

TENANT_MODELS = (
    Client,
    Campaign,
    Application,
    Person,
    PersonIdentifier,
    IdentityConflict,
    PersonResume,
    Resume,
)
T0 = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
INSUFFICIENT_PRIVILEGE = "42501"  # "new row violates row-level security policy"


@dataclass(frozen=True)
class Rows:
    """One agency's rows: at least one in every tenant table."""

    agency_id: uuid.UUID
    campaign_id: uuid.UUID
    application_id: uuid.UUID
    person_id: uuid.UUID


async def _populate(db: AsyncSession, client_id: uuid.UUID, name: str) -> Rows:
    """A campaign, two processed resumes with their People and Applications, an identifier, a
    conflict and the resolutions, all in ``db``'s agency (through ``db``)."""
    campaign = await make_campaign(db, title=f"{name}'s campaign", client_id=client_id)
    first = await make_candidate(db, campaign, name=f"{name} One", email=f"one@{name}.example")
    second = await make_candidate(
        db,
        campaign,
        resume=await make_resume(db, campaign, original_filename=f"{name}_two.pdf"),
        name=f"{name} Two",
    )
    agency_id = campaign.agency_id
    db.add_all(
        [
            PersonIdentifier(
                agency_id=agency_id,
                person_id=first.person_id,
                kind="email",
                value_normalized=f"one@{name}.example",
                raw_value=f"one@{name}.example",
            ),
            IdentityConflict(
                agency_id=agency_id,
                resume_id=second.resume_id,
                chosen_person_id=second.person_id,
                conflicting_person_id=first.person_id,
                reason="test",
            ),
            *(
                PersonResume(
                    agency_id=agency_id,
                    resume_id=application.resume_id,
                    person_id=application.person_id,
                    uploaded_at=T0,
                    matched_existing=False,
                )
                for application in (first, second)
            ),
        ]
    )
    await db.flush()
    return Rows(agency_id, campaign.id, first.id, first.person_id)


@pytest.fixture
async def two_agencies(db_session: AsyncSession, other_agency: OtherAgency) -> tuple[Rows, Rows]:
    mine = await _populate(db_session, client_id=DEFAULT_CLIENT_ID, name="mine")
    theirs = await _populate(other_agency.db, client_id=other_agency.client_id, name="theirs")
    return mine, theirs


async def _agencies_in(db: AsyncSession, model: type) -> list[uuid.UUID]:
    return list(await db.scalars(select(model.agency_id)))  # type: ignore[attr-defined]


# ── The catalogue ─────────────────────────────────────────────
@pytest.fixture
def owner_connection() -> Iterator[Engine]:
    engine = create_engine(settings.migration_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_catalogue_matches_the_spec(owner_connection: Engine) -> None:
    """The whole check (also ``python -m app.devtools.check_rls`` and the CI migrations job)."""
    with owner_connection.connect() as conn:
        assert rls.catalogue_problems(conn) == []


def test_catalogue_every_tenant_table_is_forced_with_both_policies(
    owner_connection: Engine,
) -> None:
    """The same, spelled out, so a regression in ``catalogue_problems`` cannot hide one."""
    with owner_connection.connect() as conn:
        tables = rls.tenant_tables(conn)
        assert tables == sorted(model.__tablename__ for model in TENANT_MODELS)
        flags = dict(
            conn.execute(
                text(
                    "SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
                )
            )
            .tuples()
            .all()
        )
        policies = {
            (table, name): roles
            for table, name, roles in conn.execute(
                text("SELECT tablename, policyname, roles::text[] FROM pg_policies")
            ).tuples()
        }
    for table in tables:
        assert flags[table], table
        assert policies[(table, "tenant_isolation")] == ["hireflow_app"], table
        assert policies[(table, "maintenance")] == ["hireflow_owner"], table
    for table in rls.GLOBAL_TABLES:
        assert not flags[table], table


async def test_catalogue_the_app_role_is_restricted(db_session: AsyncSession) -> None:
    """The suite's own connection: the app role, not a superuser, no BYPASSRLS, owns nothing."""
    role, superuser, bypassrls = (
        await db_session.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        )
    ).one()
    assert (role, superuser, bypassrls) == ("hireflow_app", False, False)
    owned = await db_session.scalar(
        text("SELECT count(*) FROM pg_class WHERE relowner = 'hireflow_app'::regrole")
    )
    assert owned == 0
    grants = (
        await db_session.execute(
            text(
                "SELECT has_table_privilege('campaigns', 'TRUNCATE'), "
                "has_table_privilege('alembic_version', 'SELECT'), "
                "has_table_privilege('agencies', 'SELECT')"
            )
        )
    ).one()
    assert tuple(grants) == (False, False, True)


def test_catalogue_problems_are_reported(owner_connection: Engine) -> None:
    """``catalogue_problems`` notices a table that left RLS, in a transaction rolled back."""
    with owner_connection.connect() as conn, conn.begin() as tx:
        conn.execute(text("ALTER TABLE campaigns NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text("DROP POLICY tenant_isolation ON people"))
        conn.execute(text("GRANT TRUNCATE ON resumes TO hireflow_app"))
        conn.execute(text("CREATE TABLE stray (id int)"))
        problems = rls.catalogue_problems(conn)
        tx.rollback()
    assert problems == [
        "campaigns: row-level security is not enabled and forced",
        "people: no tenant_isolation policy",
        "stray: row-level security is not enabled and forced",
        "stray: no tenant_isolation policy",
        "stray: no maintenance policy",
        "resumes: hireflow_app has ['DELETE', 'INSERT', 'SELECT', 'TRUNCATE', 'UPDATE'], "
        "expected ['DELETE', 'INSERT', 'SELECT', 'UPDATE']",
    ]


# ── No tenant: nothing ────────────────────────────────────────
async def test_unbound_session_sees_nothing(
    two_agencies: tuple[Rows, Rows], session_for: SessionFor
) -> None:
    mine, _ = two_agencies
    unbound = session_for(None)
    for model in TENANT_MODELS:
        count = await unbound.scalar(select(func.count()).select_from(model))
        assert count == 0, model.__tablename__

    # ...and writes nothing: no insert passes WITH CHECK, whatever agency it names.
    for model in TENANT_MODELS:
        with pytest.raises(DBAPIError) as raised:
            async with unbound.begin_nested():
                await unbound.execute(
                    text(f"INSERT INTO {model.__tablename__} (agency_id) VALUES (:agency)"),
                    {"agency": mine.agency_id},
                )
        assert raised.value.orig is not None
        assert getattr(raised.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE
    result = await unbound.execute(update(Campaign).values(title="changed"))
    assert result.rowcount == 0  # type: ignore[attr-defined]


# ── The backstop ──────────────────────────────────────────────
async def test_forgotten_filter_is_contained(
    db_session: AsyncSession, two_agencies: tuple[Rows, Rows], other_agency: OtherAgency
) -> None:
    mine, theirs = two_agencies

    # select(Model) with no WHERE: only the bound agency's rows, in every tenant table.
    for model in TENANT_MODELS:
        agencies = await _agencies_in(db_session, model)
        assert agencies, model.__tablename__
        assert set(agencies) == {DEFAULT_AGENCY_ID}, model.__tablename__

    # A real repository query (talent.repository.search_people) with its tenant_filter left
    # out: the search matches both agencies' names, the identifier EXISTS too.
    from app.modules.talent.repository import _search_clause

    clause = _search_clause("One")
    total = await db_session.scalar(select(func.count(Person.id)).where(clause))
    people = (
        await db_session.scalars(
            select(Person).where(clause).order_by(Person.updated_at.desc(), Person.id)
        )
    ).all()
    assert total == 1
    assert [p.id for p in people] == [mine.person_id]

    # UPDATE and DELETE with no WHERE: only the bound agency's rows.
    their_campaigns = await other_agency.db.scalar(select(func.count()).select_from(Campaign))
    changed = await db_session.execute(update(Campaign).values(title="renamed"))
    assert changed.rowcount == 1  # type: ignore[attr-defined]
    deleted = await db_session.execute(delete(Application))
    assert deleted.rowcount == 2  # type: ignore[attr-defined]
    other_agency.db.expire_all()
    titles = set(await other_agency.db.scalars(select(Campaign.title)))
    assert titles == {"theirs's campaign"}
    assert await other_agency.db.scalar(select(func.count()).select_from(Campaign)) == (
        their_campaigns
    )
    assert await other_agency.db.get(Application, theirs.application_id) is not None

    # A row naming another agency is refused (WITH CHECK), by insert and by update.
    for statement in (
        insert_campaign_of(theirs.agency_id, other_agency.client_id),
        text("UPDATE people SET agency_id = :theirs"),
    ):
        with pytest.raises(DBAPIError) as raised:
            async with db_session.begin_nested():
                await db_session.execute(statement, {"theirs": theirs.agency_id})
        assert getattr(raised.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE

    # By id, another agency's row does not exist.
    assert await db_session.get(Campaign, theirs.campaign_id) is None
    assert await db_session.get(Person, theirs.person_id) is None


def insert_campaign_of(agency_id: uuid.UUID, client_id: uuid.UUID) -> TextClause:
    return text(
        "INSERT INTO campaigns (id, title, role, required_skills, status, agency_id, client_id) "
        f"VALUES ('{uuid.uuid4()}', 'x', 'x', '[]', 'active', :theirs, '{client_id}')"
    )


# ── Pooled connections ────────────────────────────────────────
@pytest.fixture
def one_connection_pool() -> Iterator[Engine]:
    """The workers' kind of engine with a one-connection pool and the development guard."""
    engine = create_engine(settings.SYNC_DATABASE_URL, pool_size=1, max_overflow=0)
    guard_pool_checkouts(engine)
    try:
        yield engine
    finally:
        engine.dispose()


SETTING = text("SELECT pg_backend_pid(), current_setting('app.agency_id', true)")


def test_tenant_does_not_leak_across_pool_checkouts(one_connection_pool: Engine) -> None:
    clients = select(func.count()).select_from(Client)
    with Session(one_connection_pool) as bound:
        bind(bound, DEFAULT_AGENCY_ID)
        pid, setting = bound.execute(SETTING).one()
        assert setting == str(DEFAULT_AGENCY_ID)
        assert bound.scalar(clients) >= 1  # the default client (E2-01)
        bound.commit()
        # A session that commits and goes on: its next transaction has the agency again.
        assert bound.execute(SETTING).one() == (pid, str(DEFAULT_AGENCY_ID))
        assert bound.scalar(clients) >= 1

    with Session(one_connection_pool) as unbound:  # the same connection, checked out again
        assert unbound.execute(SETTING).one() == (pid, "")
        assert unbound.scalar(clients) == 0


def test_the_development_guard_catches_a_session_level_setting(
    one_connection_pool: Engine,
) -> None:
    with one_connection_pool.connect() as conn:
        # What the code must never do (a CI check rejects it in app code): survives the commit.
        conn.exec_driver_sql(f"SELECT set_config('app.agency_id', '{DEFAULT_AGENCY_ID}', false)")
        conn.commit()
    with pytest.raises(TenantLeak, match="app.agency_id"):
        one_connection_pool.connect()
    one_connection_pool.dispose()  # drop the poisoned connection


def test_the_app_engines_have_the_guard_in_tests() -> None:
    from app.modules.ingestion import tasks
    from app.platform import database, maintenance

    for engine in (database.engine.sync_engine, tasks.sync_engine, maintenance.owner_engine()):
        names = [listener.__name__ for listener in engine.pool.dispatch.checkout]
        assert "_assert_no_agency" in names, engine


def test_postgres_harness_runs_as_the_app_role() -> None:
    assert settings.DATABASE_URL.startswith("postgresql+asyncpg://hireflow_app:")
    assert settings.SYNC_DATABASE_URL.split("://")[1].startswith("hireflow_app:")
    assert settings.migration_database_url.split("://")[1].startswith("hireflow_owner:")
    assert postgres.APP_ROLE == rls.APP_ROLE and postgres.OWNER_ROLE == rls.OWNER_ROLE
