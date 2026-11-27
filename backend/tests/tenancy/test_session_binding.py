"""The session binding (``app.platform.tenancy``, EPIC-02 spec §6.1, §6.3): an unbound session
fails loudly, a bound one starts **every** transaction with its agency in the transaction-local
setting ``app.agency_id``, and a pooled connection never carries it into its next checkout."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Session

from app.modules.hiring import service as hiring_service
from app.modules.talent import service as talent_service
from app.platform.config import settings
from app.platform.tenancy import (
    AccessScope,
    TenantMismatch,
    TenantNotBound,
    agency_id_of,
    bind,
    tenant_of,
)
from tests.default_agency import DEFAULT_AGENCY_ID

A = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
B = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000002")
SETTING = text("SELECT pg_backend_pid(), current_setting('app.agency_id', true)")


@pytest_asyncio.fixture
async def one_connection_engine() -> AsyncIterator[AsyncEngine]:
    """An async engine whose pool holds exactly one connection: every session reuses it."""
    engine = create_async_engine(settings.DATABASE_URL, pool_size=1, max_overflow=0)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def one_connection_sync_engine() -> Iterator[Engine]:
    """The workers' kind of engine (sync), with a one-connection pool."""
    engine = create_engine(settings.SYNC_DATABASE_URL, pool_size=1, max_overflow=0)
    try:
        yield engine
    finally:
        engine.dispose()


# ── bind / agency_id_of ───────────────────────────────────────
async def test_an_unbound_session_raises_instead_of_querying(
    one_connection_engine: AsyncEngine,
) -> None:
    async with AsyncSession(one_connection_engine) as unbound:
        assert tenant_of(unbound) is None
        with pytest.raises(TenantNotBound):
            agency_id_of(unbound)
        with pytest.raises(TenantNotBound):
            await talent_service.count_people(unbound)
        with pytest.raises(TenantNotBound):
            await hiring_service.campaign_exists(unbound, AccessScope(A), uuid.uuid4())
        assert not unbound.in_transaction()  # nothing was sent to the database


def test_an_unbound_sync_session_raises_too(one_connection_sync_engine: Engine) -> None:
    with Session(one_connection_sync_engine) as unbound, pytest.raises(TenantNotBound):
        hiring_service.get_campaign_requirements(unbound, uuid.uuid4())


async def test_bind_is_once_per_session_and_before_its_first_transaction(
    one_connection_engine: AsyncEngine,
) -> None:
    async with AsyncSession(one_connection_engine) as session:
        bind(session, A)
        bind(session, A)  # the same agency again: nothing changes
        assert agency_id_of(session) == agency_id_of(session.sync_session) == A
        with pytest.raises(TenantMismatch):
            bind(session, B)

    async with AsyncSession(one_connection_engine) as session:
        await session.execute(text("SELECT 1"))
        with pytest.raises(RuntimeError, match="before its first transaction"):
            bind(session, A)


async def test_a_scope_of_another_agency_is_refused(db_session: AsyncSession) -> None:
    with pytest.raises(TenantMismatch):
        await hiring_service.campaign_exists(db_session, AccessScope(B), uuid.uuid4())


async def test_a_client_scoped_access_sees_only_its_clients(db_session: AsyncSession) -> None:
    """Client scoping (E2-06) filters instead of refusing: a campaign of a client outside the
    scope does not exist for it. The /api/v1 use cases (every client, default agency) refuse a
    client-limited scope rather than widen it."""
    from tests.factories import make_campaign

    campaign = await make_campaign(db_session)  # the default client, "General"
    other_client = AccessScope(DEFAULT_AGENCY_ID, client_ids=frozenset({uuid.uuid4()}))
    general = AccessScope(DEFAULT_AGENCY_ID, client_ids=frozenset({campaign.client_id}))
    assert not await hiring_service.campaign_exists(db_session, other_client, campaign.id)
    assert await hiring_service.campaign_exists(db_session, general, campaign.id)
    with pytest.raises(ValueError, match="/api/v1"):
        await hiring_service.get_campaign(db_session, general, campaign.id)


def test_a_scope_narrows_never_widens() -> None:
    x, y, z = (uuid.uuid4() for _ in range(3))
    every = AccessScope(A)
    some = AccessScope(A, frozenset({x, y}))
    assert every.restricted_to(None) is every
    assert every.restricted_to([x]) == AccessScope(A, frozenset({x}))
    assert some.restricted_to([y, z]) == AccessScope(A, frozenset({y}))
    assert some.restricted_to([z]) == AccessScope(A, frozenset())
    assert some.restricted_to([]) == AccessScope(A, frozenset())
    assert every.allows(z) and some.allows(x) and not some.allows(z)


