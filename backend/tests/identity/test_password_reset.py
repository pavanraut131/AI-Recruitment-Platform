"""Password reset with real cookies (spec §4.3, §4.6, §11.3; E2-07): the same 202 for known and
unknown addresses (body, no email, timing class), the emailed link, single use and expiry,
every session revoked, the "password changed" email, OIDC-only users adding a password, the
limits and the audit events. And the notice a password change now sends (spec §10)."""

from __future__ import annotations

import hashlib
import statistics
import string
import time

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import AuditEvent, PasswordResetToken, User
from app.platform.config import settings
from tests.identity.conftest import (
    PASSWORD,
    SESSION_COOKIE,
    V2,
    Mailbox,
    Users,
    anonymous_csrf,
    csrf,
    problem,
    set_cookie_headers,
)

ADA = "ada@example.com"
NEW_PASSWORD = "a brand new passphrase"
ACCEPTED = {
    "detail": "If an account exists for this address, we've sent it a link to choose a new "
    "password."
}
GONE = "This link has expired or was already used. Ask for a new one."


async def _events(db: AsyncSession, action: str) -> list[AuditEvent]:
    db.expire_all()
    result = await db.execute(
        select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id)
    )
    return list(result.scalars())


async def _request(client: AsyncClient, email: str) -> Response:
    return await client.post(
        f"{V2}/auth/password-reset", json={"email": email}, headers=await anonymous_csrf(client)
    )


async def _confirm(client: AsyncClient, token: str, password: str = NEW_PASSWORD) -> Response:
    return await client.post(
        f"{V2}/auth/password-reset/confirm",
        json={"token": token, "new_password": password},
        headers=await anonymous_csrf(client),
    )


# ── Requesting a link ─────────────────────────────────────────
async def test_a_known_address_gets_a_single_use_link(
    users: Users, browser: AsyncClient, mailbox: Mailbox, db_session: AsyncSession
) -> None:
    ada = await users.user(ADA)
    resp = await _request(browser, " ADA@example.com ")
    assert resp.status_code == 202 and resp.json() == ACCEPTED

    message = mailbox.last(ADA)
    token = mailbox.token(ADA)
    link = f"http://localhost:3000/reset-password#token={token}"
    assert message.subject == "Reset your HireFlow AI password"
    assert link in message.text and link in message.html
    assert "for 30 minutes" in message.text
    [row] = (await db_session.execute(select(PasswordResetToken))).scalars().all()
    assert row.token_hash == hashlib.sha256(token.encode()).digest()
    assert (row.user_id, row.used_at) == (ada, None)
    assert (row.expires_at - row.created_at).total_seconds() == pytest.approx(1800, abs=5)
    raw = (await db_session.execute(text("SELECT token_hash FROM password_reset_tokens"))).all()
    assert token.encode() not in str(raw).encode()

    [event] = await _events(db_session, "auth.password_reset_requested")
    assert (event.target_type, event.target_id) == ("user", str(ada))
    assert set(event.details) == {"email_hash"} and "@" not in event.details["email_hash"]


async def test_unknown_and_disabled_addresses_get_the_same_answer_and_no_email(
    users: Users, browser: AsyncClient, mailbox: Mailbox, db_session: AsyncSession
) -> None:
    ada = await users.user(ADA)
    disabled = await users.user("dis@example.com")
    await db_session.execute(update(User).where(User.id == disabled).values(status="disabled"))
    await db_session.flush()

    known = await _request(browser, ADA)
    for email in ("nobody@example.com", "dis@example.com", "", "not an email"):
        resp = await _request(browser, email)
        assert resp.status_code == known.status_code == 202, email
        assert resp.json() == known.json() == ACCEPTED, email
        assert resp.headers.get("content-type") == known.headers.get("content-type")
        assert set_cookie_headers(resp) == {}
    assert [m.to for m in mailbox.messages()] == [ADA]  # only the real account got one
    rows = (await db_session.execute(select(PasswordResetToken.user_id))).scalars().all()
    assert rows == [ada]
    events = await _events(db_session, "auth.password_reset_requested")
    assert [e.details.get("reason") for e in events] == [
        None,
        "unknown_email",
        "user_disabled",
        "unknown_email",
        "unknown_email",
    ]
    assert all(e.target_id in (None, str(ada)) and e.actor_user_id is None for e in events)


