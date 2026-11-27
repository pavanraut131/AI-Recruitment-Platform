"""CSRF (spec §4.5), CORS and security headers (spec §10) on ``/api/v2``.

Flows with real cookies: the token missing, not matching the cookie, minted for another session,
signed with another secret, or sent from a foreign origin is a ``403``; the right token from an
allowed origin passes. Plus unit tests of the token and the Origin check."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from starlette.requests import HTTPConnection

from app.platform import csrf
from app.platform.config import settings
from tests.identity.conftest import CSRF_COOKIE, PASSWORD, V2, Users, problem
from tests.identity.conftest import csrf as csrf_header

CSRF_FAILED = {
    "type": "about:blank",
    "title": "Forbidden",
    "status": 403,
    "detail": "CSRF token missing or invalid",
}
LOGIN = {"email": "ada@example.com", "password": PASSWORD}


# ── Sign-in (login CSRF): the anonymous token is required ─────
async def test_login_needs_the_csrf_token(users: Users, auth_client: AsyncClient) -> None:
    await users.user("ada@example.com")
    resp = await auth_client.post(f"{V2}/auth/login", json=LOGIN)  # no cookie, no header
    assert resp.status_code == 403 and problem(resp) == CSRF_FAILED

    token = (await auth_client.get(f"{V2}/auth/csrf")).json()["csrf_token"]
    assert auth_client.cookies.get(CSRF_COOKIE) == token
    for header in ("", "x" + token[1:], csrf.issue_token(None)):  # empty, tampered, not the cookie
        resp = await auth_client.post(
            f"{V2}/auth/login", json=LOGIN, headers={"X-CSRF-Token": header}
        )
        assert resp.status_code == 403, header
    resp = await auth_client.post(f"{V2}/auth/login", json=LOGIN, headers={"X-CSRF-Token": token})
    assert resp.status_code == 200, resp.text


async def test_a_token_signed_with_another_secret_is_refused(
    users: Users, auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await users.user("ada@example.com")
    monkeypatch.setattr(settings, "SECRET_KEY", "another secret, another deployment!!")
    foreign = csrf.issue_token(None)
    monkeypatch.undo()
    auth_client.cookies.set(CSRF_COOKIE, foreign, domain="test.local")
    resp = await auth_client.post(f"{V2}/auth/login", json=LOGIN, headers={"X-CSRF-Token": foreign})
    assert resp.status_code == 403


# ── Signed in: every unsafe request needs the session's token ─
async def test_unsafe_requests_with_a_session_need_its_token(
    users: Users, auth_client: AsyncClient
) -> None:
    await users.user("ada@example.com")
    assert (await users.login("ada@example.com")).status_code == 200
    body = {"display_name": "Ada L."}

    resp = await auth_client.patch(f"{V2}/me", json=body)  # missing
    assert resp.status_code == 403 and problem(resp) == CSRF_FAILED
    # The anonymous token from before sign-in is not valid for the new session.
    anonymous = csrf.issue_token(None)
    auth_client.cookies.set(CSRF_COOKIE, anonymous, domain="test.local")
    resp = await auth_client.patch(f"{V2}/me", json=body, headers={"X-CSRF-Token": anonymous})
    assert resp.status_code == 403
    # Reads need no token.
    me = (await auth_client.get(f"{V2}/me")).json()
    # /me hands out a valid token again (and sets its cookie).
    assert auth_client.cookies.get(CSRF_COOKIE) == me["csrf_token"]
    resp = await auth_client.patch(f"{V2}/me", json=body, headers=csrf_header(auth_client))
    assert resp.status_code == 200 and resp.json()["user"]["display_name"] == "Ada L."
    # The same token is accepted again: one token per session, not per request.
    resp = await auth_client.post(
        f"{V2}/campaigns", json={"title": "T", "role": "R"}, headers=csrf_header(auth_client)
    )
    assert resp.status_code == 201, resp.text


async def test_a_token_from_another_session_is_refused(
    users: Users, auth_client: AsyncClient
) -> None:
    await users.user("ada@example.com")
    await users.user("eve@example.com")
    async with AsyncClient(transport=auth_client._transport, base_url="http://test") as eve:
        assert (await users.login("eve@example.com", client=eve)).status_code == 200
        eves_token = eve.cookies.get(CSRF_COOKIE)
    assert (await users.login("ada@example.com")).status_code == 200
    auth_client.cookies.set(CSRF_COOKIE, eves_token or "", domain="test.local")
    resp = await auth_client.post(f"{V2}/auth/logout", headers={"X-CSRF-Token": eves_token or ""})
    assert resp.status_code == 403 and problem(resp) == CSRF_FAILED


@pytest.mark.parametrize(
    ("headers", "allowed"),
    [
        ({"Origin": "http://localhost:3000"}, True),  # CORS_ORIGINS
        ({"Origin": "http://test"}, True),  # the API's own origin
        ({"Origin": "https://evil.example"}, False),
        ({"Origin": "null"}, False),
        ({"Referer": "https://evil.example/page"}, False),
        ({"Referer": "http://localhost:3000/campaigns"}, True),
        ({}, True),  # neither header (curl, server-to-server): the token still decides
    ],
)
async def test_the_origin_must_be_allowed(
    users: Users, auth_client: AsyncClient, headers: dict[str, str], allowed: bool
) -> None:
    await users.user("ada@example.com")
    token = (await auth_client.get(f"{V2}/auth/csrf")).json()["csrf_token"]
    resp = await auth_client.post(
        f"{V2}/auth/login", json=LOGIN, headers={"X-CSRF-Token": token, **headers}
    )
    assert (resp.status_code == 200) is allowed, resp.text
    if not allowed:
        assert problem(resp) == CSRF_FAILED


async def test_without_a_session_cookie_only_the_origin_is_checked(
    auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No session cookie, no ambient credential: outside /auth/ the request reaches the endpoint
    (and its 401, or the AUTH_REQUIRED=false owner), but a foreign origin is still refused."""
    body = {"title": "T", "role": "R"}
    assert (await auth_client.post(f"{V2}/campaigns", json=body)).status_code == 401
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    assert (await auth_client.post(f"{V2}/campaigns", json=body)).status_code == 201
    resp = await auth_client.post(
        f"{V2}/campaigns", json=body, headers={"Origin": "https://evil.example"}
    )
    assert resp.status_code == 403


