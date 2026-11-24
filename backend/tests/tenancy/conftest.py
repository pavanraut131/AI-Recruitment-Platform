"""Fixtures for the tenant-context tests (E2-03).

``other_agency`` is a second agency with a client of its own, created through ``db_session``
(``agencies`` and ``clients`` are not filtered by agency). ``session_for(agency_id)`` opens an
extra ``AsyncSession`` on the test's connection (so it sees what ``db_session`` flushed, and is
rolled back with it), bound to that agency, or unbound with ``None``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

# The pipeline's synchronous fixtures (tasks.SyncSession on a rolled-back connection).
from tests.ingestion.conftest import sync_connection, sync_db  # noqa: F401


@dataclass(frozen=True)
class OtherAgency:
    agency_id: uuid.UUID
    client_id: uuid.UUID


@pytest_asyncio.fixture
async def other_agency(db_session: AsyncSession) -> OtherAgency:
    from app.modules.hiring.models import Client
    from app.modules.identity.models import Agency

    agency = Agency(id=uuid.uuid4(), name="Other agency", slug=f"other-{uuid.uuid4().hex[:8]}")
    db_session.add(agency)
    await db_session.flush()
    client = Client(id=uuid.uuid4(), agency_id=agency.id, name="Other client")
    db_session.add(client)
    await db_session.flush()
    return OtherAgency(agency_id=agency.id, client_id=client.id)


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
    for session in opened:
        await session.close()
