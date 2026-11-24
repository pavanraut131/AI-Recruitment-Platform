"""Fixtures for the tenant-context and isolation tests (E2-03, E2-04).

``session_for(agency_id)`` opens an extra ``AsyncSession`` on the test's connection (so it sees
what the test's other sessions flushed, and is rolled back with them), bound to that agency, or
unbound with ``None``. Under row-level security a session reads and writes only its agency's
rows, so another agency's rows are arranged through a session bound to it
(``tests/tenant_sessions.py`` keeps each session's statements under its own agency on the shared
connection).

``other_agency`` is a second agency with a client of its own: the agency through ``db_session``
(``agencies`` is a global table, spec §6.4), the client through a session of that agency.
``other_db`` is that session.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import pytest_asyncio
from sqlalchemy.exc import DBAPIError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

# The pipeline's synchronous fixtures (tasks.SyncSession on a rolled-back connection).
from tests.ingestion.conftest import sync_connection, sync_db  # noqa: F401


@dataclass(frozen=True)
class OtherAgency:
    agency_id: uuid.UUID
    client_id: uuid.UUID
    db: AsyncSession  # a session bound to this agency, on the test's connection


SessionFor = Callable[[uuid.UUID | None], AsyncSession]


@pytest_asyncio.fixture
async def session_for(db_connection: AsyncConnection) -> AsyncIterator[SessionFor]:
    from app.platform.tenancy import bind

    opened: list[AsyncSession] = []

    def open_session(agency_id: uuid.UUID | None) -> AsyncSession:
        session = AsyncSession(
            bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        if agency_id is not None:
            bind(session, agency_id)
        opened.append(session)
        return session

    yield open_session
    # Newest first: each one's savepoint may sit inside an older one's. A savepoint already
    # gone with an outer one (a test that rolled back) is fine: the test's outer transaction is
    # rolled back anyway.
    for session in reversed(opened):
        with contextlib.suppress(DBAPIError, InvalidRequestError):
            await session.close()


@pytest_asyncio.fixture
async def other_agency(db_session: AsyncSession, session_for: SessionFor) -> OtherAgency:
    from app.modules.hiring.models import Client
    from app.modules.identity.models import Agency

    agency = Agency(id=uuid.uuid4(), name="Other agency", slug=f"other-{uuid.uuid4().hex[:8]}")
    db_session.add(agency)
    await db_session.flush()
    other_db = session_for(agency.id)
    client = Client(id=uuid.uuid4(), agency_id=agency.id, name="Other client")
    other_db.add(client)
    await other_db.flush()
    return OtherAgency(agency_id=agency.id, client_id=client.id, db=other_db)