# ── CORS and headers ──────────────────────────────────────────
async def test_cors_allows_the_frontend_with_credentials_and_the_csrf_header(
    auth_client: AsyncClient,
) -> None:
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-csrf-token",
    }
    ok = await auth_client.options(
        f"{V2}/auth/login", headers={"Origin": "http://localhost:3000", **preflight}
    )
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert ok.headers["access-control-allow-credentials"] == "true"
    allowed = ok.headers["access-control-allow-headers"].lower()
    assert "x-csrf-token" in allowed and "*" not in allowed

    foreign = await auth_client.options(
        f"{V2}/auth/login", headers={"Origin": "https://evil.example", **preflight}
    )
    assert foreign.status_code == 400
    assert "access-control-allow-origin" not in foreign.headers
    other_header = await auth_client.options(
        f"{V2}/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-custom",
        },
    )
    assert other_header.status_code == 400


async def test_security_headers_on_every_v2_response(
    users: Users, auth_client: AsyncClient
) -> None:
    await users.user("ada@example.com")
    responses = [
        await auth_client.get(f"{V2}/auth/csrf"),
        await auth_client.get(f"{V2}/campaigns"),  # 401
        await auth_client.post(f"{V2}/auth/login", json=LOGIN),  # the CSRF middleware's 403
        await users.login("ada@example.com"),
        await auth_client.get(f"{V2}/me"),
    ]
    for resp in responses:
        assert resp.headers["x-content-type-options"] == "nosniff", resp.request.url
        assert resp.headers["referrer-policy"] == "no-referrer"
        assert resp.headers["cache-control"] == "no-store"


# ── Units ─────────────────────────────────────────────────────
def test_tokens_are_bound_to_their_session() -> None:
    anonymous = csrf.issue_token(None)
    mine = csrf.issue_token("session-a")
    assert csrf.verify_token(anonymous, None)
    assert csrf.verify_token(mine, "session-a")
    assert not csrf.verify_token(mine, "session-b")
    assert not csrf.verify_token(mine, None)
    assert not csrf.verify_token(anonymous, "session-a")
    assert csrf.issue_token("session-a") != mine  # a fresh nonce each time
    for broken in ("", "no-dot", "a.b.c", ".sig", "nonce.", mine.split(".")[0] + ".AAAA"):
        assert not csrf.verify_token(broken, "session-a"), broken
    assert csrf.token_matches(mine, mine, "session-a")
    assert not csrf.token_matches(mine, csrf.issue_token("session-a"), "session-a")
    assert not csrf.token_matches(None, mine, "session-a")


def _connection(headers: dict[str, str]) -> HTTPConnection:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return HTTPConnection(
        {
            "type": "http",
            "scheme": "https",
            "server": ("api.example", 443),
            "path": "/",
            "headers": [(b"host", b"api.example"), *raw],
        }
    )


def test_origin_check_units(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CORS_ORIGINS", "https://app.example, https://other.example/")
    assert csrf.origin_allowed(_connection({}))
    assert csrf.origin_allowed(_connection({"Origin": "https://app.example"}))
    assert csrf.origin_allowed(_connection({"Origin": "https://OTHER.example"}))
    assert csrf.origin_allowed(_connection({"Origin": "https://api.example"}))  # own origin
    assert not csrf.origin_allowed(_connection({"Origin": "http://app.example"}))  # scheme
    assert not csrf.origin_allowed(_connection({"Origin": "https://app.example.evil"}))
    assert not csrf.origin_allowed(_connection({"Referer": "not a url"}))
    assert csrf.origin_allowed(_connection({"Referer": "https://app.example/x?y=1"}))
