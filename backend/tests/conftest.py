"""Shared test harness: real Postgres in Docker, one rolled-back transaction per test.

The suite needs only Docker. No Redis, MinIO, Celery broker or Gemini network access.

Session setup (hooks, see ``tests/postgres.py``)
    ``pytest_sessionstart`` starts ``pgvector/pgvector:pg16`` with testcontainers, exports
    ``DATABASE_URL`` / ``SYNC_DATABASE_URL`` / ``GEMINI_API_KEY`` and inert values for the other
    services, then runs ``alembic upgrade head`` once. ``pytest_sessionfinish`` stops the
    container. This happens in hooks, before collection, because ``app`` reads its settings and
    builds its engine at import time. **Never import ``app`` at the top of this file**; test
    modules may import it, since collection runs after ``pytest_sessionstart``.

Fixtures
    ``db_engine`` (session)
        Async engine on the test database. ``NullPool``: pytest-asyncio gives each test its own
        event loop and asyncpg connections cannot move between loops.
    ``db_session`` (function)
        ``AsyncSession`` bound to a connection with an open outer transaction, using
        ``join_transaction_mode="create_savepoint"``. ``commit()`` in code under test only
        releases a savepoint; the outer transaction is rolled back after the test, so nothing
        persists between tests. Use it in tests to arrange data and to assert on the database.
    ``app`` (session)
        The FastAPI application (imported lazily).
    ``client`` (function)
        ``httpx.AsyncClient`` on ``ASGITransport(app=app)``, base URL ``http://test``. Overrides
        ``get_db`` so requests use ``db_session`` (the same session and transaction as the test).
        ASGITransport does not send lifespan events, so the app's startup hook
        (``ensure_bucket_exists``) does not run.
    ``celery_calls`` (function, autouse)
        :class:`tests.fakes.CeleryRecorder`. ``Task.apply_async`` is patched on Celery's base
        ``Task`` class, which covers ``delay()``, ``apply_async()`` and signatures of every
        project task, whatever module they were imported into. Nothing is sent to a broker.
    ``fake_storage`` (function, autouse)
        :class:`tests.fakes.InMemoryStorage`. Replaces ``upload_file``, ``download_file``,
        ``delete_file``, ``get_presigned_url`` and ``ensure_bucket_exists`` in
        ``app.platform.storage`` (and the direct import in ``app.main``). ``get_s3_client`` is
        made to raise, so any path that bypasses the fake fails loudly instead of reaching the
        network. Patch where the code looks the names up: the ``app.core.storage`` shim only
        re-binds them at import, so patching it would not reach the app.
    ``fake_redis`` (function, autouse)
        :class:`tests.fakes.FakeRedis`. ``redis.Redis.from_url`` (``/health/ready``, the workers'
        ``_publish_sse_event``) and ``redis.asyncio.Redis.from_url`` (the SSE stream) return
        fakeredis clients on one in-memory server per test, so a sync ``publish`` reaches an async
        subscriber. Set ``fake_redis.server.connected = False`` to simulate Redis being down.
    ``fake_gemini`` (function, autouse)
        :class:`tests.fakes.FakeGemini`. Replaces ``google.generativeai.GenerativeModel`` (used by
        ``/campaigns/parse-jd`` and the OCR fallback) and the module-level model in
        ``app.services.gemini``. Calls raise until the test scripts a response with
        ``fake_gemini.respond_with(...)``; no test can reach the Gemini API.

Test data
    ``tests/factories.py`` builds campaigns, resumes and candidates through ``db_session``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from tests import postgres
from tests.fakes import CeleryRecorder, FakeGemini, FakeRedis, InMemoryStorage

if TYPE_CHECKING:
    from fastapi import FastAPI


# ── Session hooks ─────────────────────────────────────────────
def pytest_sessionstart(session: pytest.Session) -> None:
    postgres.start()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    postgres.stop()


# ── Database ──────────────────────────────────────────────────
@pytest.fixture(scope="session")
def db_engine() -> AsyncEngine:
    from app.platform.config import settings

    return create_async_engine(settings.DATABASE_URL, poolclass=NullPool)


@pytest_asyncio.fixture
async def db_connection(db_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """A connection inside an outer transaction that is always rolled back."""
    async with db_engine.connect() as conn:
        outer = await conn.begin()
        try:
            yield conn
        finally:
            if outer.is_active:
                await outer.rollback()


@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    session = AsyncSession(
        bind=db_connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,  # same as app.platform.database.AsyncSessionLocal
    )
    try:
        yield session
    finally:
        await session.close()


# ── App and HTTP client ───────────────────────────────────────
@pytest.fixture(scope="session")
def app() -> FastAPI:
    from app.main import app as fastapi_app

    return fastapi_app


@pytest_asyncio.fixture
async def client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    from app.platform.database import get_db

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── External services ─────────────────────────────────────────
@pytest.fixture(autouse=True)
def celery_calls(monkeypatch: pytest.MonkeyPatch) -> CeleryRecorder:
    from celery.app.task import Task

    recorder = CeleryRecorder()

    def fake_apply_async(
        self: Task, args: Any = None, kwargs: Any = None, task_id: str | None = None, **options: Any
    ) -> Any:
        return recorder.record(self.name, args, kwargs, task_id, **options)

    # Make sure every project task is registered, then patch the shared base class.
    import workers.ai_task  # noqa: F401
    import workers.extract_task  # noqa: F401

    monkeypatch.setattr(Task, "apply_async", fake_apply_async)
    return recorder


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> InMemoryStorage:
    from app import main as app_main
    from app.platform import storage
    from app.platform.config import settings

    fake = InMemoryStorage(bucket=settings.MINIO_BUCKET_NAME)
    for name in (
        "ensure_bucket_exists",
        "upload_file",
        "download_file",
        "delete_file",
        "get_presigned_url",
    ):
        monkeypatch.setattr(storage, name, getattr(fake, name))
    monkeypatch.setattr(app_main, "ensure_bucket_exists", fake.ensure_bucket_exists)

    def no_real_s3() -> None:
        raise RuntimeError("tests must not reach real object storage; use the fake_storage fixture")

    monkeypatch.setattr(storage, "get_s3_client", no_real_s3)
    return fake


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    import redis
    import redis.asyncio

    fake = FakeRedis()
    # Plain functions on the class: `redis.Redis.from_url(url, **kw)` calls them unbound.
    monkeypatch.setattr(redis.Redis, "from_url", fake.sync_client)
    monkeypatch.setattr(redis.asyncio.Redis, "from_url", fake.async_client)
    return fake


@pytest.fixture(autouse=True)
def fake_gemini(monkeypatch: pytest.MonkeyPatch) -> FakeGemini:
    import google.generativeai as genai

    from app.platform.config import settings
    from app.services import gemini

    fake = FakeGemini()
    monkeypatch.setattr(genai, "GenerativeModel", fake.GenerativeModel)
    monkeypatch.setattr(gemini, "_model", fake.GenerativeModel(settings.GEMINI_MODEL))
    return fake
