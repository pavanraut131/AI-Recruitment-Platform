"""The session and CSRF cookies of ``/api/v2`` (EPIC-02 spec §4.1, §4.5; ADR 0010).

Two cookies, both host-only (no ``Domain``) on ``Path=/``:

========================  ===========  ========  ========  =========================================
name (Secure / not)       HttpOnly     Secure    SameSite  holds
========================  ===========  ========  ========  =========================================
``__Host-hf_session``     yes          yes       Lax       the opaque session id (256 bits); the
/ ``hf_session``                                           server stores only its SHA-256
``__Host-hf_csrf``        no           yes       Strict    the signed CSRF token (``app.platform
/ ``hf_csrf``                                              .csrf``); the frontend reads it
========================  ===========  ========  ========  =========================================

``Secure`` follows :attr:`Settings.session_cookie_secure` (on everywhere but development and test
unless ``SESSION_COOKIE_SECURE`` says otherwise; ``false`` is refused outside them). With it the
names get the ``__Host-`` prefix, which makes the browser refuse a cookie that is not Secure,
host-only and on ``/``; without it (plain ``http://localhost``) the names drop the prefix, since
a browser refuses a ``__Host-`` cookie that is not Secure.

This module only names, sets, reads and clears the cookies; ``identity`` creates and checks
sessions, ``app.platform.csrf`` signs and checks the tokens.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

from starlette.requests import HTTPConnection
from starlette.responses import Response

from app.platform.config import settings

SESSION_COOKIE_BASE: Final = "hf_session"
CSRF_COOKIE_BASE: Final = "hf_csrf"
HOST_PREFIX: Final = "__Host-"


def _name(base: str) -> str:
    return f"{HOST_PREFIX}{base}" if settings.session_cookie_secure else base


def session_cookie_name() -> str:
    """``__Host-hf_session`` when cookies are Secure, else ``hf_session``."""
    return _name(SESSION_COOKIE_BASE)


def csrf_cookie_name() -> str:
    """``__Host-hf_csrf`` when cookies are Secure, else ``hf_csrf``."""
    return _name(CSRF_COOKIE_BASE)


def session_cookie(connection: HTTPConnection) -> str | None:
    """The session cookie's value, or ``None`` (absent or empty)."""
    return connection.cookies.get(session_cookie_name()) or None


def csrf_cookie(connection: HTTPConnection) -> str | None:
    """The CSRF cookie's value, or ``None`` (absent or empty)."""
    return connection.cookies.get(csrf_cookie_name()) or None


def set_session_cookie(response: Response, token: str, max_age: timedelta) -> None:
    """Send the session id. ``max_age`` is what is left of the session's absolute lifetime, so
    the browser drops the cookie when the server would refuse it anyway."""
    response.set_cookie(
        session_cookie_name(),
        token,
        max_age=max(0, int(max_age.total_seconds())),
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def set_csrf_cookie(response: Response, token: str) -> None:
    """Send the CSRF token. Not HttpOnly (the frontend echoes it in ``X-CSRF-Token``) and
    SameSite=Strict; a browser-session cookie (no ``Max-Age``): a fresh one comes with every
    sign-in and from ``GET /auth/csrf``."""
    response.set_cookie(
        csrf_cookie_name(),
        token,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=False,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        session_cookie_name(),
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(
        csrf_cookie_name(),
        path="/",
        secure=settings.session_cookie_secure,
        httponly=False,
        samesite="strict",
    )


def cleared_session_cookie_header() -> str:
    """A ``Set-Cookie`` value that deletes the session cookie, for an error response raised as
    an exception (a 401 for an expired or revoked session clears the cookie)."""
    response = Response()
    clear_session_cookie(response)
    return response.headers["set-cookie"]
