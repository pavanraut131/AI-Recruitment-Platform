"""The start-up check (EPIC-02 spec §6.2, §10): the API and the Celery workers refuse to run as a
database user that row-level security would not restrict (a superuser, a ``BYPASSRLS`` role, a
table owner), with a message that says what to do."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.platform import celery_app
from app.platform.config import settings
from app.platform.rls import UnsafeDatabaseRole, check_app_role
from tests import postgres


def _engine(url: str) -> Engine:
    return create_engine(url, poolclass=NullPool)


@pytest.fixture
def bypass_role_url() -> Iterator[str]:
    """A throwaway login role with BYPASSRLS (created by the container's superuser)."""
    assert postgres._admin_url is not None
    admin = create_engine(postgres._admin_url, isolation_level="AUTOCOMMIT")
    name = f"bypass_{uuid.uuid4().hex[:8]}"
    with admin.connect() as conn:
        conn.execute(text(f"CREATE ROLE {name} LOGIN BYPASSRLS PASSWORD 'bypass'"))
    url = make_url(settings.SYNC_DATABASE_URL).set(username=name, password="bypass")
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP ROLE IF EXISTS {name}"))
        admin.dispose()


def test_the_app_role_passes() -> None:
    engine = _engine(settings.SYNC_DATABASE_URL)
    with engine.connect() as conn:
        assert check_app_role(conn, "the API") == "hireflow_app"
    engine.dispose()


@pytest.mark.parametrize(
    ("which", "reason"),
    [
        ("owner", "hireflow_owner owns, or is a member of the owner of, agencies"),
        ("superuser", "test is a superuser"),
        ("bypass", "has BYPASSRLS"),
    ],
)
def test_other_roles_are_refused(which: str, reason: str, bypass_role_url: str) -> None:
    assert postgres._admin_url is not None
    url = {
        "owner": settings.migration_database_url,
        "superuser": postgres._admin_url,
        "bypass": bypass_role_url,
    }[which]
    engine = _engine(url)
    with engine.connect() as conn, pytest.raises(UnsafeDatabaseRole) as refused:
        check_app_role(conn, "the API")
    engine.dispose()
    message = str(refused.value)
    assert message.startswith("Refusing to start the API")
    assert reason in message
    assert "Connect as hireflow_app" in message


async def test_the_api_does_not_start_as_the_owner(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import main
    from app.platform import database

    owner = create_async_engine(
        settings.migration_database_url.replace("postgresql+psycopg2", "postgresql+asyncpg"),
        poolclass=NullPool,
    )
    monkeypatch.setattr(database, "engine", owner)
    try:
        with pytest.raises(UnsafeDatabaseRole):
            async with main.lifespan(app):
                pytest.fail("the API started")
    finally:
        await owner.dispose()


async def test_the_api_starts_as_the_app_role(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import main
    from app.platform import database

    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    monkeypatch.setattr(database, "engine", engine)
    started = False
    try:
        async with main.lifespan(app):
            started = True
    finally:
        await engine.dispose()
    assert started


def test_a_worker_does_not_start_as_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SYNC_DATABASE_URL", settings.migration_database_url)
    with pytest.raises(SystemExit) as stopped:
        celery_app._refuse_unsafe_database_role()
    assert "Refusing to start the Celery worker" in str(stopped.value.code)


def test_a_worker_starts_as_the_app_role() -> None:
    celery_app._refuse_unsafe_database_role()  # no exception
