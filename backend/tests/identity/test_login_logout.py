"""Sign-in and sign-out with real cookies (spec §4.3, §11.3): success, the cookie and session
storage, failures without enumeration, rotation (session fixation), logout and replay."""

from __future__ import annotations

import hashlib
import statistics
import time
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import AuditEvent, User, UserSession
from app.modules.identity.schemas import Role
from app.platform.tenancy import DEFAULT_AGENCY_ID
from tests.identity.conftest import (
    CSRF_COOKIE,
    PASSWORD,
    SESSION_COOKIE,
    V2,
    Users,
    csrf,
    problem,
    set_cookie_headers,
)

INVALID = {
    "type": "about:blank",
    "title": "Unauthorized",
    "status": 401,
    "detail": "Invalid email or password",
}


async def _events(db: AsyncSession, action: str) -> list[AuditEvent]:
    db.expire_all()
    result = await db.execute(
        select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id)
    )
    return list(result.scalars())


async def _sessions(db: AsyncSession, user_id: uuid.UUID) -> list[UserSession]:
    db.expire_all()
    result = await db.execute(
        select(UserSession).where(UserSession.user_id == user_id).order_by(UserSession.created_at)
    )
    return list(result.scalars())


# ── Success ───────────────────────────────────────────────────
async def test_login_starts_a_session_and_answers_me(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await users.user("Ada@Example.com")
    resp = await users.login("  ADA@example.com ")  # trimmed and case-insensitive
    assert resp.status_code == 200, resp.text
    me = resp.json()
    assert me["user"] == {"id": str(user_id), "email": "ada@example.com", "display_name": "Ada"}
    assert me["active_agency"] == {"id": str(DEFAULT_AGENCY_ID), "name": "Default agency"}
    assert me["role"] == "owner"
    assert me["client_scope"] == {"all_clients": True, "client_ids": []}
    assert me["memberships"] == [
        {"agency": {"id": str(DEFAULT_AGENCY_ID), "name": "Default agency"}, "role": "owner"}
    ]
    # The CSRF token of the new session is in the body and in the (readable) cookie.
    assert me["csrf_token"] == auth_client.cookies.get(CSRF_COOKIE)

    # The cookie value is 256 bits of randomness; the database holds only its SHA-256.
    token = auth_client.cookies.get(SESSION_COOKIE)
    assert token is not None and len(token) >= 43
    [session] = await _sessions(db_session, user_id)
    assert session.id_hash == hashlib.sha256(token.encode()).digest()
    assert session.active_agency_id == DEFAULT_AGENCY_ID
    assert session.revoked_at is None
    assert session.idle_expires_at > session.created_at
    assert session.absolute_expires_at > session.idle_expires_at
    raw = (await db_session.execute(text("SELECT id_hash FROM user_sessions"))).scalars().all()
    assert token.encode() not in raw

    [event] = await _events(db_session, "auth.login_succeeded")
    assert (event.actor_user_id, event.agency_id) == (user_id, DEFAULT_AGENCY_ID)
    assert event.details == {"method": "password"}
    user = await db_session.get(User, user_id)
    assert user is not None and user.last_login_at is not None

    # The session works.
    assert (await auth_client.get(f"{V2}/me")).json()["user"]["id"] == str(user_id)
    assert (await auth_client.get(f"{V2}/campaigns")).status_code == 200


async def test_cookie_attributes_in_development(users: Users) -> None:
    await users.user("ada@example.com")
    cookies = set_cookie_headers(await users.login("ada@example.com"))
    session = cookies[SESSION_COOKIE].lower()
    assert "httponly" in session and "samesite=lax" in session and "path=/" in session
    assert "domain" not in session
    assert "max-age=604800" in session  # the absolute lifetime (7 days)
    assert "secure" not in session  # APP_ENV=test over http: no Secure, no __Host- prefix
    csrf_cookie = cookies[CSRF_COOKIE].lower()
    assert "httponly" not in csrf_cookie and "samesite=strict" in csrf_cookie


# ── Failures: one answer, no enumeration ─────────────────────
async def test_every_failure_gets_the_same_401(users: Users, db_session: AsyncSession) -> None:
    await users.user("ada@example.com")
    disabled = await users.user("dis@example.com")
    await db_session.execute(update(User).where(User.id == disabled).values(status="disabled"))
    oidc_only = await users.user("oidc@example.com")
    await db_session.execute(update(User).where(User.id == oidc_only).values(password_hash=None))
    await db_session.flush()

    attempts = [
        ("nobody@example.com", PASSWORD),  # unknown email
        ("ada@example.com", "wrong password!!"),  # wrong password
        ("dis@example.com", PASSWORD),  # right password, disabled user
        ("oidc@example.com", PASSWORD),  # no password (OIDC only)
        ("", PASSWORD),  # not even an email
    ]
    for email, password in attempts:
        resp = await users.login(email, password)
        assert resp.status_code == 401, (email, resp.text)
        assert problem(resp) == INVALID, email
        assert SESSION_COOKIE not in set_cookie_headers(resp)

    events = await _events(db_session, "auth.login_failed")
    assert [e.details["reason"] for e in events] == [
        "unknown_email",
        "wrong_password",
        "user_disabled",
        "no_password",
        "unknown_email",
    ]
    for event in events:  # a keyed hash of the email, never the address or the password
        assert set(event.details) == {"email_hash", "reason"}
        assert "@" not in event.details["email_hash"]
        assert event.agency_id is None and event.actor_user_id is None


async def test_unknown_email_costs_about_as_much_as_a_wrong_password(users: Users) -> None:
    """The dummy hash makes timing useless for finding accounts (roughly equal: generous bounds,
    this is a smoke test, not a benchmark)."""
    await users.user("ada@example.com")
    await users.login("warm-up@example.com", "x" * 12)  # builds the dummy hash once

    async def timed(email: str) -> float:
        started = time.perf_counter()
        resp = await users.login(email, "wrong password!!")
        assert resp.status_code == 401
        return time.perf_counter() - started

    unknown, known = [], []
    for i in range(4):  # under the 5-per-email limit
        unknown.append(await timed(f"nobody{i}@example.com"))
        known.append(await timed("ada@example.com"))
    ratio = statistics.median(unknown) / statistics.median(known)
    assert 0.5 < ratio < 2.0, (unknown, known)


async def test_right_password_without_agency_access_is_403(
    users: Users, db_session: AsyncSession
) -> None:
    agency = await users.agency()
    await users.user("ada@example.com", {agency: Role.recruiter})
    await db_session.execute(
        text("UPDATE agencies SET status = 'suspended' WHERE id = :id"), {"id": agency}
    )
    await users.user("bob@example.com", {})  # no membership at all
    for email in ("ada@example.com", "bob@example.com"):
        resp = await users.login(email)
        assert resp.status_code == 403
        assert problem(resp)["detail"] == "Your account has no active agency access"
        assert SESSION_COOKIE not in set_cookie_headers(resp)


async def test_a_hash_with_old_parameters_is_upgraded_on_login(
    users: Users, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.platform.config import settings

    user_id = await users.user("ada@example.com")
    before = await _password_hash(db_session, user_id)
    assert before.startswith("$argon2id$") and "m=65536,t=3,p=4" in before
    monkeypatch.setattr(settings, "PASSWORD_HASH_TIME_COST", 2)
    assert (await users.login("ada@example.com")).status_code == 200
    after = await _password_hash(db_session, user_id)
    assert after != before and "m=65536,t=2,p=4" in after


async def _password_hash(db: AsyncSession, user_id: uuid.UUID) -> str:
    db.expire_all()
    value = (await db.execute(select(User.password_hash).where(User.id == user_id))).scalar_one()
    assert value is not None
    return str(value)


# ── Rotation and logout ───────────────────────────────────────
async def test_login_rotates_the_session_fixation(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    ada = await users.user("ada@example.com")
    await users.user("eve@example.com")
    # An attacker's session id planted in the victim's browser...
    assert (await users.login("eve@example.com")).status_code == 200
    planted = auth_client.cookies.get(SESSION_COOKIE)
    # ...is revoked when the victim signs in, and the victim gets a new id.
    assert (await users.login("ada@example.com")).status_code == 200
    fresh = auth_client.cookies.get(SESSION_COOKIE)
    assert fresh and planted and fresh != planted
    [eve_session] = await _sessions(db_session, await _user_id(db_session, "eve@example.com"))
    assert eve_session.revoked_at is not None
    async with AsyncClient(
        transport=auth_client._transport, base_url="http://test", cookies={SESSION_COOKIE: planted}
    ) as attacker:
        assert (await attacker.get(f"{V2}/me")).status_code == 401
    assert (await auth_client.get(f"{V2}/me")).json()["user"]["id"] == str(ada)


async def _user_id(db: AsyncSession, email: str) -> uuid.UUID:
    return (await db.execute(select(User.id).where(User.email == email))).scalar_one()


async def test_a_pre_set_unknown_cookie_is_not_adopted(
    users: Users, auth_client: AsyncClient
) -> None:
    await users.user("ada@example.com")
    auth_client.cookies.set(SESSION_COOKIE, "attacker-chosen-value", domain="test.local")
    assert (await auth_client.get(f"{V2}/auth/csrf")).status_code == 200
    resp = await users.login("ada@example.com")
    assert resp.status_code == 200
    assert auth_client.cookies.get(SESSION_COOKIE) != "attacker-chosen-value"


async def test_logout_revokes_the_session_and_clears_the_cookies(
    users: Users, auth_client: AsyncClient, db_session: AsyncSession
) -> None:
    user_id = await users.user("ada@example.com")
    await users.login("ada@example.com")
    token = auth_client.cookies.get(SESSION_COOKIE)

    resp = await auth_client.post(f"{V2}/auth/logout", headers=csrf(auth_client))
    assert resp.status_code == 204, resp.text
    cleared = set_cookie_headers(resp)
    assert (
        'hf_session=""' in cleared[SESSION_COOKIE]
        and "max-age=0" in cleared[SESSION_COOKIE].lower()
    )
    assert "max-age=0" in cleared[CSRF_COOKIE].lower()
    [session] = await _sessions(db_session, user_id)
    assert session.revoked_at is not None
    [event] = await _events(db_session, "auth.logout")
    assert event.actor_user_id == user_id

    # Replaying the old cookie: 401, and the response clears it again.
    auth_client.cookies.clear()
    auth_client.cookies.set(SESSION_COOKIE, token or "")
    replay = await auth_client.get(f"{V2}/me")
    assert replay.status_code == 401
    assert problem(replay)["detail"] == "Your session has ended. Sign in again."
    assert "max-age=0" in set_cookie_headers(replay)[SESSION_COOKIE].lower()
    assert (await auth_client.get(f"{V2}/campaigns")).status_code == 401


async def test_logout_without_a_session_is_401(auth_client: AsyncClient) -> None:
    token = (await auth_client.get(f"{V2}/auth/csrf")).json()["csrf_token"]
    resp = await auth_client.post(f"{V2}/auth/logout", headers={"X-CSRF-Token": token})
    assert resp.status_code == 401
    assert problem(resp)["detail"] == "Not signed in"
