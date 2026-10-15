"""Session-wide Postgres for the test suite: a testcontainers-managed ``pgvector/pgvector:pg16``.

Used by the ``pytest_sessionstart`` / ``pytest_sessionfinish`` hooks in ``tests/conftest.py``.

Why a hook and not a session fixture: ``app.platform.config`` builds ``Settings()`` at import time
(``DATABASE_URL`` and ``SYNC_DATABASE_URL`` are required) and ``app.platform.database`` creates
the async engine from ``settings.DATABASE_URL`` at import time. So the container must be running
and the environment must point at it *before anything imports ``app``*, which includes
test-module collection. ``pytest_sessionstart`` runs before collection; a session fixture would
run after it.

Readiness (regression note, E0-13)
    A session once failed in ``alembic upgrade head`` with ``psycopg2.OperationalError:
    connection to server at "127.0.0.1", port NNNNN failed: server closed the connection
    unexpectedly``. A *running* container (liveness) is not yet a Postgres that answers queries
    on the host port (readiness). There are two gaps between the two:

    1. The image's entrypoint initialises the data directory with a temporary server, stops it
       and then starts the real one. testcontainers 4.15 already covers this: it waits until
       ``psql --host 127.0.0.1 -c 'select version();'`` succeeds *inside* the container, and the
       temporary server listens on the Unix socket only (``listen_addresses=''`` in the image's
       ``docker-entrypoint.sh``), so that check cannot pass before the restart.
    2. That check never uses the path the tests use: the published port on the host. On Docker
       Desktop (Windows, macOS) the host end of a published port is a proxy, which can accept a
       TCP connection and then drop it while forwarding is not in place yet. libpq reports
       exactly the error above.

    So after ``container.start()``, :func:`wait_until_accepting_queries` opens a *fresh*
    connection through the host port and runs a query, retrying until that succeeds
    :data:`READY_CONSECUTIVE_SUCCESSES` times in a row (one lucky connection is not stable),
    within :data:`READY_TIMEOUT_S`. If it never does, the session stops with a message naming
    the last error, instead of failing somewhere inside Alembic.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg2
import pytest
from testcontainers.community.postgres import PostgresContainer

if TYPE_CHECKING:
    from sqlalchemy import Engine

POSTGRES_IMAGE = "pgvector/pgvector:pg16"  # same image as docker-compose.yml

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

# Host-side readiness check (see the module docstring).
READY_TIMEOUT_S = 60.0
READY_CONSECUTIVE_SUCCESSES = 3
READY_RETRY_INTERVAL_S = 0.2
READY_CONNECT_TIMEOUT_S = 3

# Settings forced for the whole test session. They override the shell and any backend/.env
# (pydantic-settings gives environment variables priority over the env file), so a developer's
# local configuration can never leak into tests. Everything that could reach the network points
# at something inert: Celery uses the in-memory transport (dispatch is stubbed anyway), Redis and
# MinIO point at a closed local port so accidental use fails fast instead of hanging.
STATIC_TEST_ENV = {
    "APP_ENV": "test",
    "DEBUG": "false",  # also keeps SQLAlchemy echo off
    # The suite runs the default provider (gemini) on top of the FakeGemini fixture, and Settings
    # requires a key for it. A dummy: no test can reach the Gemini API.
    "AI_PROVIDER": "gemini",
    "GEMINI_API_KEY": "test-dummy-gemini-key",
    "CELERY_BROKER_URL": "memory://",
    "CELERY_RESULT_BACKEND": "cache+memory://",
    "REDIS_URL": "redis://127.0.0.1:1/0",
    "MINIO_ENDPOINT": "127.0.0.1:1",
}

_container: PostgresContainer | None = None


class PostgresNotReadyError(RuntimeError):
    """Postgres did not answer queries on the host port within the readiness timeout."""


def wait_until_accepting_queries(
    dsn: str,
    *,
    timeout_s: float = READY_TIMEOUT_S,
    consecutive: int = READY_CONSECUTIVE_SUCCESSES,
    interval_s: float = READY_RETRY_INTERVAL_S,
) -> int:
    """Block until ``SELECT 1`` succeeds on ``consecutive`` fresh connections in a row.

    Every attempt opens a new connection (a connection opened before a server restart proves
    nothing about the server that replaced it), and any failure resets the streak. Returns the
    number of attempts made. Raises :class:`PostgresNotReadyError`, carrying the last error, once
    ``timeout_s`` has passed.
    """
    deadline = time.monotonic() + timeout_s
    attempts = 0
    streak = 0
    last_error: Exception | None = None
    while True:
        attempts += 1
        try:
            conn = psycopg2.connect(dsn, connect_timeout=READY_CONNECT_TIMEOUT_S)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            finally:
                conn.close()
        except psycopg2.Error as exc:
            streak = 0
            last_error = exc
        else:
            streak += 1
            if streak >= consecutive:
                return attempts
        if time.monotonic() >= deadline:
            raise PostgresNotReadyError(
                f"Postgres did not accept queries on {consecutive} consecutive fresh connections "
                f"within {timeout_s:g}s ({attempts} attempts). Last error: {last_error!r}"
            )
        time.sleep(interval_s)


def start() -> None:
    """Start the container, wait until it answers queries on the host port, export the env vars
    ``Settings`` needs, and migrate to head."""
    global _container
    if "app" in sys.modules:  # guard against misuse: too late to configure Settings
        raise RuntimeError("tests.postgres.start() must run before `app` is imported")

    container = PostgresContainer(
        POSTGRES_IMAGE, username="test", password="test", dbname="test", driver=None
    )
    try:
        container.start()
        # 127.0.0.1 rather than "localhost": on Windows "localhost" may resolve to ::1 first.
        host = container.get_container_host_ip().replace("localhost", "127.0.0.1")
        port = container.get_exposed_port(5432)
        auth = f"{container.username}:{container.password}@{host}:{port}/{container.dbname}"
        wait_until_accepting_queries(f"postgresql://{auth}")
    except Exception as exc:  # docker not running / image pull failed / never became ready
        with contextlib.suppress(Exception):  # don't leave a half-started container behind
            container.stop()
        pytest.exit(
            f"Could not start the test Postgres container ({POSTGRES_IMAGE}). "
            f"The test suite needs Docker running. Cause: {exc!r}",
            returncode=3,
        )
    _container = container

    os.environ.update(STATIC_TEST_ENV)
    os.environ["DATABASE_URL"] = f"postgresql+asyncpg://{auth}"
    os.environ["SYNC_DATABASE_URL"] = f"postgresql+psycopg2://{auth}"

    migrate_to_head()


def migrate_to_head() -> None:
    """Run ``alembic upgrade head`` against the (empty) container, exactly as the CLI would.

    ``alembic/env.py`` takes the URL from ``settings.SYNC_DATABASE_URL``, which is why this runs
    after the environment is set. Applying every migration to an empty database on each run also
    proves the migration chain is valid.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    # script_location in alembic.ini is relative to the working directory; make it absolute so
    # pytest can be started from anywhere.
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")


