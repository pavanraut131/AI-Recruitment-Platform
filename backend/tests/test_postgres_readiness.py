"""The host-side readiness wait in ``tests/postgres.py`` (E0-13 start-up flake).

``container.start()`` returning is liveness; the wait turns it into readiness: a query must
succeed on several fresh connections in a row through the published port before migrations run.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg2
import pytest

from app.platform.config import settings
from tests import postgres

# Mirrors libpq's message from the original flake.
RESTART_ERROR = psycopg2.OperationalError(
    'connection to server at "127.0.0.1", port 55432 failed: '
    "server closed the connection unexpectedly"
)


class _Cursor:
    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str) -> None:
        assert sql == "SELECT 1"

    def fetchone(self) -> tuple[int]:
        return (1,)


class _Connection:
    def __init__(self) -> None:
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor()

    def close(self) -> None:
        self.closed = True


class ScriptedConnect:
    """Stands in for ``psycopg2.connect``: each call takes the next outcome from a script."""

    def __init__(self, outcomes: list[bool]) -> None:
        self._outcomes: Iterator[bool] = iter(outcomes)
        self.calls = 0
        self.connections: list[_Connection] = []

    def __call__(self, dsn: str, **kwargs: Any) -> _Connection:
        self.calls += 1
        assert kwargs["connect_timeout"] == postgres.READY_CONNECT_TIMEOUT_S
        if not next(self._outcomes, False):
            raise RESTART_ERROR
        conn = _Connection()
        self.connections.append(conn)
        return conn


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(postgres.time, "sleep", lambda _s: None)


@pytest.mark.usefixtures("no_sleep")
def test_retries_through_the_restart_window_until_stable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Refused, accepted once (temporary server / proxy), dropped again, then stable.
    connect = ScriptedConnect([False, False, True, False, True, True, True])
    monkeypatch.setattr(postgres.psycopg2, "connect", connect)

    attempts = postgres.wait_until_accepting_queries("postgresql://t", consecutive=3)

    # The lone success at attempt 3 did not count: the failure after it reset the streak.
    assert attempts == connect.calls == 7
    assert all(conn.closed for conn in connect.connections)  # every probe used a fresh one


@pytest.mark.usefixtures("no_sleep")
def test_gives_up_after_the_timeout_with_the_last_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(postgres.psycopg2, "connect", ScriptedConnect([]))
    clock = iter(range(0, 1000, 10))  # every monotonic() call advances 10 s
    monkeypatch.setattr(postgres.time, "monotonic", lambda: float(next(clock)))

    with pytest.raises(postgres.PostgresNotReadyError) as excinfo:
        postgres.wait_until_accepting_queries("postgresql://t", timeout_s=30, consecutive=3)

    message = str(excinfo.value)
    assert "3 consecutive fresh connections within 30s" in message
    assert "server closed the connection unexpectedly" in message


def test_session_database_passes_the_check() -> None:
    dsn = settings.SYNC_DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")

    assert postgres.wait_until_accepting_queries(dsn, timeout_s=10) == (
        postgres.READY_CONSECUTIVE_SUCCESSES
    )