# ── The setting, per transaction ──────────────────────────────
async def test_every_transaction_of_a_bound_session_starts_with_the_agency(
    one_connection_engine: AsyncEngine,
) -> None:
    async with AsyncSession(one_connection_engine) as session:
        bind(session, A)
        _, first = (await session.execute(SETTING)).one()
        await session.commit()  # ends the transaction, and the transaction-local setting
        _, second = (await session.execute(SETTING)).one()
        await session.rollback()
        _, third = (await session.execute(SETTING)).one()
    assert (first, second, third) == (str(A), str(A), str(A))


def test_every_transaction_of_a_bound_sync_session_starts_with_the_agency(
    one_connection_sync_engine: Engine,
) -> None:
    """The Celery tasks' case: one session, several commits."""
    with Session(one_connection_sync_engine) as session:
        bind(session, A)
        settings_seen = []
        for _ in range(3):
            settings_seen.append(session.execute(SETTING).one()[1])
            session.commit()
    assert settings_seen == [str(A)] * 3


async def test_the_test_session_is_bound_to_the_default_agency(db_session: AsyncSession) -> None:
    """``db_session`` joins an outer transaction through savepoints; it gets the setting too."""
    assert (await db_session.execute(SETTING)).one()[1] == str(DEFAULT_AGENCY_ID)


async def test_an_unbound_session_has_no_agency_setting(
    one_connection_engine: AsyncEngine,
) -> None:
    async with AsyncSession(one_connection_engine) as session:
        assert (await session.execute(SETTING)).one()[1] in (None, "")


# ── Pooled connections ────────────────────────────────────────
async def test_the_agency_does_not_leak_into_the_next_checkout(
    one_connection_engine: AsyncEngine,
) -> None:
    async with AsyncSession(one_connection_engine) as session:
        bind(session, A)
        pid, setting = (await session.execute(SETTING)).one()
        assert setting == str(A)
        await session.commit()

    async with AsyncSession(one_connection_engine) as next_session:  # unbound
        next_pid, next_setting = (await next_session.execute(SETTING)).one()
    async with one_connection_engine.connect() as conn:  # a plain connection
        conn_pid, conn_setting = (await conn.execute(SETTING)).one()

    assert next_pid == conn_pid == pid  # the very same pooled connection
    assert next_setting in (None, "")
    assert conn_setting in (None, "")


async def test_a_rolled_back_transaction_does_not_leak_either(
    one_connection_engine: AsyncEngine,
) -> None:
    async with AsyncSession(one_connection_engine) as session:
        bind(session, B)
        pid, _ = (await session.execute(SETTING)).one()
        # closed without commit: the pool rolls the connection back
    async with AsyncSession(one_connection_engine) as next_session:
        bind(next_session, A)
        next_pid, setting = (await next_session.execute(SETTING)).one()
    assert (next_pid, setting) == (pid, str(A))


def test_the_agency_does_not_leak_between_sync_checkouts(
    one_connection_sync_engine: Engine,
) -> None:
    with Session(one_connection_sync_engine) as session:
        bind(session, A)
        pid, setting = session.execute(SETTING).one()
        session.commit()
    with Session(one_connection_sync_engine) as next_session:
        next_pid, next_setting = next_session.execute(SETTING).one()
    assert (setting, next_pid) == (str(A), pid)
    assert next_setting in (None, "")


# ── HTTP dependencies ─────────────────────────────────────────
async def test_v1_requests_are_bound_to_the_default_agency_and_log_it(
    one_connection_engine: AsyncEngine,
) -> None:
    """Until E2-12 every v1 request runs for the default agency. (v2 binds the principal's
    agency since E2-05: tests/identity/test_principal.py.)"""
    import structlog

    from app.platform import request_tenant

    try:
        async with AsyncSession(one_connection_engine) as session:
            structlog.contextvars.clear_contextvars()
            assert await request_tenant.default_agency_db(session) is session
            assert agency_id_of(session) == DEFAULT_AGENCY_ID
            assert structlog.contextvars.get_contextvars() == {"agency_id": str(DEFAULT_AGENCY_ID)}
        assert request_tenant.default_agency_scope() == AccessScope(DEFAULT_AGENCY_ID)
    finally:
        structlog.contextvars.clear_contextvars()