@contextlib.contextmanager
def throwaway_database(prefix: str, **engine_options: Any) -> Iterator[Engine]:
    """A new database in the session's container, migrated to ``head``, dropped afterwards.

    For tests that must **commit** on several connections (concurrency, the backfill), which the
    rolled-back ``db_session`` / ``sync_session`` cannot give. The session database is never
    touched. ``engine_options`` go to ``create_engine`` (e.g. ``pool_size``).
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from app.platform.config import settings

    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    base_url = make_url(settings.SYNC_DATABASE_URL)
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = base_url.set(database=name).render_as_string(hide_password=False)

    original = settings.SYNC_DATABASE_URL
    settings.SYNC_DATABASE_URL = url  # alembic/env.py reads it
    try:
        cfg = Config()  # no ini file: env.py would reconfigure logging
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        settings.SYNC_DATABASE_URL = original

    engine = create_engine(url, **engine_options)
    try:
        yield engine
    finally:
        engine.dispose()
        # Suppressed: after `pytest -x` the session hook may already have stopped the container
        # (and the database with it); that must not hide the test failure.
        with contextlib.suppress(Exception), admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def stop() -> None:
    """Stop and remove the container this session started (and only that one)."""
    global _container
    if _container is not None:
        _container.stop()
        _container = None
