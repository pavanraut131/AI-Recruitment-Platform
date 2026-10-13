"""Session-wide Postgres for the test suite: a testcontainers-managed ``pgvector/pgvector:pg16``.

Used by the ``pytest_sessionstart`` / ``pytest_sessionfinish`` hooks in ``tests/conftest.py``.

Why a hook and not a session fixture: ``app.core.config`` builds ``Settings()`` at import time
(``DATABASE_URL``, ``SYNC_DATABASE_URL`` and ``GEMINI_API_KEY`` are required) and
``app.core.database`` creates the async engine from ``settings.DATABASE_URL`` at import time.
So the container must be running and the environment must point at it *before anything imports
``app``*, which includes test-module collection. ``pytest_sessionstart`` runs before collection;
a session fixture would run after it.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

import pytest
from testcontainers.community.postgres import PostgresContainer

POSTGRES_IMAGE = "pgvector/pgvector:pg16"  # same image as docker-compose.yml

BACKEND_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

# Settings forced for the whole test session. They override the shell and any backend/.env
# (pydantic-settings gives environment variables priority over the env file), so a developer's
# local configuration can never leak into tests. Everything that could reach the network points
# at something inert: Celery uses the in-memory transport (dispatch is stubbed anyway), Redis and
# MinIO point at a closed local port so accidental use fails fast instead of hanging.
STATIC_TEST_ENV = {
    "APP_ENV": "test",
    "DEBUG": "false",  # also keeps SQLAlchemy echo off
    "GEMINI_API_KEY": "test-dummy-gemini-key",  # genai.configure() at import needs a value only
    "CELERY_BROKER_URL": "memory://",
    "CELERY_RESULT_BACKEND": "cache+memory://",
    "REDIS_URL": "redis://127.0.0.1:1/0",
    "MINIO_ENDPOINT": "127.0.0.1:1",
}

_container: PostgresContainer | None = None


def start() -> None:
    """Start the container, export the env vars ``Settings`` needs, and migrate to head."""
    global _container
    if "app" in sys.modules:  # guard against misuse: too late to configure Settings
        raise RuntimeError("tests.postgres.start() must run before `app` is imported")

    container = PostgresContainer(
        POSTGRES_IMAGE, username="test", password="test", dbname="test", driver=None
    )
    try:
        container.start()
    except Exception as exc:  # docker not running / image pull failed / never became ready
        with contextlib.suppress(Exception):  # don't leave a half-started container behind
            container.stop()
        pytest.exit(
            f"Could not start the test Postgres container ({POSTGRES_IMAGE}). "
            f"The test suite needs Docker running. Cause: {exc!r}",
            returncode=3,
        )
    _container = container

    # 127.0.0.1 rather than "localhost": on Windows "localhost" may resolve to ::1 first.
    host = container.get_container_host_ip().replace("localhost", "127.0.0.1")
    port = container.get_exposed_port(5432)
    auth = f"{container.username}:{container.password}@{host}:{port}/{container.dbname}"

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


def stop() -> None:
    """Stop and remove the container this session started (and only that one)."""
    global _container
    if _container is not None:
        _container.stop()
        _container = None
