"""Synchronous database fixtures for talent: ``resolve_person`` takes a sync ``Session``.

``sync_session`` mirrors ``db_session`` in ``tests/conftest.py``: a ``Session`` on a connection
with an outer transaction that is rolled back after the test. ``Session.commit()`` and the
savepoints ``resolve_person`` opens stay inside it, so nothing persists. Tests that must commit
(the concurrency tests) use their own database instead, see ``test_resolve_person_concurrency``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool


@pytest.fixture(scope="session")
def sync_engine() -> Iterator[Engine]:
    from app.platform.config import settings

    engine = create_engine(settings.SYNC_DATABASE_URL, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def sync_session(sync_engine: Engine) -> Iterator[Session]:
    with sync_engine.connect() as conn:
        outer = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            if outer.is_active:
                outer.rollback()
