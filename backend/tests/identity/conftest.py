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
    the operator command uses), limits a membership to some clients, and signs them in.
``mailbox`` (E2-07)
    :class:`Mailbox`: the emails the code queued, delivered through the real
    ``identity.send_email`` task with the console backend; ``mailbox.token(to)`` reads the
    token from the last link sent to an address.
``browser`` (E2-07)
    A second client with cookies of its own (the invitee next to the signed-in owner).

``AUTH_REQUIRED`` is ``true`` here (the default; the test environment does not set it). The
password hash cost stays at its default so the timing tests see real Argon2 work.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from tests.fakes import CeleryRecorder

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.modules.identity.schemas import Role
    from app.platform.email import EmailMessage

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

    async def limit(
        self,
        user_id: uuid.UUID,
        client_ids: list[uuid.UUID],
        agency_id: uuid.UUID | None = None,
    ) -> None:
        """Limit the user's membership in the agency (default: the default agency) to these
        clients (``all_clients`` false, spec §5.3; recruiters and viewers only). Written
        directly, as the team endpoints (E2-07) will."""
        from sqlalchemy import select

        from app.modules.identity.models import Membership, MembershipClient
        from app.platform.tenancy import DEFAULT_AGENCY_ID

        membership = await self.db.scalar(
            select(Membership).where(
                Membership.agency_id == (agency_id or DEFAULT_AGENCY_ID),
                Membership.user_id == user_id,
            )
        )
        assert membership is not None
        membership.all_clients = False
        self.db.add_all(
            MembershipClient(membership_id=membership.id, client_id=client_id)
            for client_id in client_ids
        )
        await self.db.flush()

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


# ── E2-07: emails, a second browser ───────────────────────────
TOKEN_IN_LINK = re.compile(r"#token=([A-Za-z0-9_-]+)")


@dataclass
class Mailbox:
    """The emails the code under test queued (``identity.send_email`` calls, recorded by the
    ``celery_calls`` fixture), delivered through the real task with the console backend
    (``app.platform.email.outbox``)."""

    calls: CeleryRecorder
    delivered: int = 0

    def messages(self) -> list[EmailMessage]:
        """Deliver what was queued since the last call; every message delivered so far."""
        from app.modules.identity import tasks
        from app.platform import email

        queued = self.calls.for_task(tasks.SEND_EMAIL_TASK)
        for call in queued[self.delivered :]:
            tasks.send_email(**call.kwargs)  # runs the task body (console backend)
        self.delivered = len(queued)
        return list(email.outbox)

    def last(self, to: str) -> EmailMessage:
        sent = [m for m in self.messages() if m.to == to]
        assert sent, f"no email to {to}"
        return sent[-1]

    def count(self, to: str) -> int:
        return sum(1 for m in self.messages() if m.to == to)

    def token(self, to: str) -> str:
        """The token in the link of the last email to ``to``."""
        match = TOKEN_IN_LINK.search(self.last(to).text)
        assert match, "no #token= link in the email"
        return match.group(1)


@pytest.fixture
def mailbox(celery_calls: CeleryRecorder) -> Iterator[Mailbox]:
    from app.platform import email

    email.outbox.clear()
    yield Mailbox(celery_calls)
    email.outbox.clear()


@pytest_asyncio.fixture
async def browser(app: FastAPI, auth_client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """Another browser (its own cookies) on the same app and database as ``auth_client``."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def anonymous_csrf(client: AsyncClient) -> dict[str, str]:
    """``GET /auth/csrf`` (as the auth pages do) and the header with its token."""
    token = (await client.get(f"{V2}/auth/csrf")).json()["csrf_token"]
    return {"X-CSRF-Token": token}
