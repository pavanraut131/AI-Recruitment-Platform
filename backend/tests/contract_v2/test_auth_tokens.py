"""Contract: the anonymous endpoints that take an emailed token or address (E2-07): invitation
preview and accept, password reset request and confirm, and the emails they send (invitation,
reset link, "password changed"). Tokens in links become ``<token>`` and the invitation's expiry
date ``<date>``, on top of the usual masking."""

from __future__ import annotations

import re
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text, update
from syrupy.assertion import SnapshotAssertion

from app.modules.identity.models import Invitation
from app.modules.identity.schemas import Role
from app.platform.email import EmailMessage
from tests.contract_v2.test_identity import _data
from tests.default_agency import DEFAULT_AGENCY_ID
from tests.identity.conftest import (  # noqa: F401 (fixtures)
    V2,
    Mailbox,
    Users,
    anonymous_csrf,
    browser,
    csrf,
    mailbox,
)

TOKEN = re.compile(r"#token=[A-Za-z0-9_-]+")
EXPIRY = re.compile(r"expires on \d{1,2} \w+ \d{4}")


def _email(message: EmailMessage) -> dict[str, str]:
    def mask(value: str) -> str:
        return EXPIRY.sub("expires on <date>", TOKEN.sub("#token=<token>", value))

    return {
        "to": message.to,
        "subject": message.subject,
        "text": mask(message.text),
        "html": mask(message.html),
    }


async def _post(client: AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    headers = await anonymous_csrf(client)
    return _data(await client.post(f"{V2}{path}", json=body, headers=headers), "retry-after")


async def test_invitation_preview_and_accept(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,  # noqa: F811
    mailbox: Mailbox,  # noqa: F811
    snapshot: SnapshotAssertion,
) -> None:
    await users.user("owner@example.com")
    await users.user("someone@example.com", {DEFAULT_AGENCY_ID: Role.viewer})
    await users.login("owner@example.com")
    for email in ("nina@example.com", "someone.else@example.com"):
        resp = await auth_client.post(
            f"{V2}/team/invitations",
            json={"email": email, "role": "recruiter"},
            headers=csrf(auth_client),
        )
        assert resp.status_code == 201
    token = mailbox.token("nina@example.com")
    responses: dict[str, Any] = {
        "email": _email(mailbox.last("nina@example.com")),
        "preview": await _post(browser, "/auth/invitations/preview", {"token": token}),
        "preview_unknown": await _post(browser, "/auth/invitations/preview", {"token": "x" * 43}),
        "accept_without_name": await _post(
            browser, "/auth/invitations/accept", {"token": token, "password": "x" * 16}
        ),
        "accept_short_password": await _post(
            browser,
            "/auth/invitations/accept",
            {"token": token, "display_name": "Nina", "password": "short"},
        ),
        "accept": await _post(
            browser,
            "/auth/invitations/accept",
            {"token": token, "display_name": "Nina Shah", "password": "nina's long passphrase"},
        ),
        "accept_again": await _post(
            browser,
            "/auth/invitations/accept",
            {"token": token, "display_name": "Nina", "password": "x" * 16},
        ),
    }
    # Signed in as someone else.
    await users.login("someone@example.com", client=browser)
    other = mailbox.token("someone.else@example.com")
    responses["accept_signed_in_as_someone_else"] = _data(
        await browser.post(
            f"{V2}/auth/invitations/accept", json={"token": other}, headers=csrf(browser)
        )
    )
    # An expired invitation.
    await users.db.execute(update(Invitation).values(expires_at=text("now() - interval '1 s'")))
    await users.db.flush()
    responses["preview_expired"] = await _post(
        browser, "/auth/invitations/preview", {"token": other}
    )
    assert responses == snapshot


async def test_password_reset(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,  # noqa: F811
    mailbox: Mailbox,  # noqa: F811
    snapshot: SnapshotAssertion,
) -> None:
    await users.user("ada@example.com")
    responses: dict[str, Any] = {
        "request": await _post(browser, "/auth/password-reset", {"email": "ada@example.com"}),
        "request_unknown": await _post(
            browser, "/auth/password-reset", {"email": "nobody@example.com"}
        ),
    }
    token = mailbox.token("ada@example.com")
    responses["reset_email"] = _email(mailbox.last("ada@example.com"))
    responses["confirm_weak"] = await _post(
        browser, "/auth/password-reset/confirm", {"token": token, "new_password": "short"}
    )
    responses["confirm"] = await _post(
        browser,
        "/auth/password-reset/confirm",
        {"token": token, "new_password": "a brand new passphrase"},
    )
    responses["changed_email"] = _email(mailbox.last("ada@example.com"))
    responses["confirm_again"] = await _post(
        browser,
        "/auth/password-reset/confirm",
        {"token": token, "new_password": "a brand new passphrase"},
    )
    for _ in range(2):
        await _post(browser, "/auth/password-reset", {"email": "ada@example.com"})
    responses["request_over_the_limit"] = await _post(
        browser, "/auth/password-reset", {"email": "ada@example.com"}
    )
    assert responses == snapshot