async def test_known_and_unknown_addresses_take_about_as_long(
    users: Users, browser: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No password hashing on either path and the email is queued, not sent: the two answers
    are in the same timing class (generous bounds: a smoke test, not a benchmark)."""
    monkeypatch.setattr(settings, "PASSWORD_RESET_MAX_PER_EMAIL", 1000)
    monkeypatch.setattr(settings, "PASSWORD_RESET_MAX_PER_IP", 1000)
    await users.user(ADA)
    headers = await anonymous_csrf(browser)

    async def timed(email: str) -> float:
        started = time.perf_counter()
        resp = await browser.post(
            f"{V2}/auth/password-reset", json={"email": email}, headers=headers
        )
        assert resp.status_code == 202
        return time.perf_counter() - started

    for email in (ADA, "nobody@example.com"):  # warm up both paths
        await timed(email)
    known = statistics.median([await timed(ADA) for _ in range(7)])
    unknown = statistics.median([await timed("nobody@example.com") for _ in range(7)])
    assert abs(known - unknown) < 0.05, (known, unknown)


async def test_a_new_request_retires_the_earlier_link(
    users: Users, browser: AsyncClient, mailbox: Mailbox
) -> None:
    await users.user(ADA)
    await _request(browser, ADA)
    first = mailbox.token(ADA)
    await _request(browser, ADA)
    second = mailbox.token(ADA)
    assert first != second
    resp = await _confirm(browser, first)
    assert resp.status_code == 410 and problem(resp)["detail"] == GONE
    assert (await _confirm(browser, second)).status_code == 204


# ── Choosing the new password ─────────────────────────────────
async def test_confirm_sets_the_password_and_signs_out_everywhere(
    users: Users,
    auth_client: AsyncClient,
    browser: AsyncClient,
    mailbox: Mailbox,
    db_session: AsyncSession,
) -> None:
    ada = await users.user(ADA)
    assert (await users.login(ADA)).status_code == 200  # signed in elsewhere
    assert (await auth_client.get(f"{V2}/me")).status_code == 200
    await _request(browser, ADA)
    token = mailbox.token(ADA)

    resp = await _confirm(browser, token)
    assert resp.status_code == 204, resp.text
    cleared = set_cookie_headers(resp)
    assert cleared[SESSION_COOKIE].startswith(f'{SESSION_COOKIE}=""')
    # Every session ended: the other browser's cookie is a 401 now.
    assert (await auth_client.get(f"{V2}/me")).status_code == 401
    # The new password works, the old one does not.
    assert (await users.login(ADA, PASSWORD, client=browser)).status_code == 401
    assert (await users.login(ADA, NEW_PASSWORD, client=browser)).status_code == 200
    # Single use.
    again = await _confirm(browser, token)
    assert again.status_code == 410 and problem(again)["detail"] == GONE

    notice = mailbox.last(ADA)
    assert notice.subject == "Your HireFlow AI password was changed"
    assert "http://localhost:3000/forgot-password" in notice.text
    assert "#token=" not in notice.text
    [event] = await _events(db_session, "auth.password_reset_completed")
    assert (event.actor_user_id, event.target_id) == (ada, str(ada))
    assert event.details == {"sessions_revoked": 1, "password_added": False}


async def test_an_expired_link_is_gone(
    users: Users, browser: AsyncClient, mailbox: Mailbox, db_session: AsyncSession
) -> None:
    await users.user(ADA)
    await _request(browser, ADA)
    token = mailbox.token(ADA)
    await db_session.execute(
        update(PasswordResetToken).values(expires_at=text("now() - interval '1 second'"))
    )
    await db_session.flush()
    resp = await _confirm(browser, token)
    assert resp.status_code == 410 and problem(resp)["detail"] == GONE
    assert (await users.login(ADA, client=browser)).status_code == 200  # unchanged


async def test_a_refused_password_leaves_the_link_usable(
    users: Users, browser: AsyncClient, mailbox: Mailbox
) -> None:
    await users.user(ADA)
    await _request(browser, ADA)
    token = mailbox.token(ADA)
    for weak in ("short", ADA):
        resp = await _confirm(browser, token, weak)
        assert resp.status_code == 422, weak
        [error] = problem(resp)["errors"]
        assert error["loc"] == ["body", "new_password"]
    assert (await _confirm(browser, token)).status_code == 204


async def test_unknown_tokens_are_gone(browser: AsyncClient) -> None:
    for token in ("x" * 43, "nope"):
        resp = await _confirm(browser, token)
        assert resp.status_code == 410 and problem(resp)["detail"] == GONE


async def test_an_oidc_only_user_adds_a_password(
    users: Users, browser: AsyncClient, mailbox: Mailbox, db_session: AsyncSession
) -> None:
    ada = await users.user(ADA)
    await db_session.execute(
        update(User).where(User.id == ada).values(password_hash=None, email_verified_at=None)
    )
    await db_session.flush()
    await _request(browser, ADA)
    assert (await _confirm(browser, mailbox.token(ADA))).status_code == 204
    assert (await users.login(ADA, NEW_PASSWORD, client=browser)).status_code == 200
    db_session.expire_all()
    user = await db_session.get(User, ada)
    assert user is not None and user.email_verified_at is not None  # the link proved the inbox
    [event] = await _events(db_session, "auth.password_reset_completed")
    assert event.details["password_added"] is True


# ── Limits ────────────────────────────────────────────────────
async def test_three_requests_per_address_an_hour(
    users: Users, browser: AsyncClient, mailbox: Mailbox
) -> None:
    await users.user(ADA)
    for email in (ADA, "nobody@example.com"):  # counted whether or not the account exists
        for _ in range(3):
            assert (await _request(browser, email)).status_code == 202
        resp = await _request(browser, email.upper())
        assert resp.status_code == 429, email
        assert problem(resp)["detail"] == "Too many attempts. Try again later."
        assert 0 < int(resp.headers["retry-after"]) <= 3600
    assert mailbox.count(ADA) == 3


async def test_ten_requests_per_ip_an_hour(browser: AsyncClient) -> None:
    for i in range(10):
        assert (await _request(browser, f"person{i}@example.com")).status_code == 202
    resp = await _request(browser, "another@example.com")
    assert resp.status_code == 429 and int(resp.headers["retry-after"]) > 0


async def test_confirm_attempts_are_limited_per_ip(
    browser: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "TOKEN_MAX_ATTEMPTS_PER_IP", 2)
    for _ in range(2):
        assert (await _confirm(browser, "x" * 43)).status_code == 410
    assert (await _confirm(browser, "x" * 43)).status_code == 429


async def test_the_reset_endpoints_need_the_csrf_token(browser: AsyncClient) -> None:
    for path, body in (
        ("/auth/password-reset", {"email": ADA}),
        ("/auth/password-reset/confirm", {"token": "x" * 43, "new_password": NEW_PASSWORD}),
    ):
        resp = await browser.post(V2 + path, json=body)
        assert resp.status_code == 403, path


# ── A password change notifies too (spec §10) ─────────────────
async def test_changing_the_password_sends_a_notice(
    users: Users, auth_client: AsyncClient, mailbox: Mailbox
) -> None:
    await users.user(ADA)
    await users.login(ADA)
    resp = await auth_client.post(
        f"{V2}/me/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=csrf(auth_client),
    )
    assert resp.status_code == 204
    assert mailbox.last(ADA).subject == "Your HireFlow AI password was changed"


def test_reset_tokens_are_random_and_long() -> None:
    from app.modules.identity import service

    alphabet = set(string.ascii_letters + string.digits + "-_")  # URL-safe base64
    tokens = {service.new_token() for _ in range(50)}
    assert len(tokens) == 50 and min(len(t) for t in tokens) >= 43  # 256 bits
    assert all(set(t) <= alphabet for t in tokens)
