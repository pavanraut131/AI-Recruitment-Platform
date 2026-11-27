"""Fixtures for the identity tests: real sign-in with real cookies (spec §11.1, §11.3).

``auth_client``
    An ``httpx.AsyncClient`` on the app **without** the ``current_principal`` override of the
    shared ``client`` fixture, so ``/api/v2`` authenticates every request from its cookies. Each
    request gets a **fresh, unbound** ``AsyncSession`` on the test's connection (joining its
    outer transaction through a savepoint, like ``db_session``), so ``tenant_db`` binds it to
    the signed-in user's agency, whichever it is, and nothing persists after the test. Data
    arranged through ``db_session`` must be flushed first (the factories do).
``users``
    :class:`Users`: creates users with memberships through ``identity.service`` (the same code
    the operator command uses) and signs them in.

``AUTH_REQUIRED`` is ``true`` here (the default; the test environment does not set it). The
password hash cost stays at its default so the timing tests see real Argon2 work.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.modules.identity.schemas import Role

# Never import ``app`` at the top of a conftest: pytest loads it before the session hook has
# configured the environment (tests/conftest.py).

V2 = "/api/v2"
PASSWORD = "correct horse battery staple"
SESSION_COOKIE = "hf_session"  # APP_ENV=test: not Secure, so no __Host- prefix
CSRF_COOKIE = "hf_csrf"


@pytest_asyncio.fixture
async def auth_client(app: FastAPI, db_connection: AsyncConnection) -> AsyncIterator[AsyncClient]:
    from app.platform.database import get_db
    from tests.conftest import _fastapi_apps

    async def fresh_session() -> AsyncIterator[AsyncSession]:
        session = AsyncSession(
            bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()

    apps = _fastapi_apps(app)
    for each in apps:
        each.dependency_overrides[get_db] = fresh_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        for each in apps:
            each.dependency_overrides.pop(get_db, None)


@dataclass
class Users:
    db: AsyncSession
    client: AsyncClient
    created: list[uuid.UUID] = field(default_factory=list)

    async def agency(self, name: str = "Northbank Talent") -> uuid.UUID:
        from app.modules.identity import service

        agency = await service.create_agency(self.db, name)
        await self.db.flush()
        return agency.id

    async def user(
        self,
        email: str,
        memberships: dict[uuid.UUID, Role] | None = None,
        *,
        password: str = PASSWORD,
    ) -> uuid.UUID:
        """A user with these memberships (default: owner of the default agency)."""
        from app.modules.identity import service
        from app.modules.identity.schemas import Role
        from app.platform.tenancy import DEFAULT_AGENCY_ID

        user = await service.ensure_user(
            self.db, email, display_name=email.split("@")[0], password=password
        )
        if memberships is None:
            memberships = {DEFAULT_AGENCY_ID: Role.owner}
        for agency_id, role in memberships.items():
            await service.ensure_membership(self.db, agency_id, user.id, role)
        await self.db.flush()
        return user.id

    async def login(
        self, email: str, password: str = PASSWORD, client: AsyncClient | None = None
    ) -> httpx.Response:
        """``GET /auth/csrf`` then ``POST /auth/login`` with the token, as a browser would."""
        ac = client or self.client
        token = (await ac.get(f"{V2}/auth/csrf")).json()["csrf_token"]
        return await ac.post(
            f"{V2}/auth/login",
            json={"email": email, "password": password},
            headers={"X-CSRF-Token": token},
        )


@pytest_asyncio.fixture
async def users(db_session: AsyncSession, auth_client: AsyncClient) -> Users:
    return Users(db_session, auth_client)


def csrf(client: AsyncClient) -> dict[str, str]:
    """The ``X-CSRF-Token`` header for the client's current CSRF cookie."""
    return {"X-CSRF-Token": client.cookies.get(CSRF_COOKIE) or ""}


def set_cookie_headers(resp: httpx.Response) -> dict[str, str]:
    """``Set-Cookie`` headers by cookie name."""
    out: dict[str, str] = {}
    for header in resp.headers.get_list("set-cookie"):
        out[header.split("=", 1)[0]] = header
    return out


def problem(resp: httpx.Response) -> dict[str, Any]:
    assert resp.headers["content-type"] == "application/problem+json", resp.text
    body: dict[str, Any] = resp.json()
    return body
