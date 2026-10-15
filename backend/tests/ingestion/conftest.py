"""Fixtures for running the upload pipeline's Celery tasks in-process.

The tasks open their own synchronous sessions (``tasks.SyncSession``). ``sync_db`` replaces that
factory by one bound to a single connection inside an outer transaction that is rolled back after
the test (``join_transaction_mode="create_savepoint"``, like ``db_session``), so the tasks'
commits never persist. Test data is arranged, and results read, through the same connection.

``task.apply()`` runs a task eagerly; a ``self.retry(...)`` inside it is re-run at once, inline
(Celery ignores the countdown in eager mode), so one ``apply()`` covers the task and its retries.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import Connection, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from tests.fakes import FakeRedis

# No ``app`` import at module level: this conftest is loaded before the session hook that
# configures the app's settings (see tests/conftest.py), so the fixtures import it lazily.


@pytest.fixture
def sync_connection() -> Iterator[Connection]:
    from app.platform.config import settings

    engine = create_engine(settings.SYNC_DATABASE_URL, poolclass=NullPool)
    with engine.connect() as conn:
        outer = conn.begin()
        try:
            yield conn
        finally:
            outer.rollback()
    engine.dispose()


@pytest.fixture
def sync_db(sync_connection: Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    from app.modules.ingestion import tasks

    factory = sessionmaker(bind=sync_connection, join_transaction_mode="create_savepoint")
    monkeypatch.setattr(tasks, "SyncSession", factory)
    with factory() as session:
        yield session


Events = Callable[[], list[dict[str, Any]]]


@pytest.fixture
def events(fake_redis: FakeRedis) -> Iterator[Events]:
    """Call it to get every payload published on the SSE channel so far in the test."""
    from app.modules.ingestion.events import ALL_UPDATES_CHANNEL

    pubsub = fake_redis.sync_client().pubsub()
    pubsub.subscribe(ALL_UPDATES_CHANNEL)
    received: list[dict[str, Any]] = []

    def drain() -> list[dict[str, Any]]:
        # (Not ignore_subscribe_messages: that returns None for the subscribe confirmation.)
        while (message := pubsub.get_message()) is not None:
            if message["type"] == "message":
                received.append(json.loads(message["data"]))
        return received

    yield drain
    pubsub.close()
