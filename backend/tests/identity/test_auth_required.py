"""Every ``/api/v2`` route requires a session (spec §5.4, §8.3), except the anonymous auth
endpoints; ``AUTH_REQUIRED=false`` (development and test only) makes a request without a session
act as the default agency's owner; and the settings refuse the development-only values
elsewhere (spec §10)."""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import AsyncClient
from pydantic import ValidationError

from app.platform.config import Settings, settings
from tests.identity.conftest import V2, Users, problem

PUBLIC = {("get", "/auth/csrf"), ("post", "/auth/login")}
UNKNOWN = "00000000-0000-4000-8000-00000000dead"


def _operations() -> list[tuple[str, str, dict[str, Any]]]:
    from app.v2 import v2_app

    document = v2_app.openapi()
    return [
        (method, path, op) for path, ops in document["paths"].items() for method, op in ops.items()
    ]


def _url(path: str) -> str:
    return V2 + re.sub(r"\{[^}]+\}", UNKNOWN, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [(m, p) for m, p, _ in _operations() if (m, p) not in PUBLIC],
)
async def test_every_operation_needs_a_session(
    auth_client: AsyncClient, method: str, path: str
) -> None:
    """Generated from the OpenAPI document, so a new endpoint is covered automatically. With a
    valid (anonymous) CSRF token, so the CSRF check passes and the endpoint itself answers 401,
    before it reads the body or the path."""
    token = (await auth_client.get(f"{V2}/auth/csrf")).json()["csrf_token"]
    resp = await auth_client.request(
        method.upper(), _url(path), json={}, headers={"X-CSRF-Token": token}
    )
    assert resp.status_code == 401, (method, path, resp.text)
    assert problem(resp)["detail"] == "Not signed in"


def test_the_document_declares_the_security_of_each_operation() -> None:
    from app.v2 import v2_app

    document = v2_app.openapi()
    schemes = document["components"]["securitySchemes"]
    assert schemes["cookieAuth"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "__Host-hf_session",
        "description": schemes["cookieAuth"]["description"],
    }
    assert schemes["csrfToken"]["in"] == "header" and schemes["csrfToken"]["name"] == "X-CSRF-Token"
    for method, path, op in _operations():
        unsafe = method in ("post", "put", "patch", "delete")
        if (method, path) in PUBLIC:
            assert op.get("security", []) == ([{"csrfToken": []}] if unsafe else []), path
            continue
        expected = {"cookieAuth": [], **({"csrfToken": []} if unsafe else {})}
        assert op["security"] == [expected], (method, path)
        assert "401" in op["responses"], (method, path)
        if unsafe or not path.startswith("/me"):
            assert "403" in op["responses"], (method, path)


def test_a_route_without_a_session_or_public_marker_is_refused() -> None:
    from app.modules.identity.dependencies import current_principal, public
    from app.v2 import _check_authentication

    router = APIRouter()

    @router.get("/open", operation_id="open")
    async def open_route() -> None: ...

    app = FastAPI()
    app.include_router(router)
    with pytest.raises(RuntimeError, match="requires no session and is not marked public"):
        _check_authentication(app)

    ok = FastAPI()
    ok.include_router(router, dependencies=[Depends(current_principal)])
    _check_authentication(ok)
    marked = FastAPI()
    marked.include_router(router, dependencies=[Depends(public)])
    _check_authentication(marked)


# ── AUTH_REQUIRED=false ───────────────────────────────────────
async def test_auth_required_false_acts_as_the_default_owner_without_a_cookie(
    users: Users, auth_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "AUTH_REQUIRED", False)
    assert (await auth_client.get(f"{V2}/campaigns")).status_code == 200
    agency = (await auth_client.get(f"{V2}/agency")).json()
    assert agency["slug"] == "default"
    created = await auth_client.post(f"{V2}/campaigns", json={"title": "T", "role": "R"})
    assert created.status_code == 201
    # /me needs a real session even then.
    assert (await auth_client.get(f"{V2}/me")).status_code == 401
    # A cookie is always checked: an invalid one is a 401, not the fallback owner.
    auth_client.cookies.set("hf_session", "stale", domain="test.local")
    assert (await auth_client.get(f"{V2}/campaigns")).status_code == 401
    # And a signed-in user is themselves, in their own agency.
    northbank = await users.agency()
    from app.modules.identity.schemas import Role

    await users.user("nina@example.com", {northbank: Role.viewer})
    await users.login("nina@example.com")
    assert (await auth_client.get(f"{V2}/agency")).json()["id"] == str(northbank)


# ── Settings ──────────────────────────────────────────────────
def _settings(**values: Any) -> Settings:
    return Settings(**{"AI_PROVIDER": "fake", "SECRET_KEY": "s" * 64, **values})


@pytest.mark.parametrize("env", ["production", "staging", "Production "])
def test_development_only_settings_are_refused_in_deployments(env: str) -> None:
    with pytest.raises(ValidationError, match="AUTH_REQUIRED=false is allowed only"):
        _settings(APP_ENV=env, AUTH_REQUIRED=False)
    with pytest.raises(ValidationError, match="SESSION_COOKIE_SECURE=false"):
        _settings(APP_ENV=env, SESSION_COOKIE_SECURE=False)
    for weak in ("change_me", "change_me_in_production_use_openssl_rand_hex_32", "short-key"):
        with pytest.raises(ValidationError, match="SECRET_KEY must be at least 32 bytes"):
            _settings(APP_ENV=env, SECRET_KEY=weak)
    good = _settings(APP_ENV=env)
    assert good.AUTH_REQUIRED and good.session_cookie_secure


@pytest.mark.parametrize("env", ["development", "test"])
def test_development_accepts_them(env: str) -> None:
    dev = _settings(
        APP_ENV=env, AUTH_REQUIRED=False, SESSION_COOKIE_SECURE=False, SECRET_KEY="change_me"
    )
    assert not dev.AUTH_REQUIRED and not dev.session_cookie_secure
    # Unset, cookies are Secure only outside development and test.
    assert _settings(APP_ENV=env).session_cookie_secure is False
    assert _settings(APP_ENV=env, SESSION_COOKIE_SECURE=True).session_cookie_secure


def test_secure_cookies_get_the_host_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.responses import Response

    from app.platform import auth_cookies

    monkeypatch.setattr(settings, "SESSION_COOKIE_SECURE", True)
    assert auth_cookies.session_cookie_name() == "__Host-hf_session"
    assert auth_cookies.csrf_cookie_name() == "__Host-hf_csrf"
    from datetime import timedelta

    response = Response()
    auth_cookies.set_session_cookie(response, "token", timedelta(days=7))
    auth_cookies.set_csrf_cookie(response, "csrf")
    session, csrf_cookie = response.headers.getlist("set-cookie")
    assert session.startswith("__Host-hf_session=token;")
    for attribute in ("HttpOnly", "Max-Age=604800", "Path=/", "SameSite=lax", "Secure"):
        assert attribute in session
    assert "Domain" not in session
    assert csrf_cookie.startswith("__Host-hf_csrf=csrf;")
    assert "Secure" in csrf_cookie and "SameSite=strict" in csrf_cookie
    assert "HttpOnly" not in csrf_cookie
