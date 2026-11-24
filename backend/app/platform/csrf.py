"""CSRF protection for ``/api/v2`` (EPIC-02 spec §4.5; ADR 0010).

The session cookie is ``SameSite=Lax``, which already keeps it off cross-site POSTs in current
browsers. Not enough on its own (older browsers, sibling subdomains, method-override bugs), so
:class:`CSRFMiddleware` also checks every **unsafe** request (``POST``, ``PUT``, ``PATCH``,
``DELETE``):

1. **Origin.** When the request has an ``Origin`` header (else a ``Referer``), it must be one of
   ``CORS_ORIGINS`` or the API's own origin. ``Origin: null`` is refused.
2. **A signed double-submit token.** The ``X-CSRF-Token`` header must equal the CSRF cookie
   (compared in constant time) and carry a valid signature for the request's session:

   ``token = nonce "." base64url(HMAC-SHA256(key, binding "|" nonce))``

   where ``key`` is derived from ``SECRET_KEY`` and ``binding`` is the SHA-256 of the session
   cookie (or ``anonymous`` when the request has none). A token minted for another session, or
   planted by a sibling subdomain without the secret, does not validate. ``GET /auth/csrf``
   issues one for the anonymous browser before sign-in, and sign-in issues one for the new
   session.

   The token is required whenever the request **carries a session cookie** (the ambient
   credential CSRF abuses), and always on the anonymous auth endpoints (``/auth/…``, e.g. login:
   login CSRF). A request with no session cookie and outside ``/auth/`` carries no credential:
   it can only be refused with 401 by the endpoint, or (``AUTH_REQUIRED=false``, development
   only) act as the fallback owner, which the Origin check still guards against other sites.

A failure is ``403`` with the problem detail *"CSRF token missing or invalid"*. The custom
header also forces a CORS preflight, which the CORS policy refuses for unknown origins.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Final
from urllib.parse import urlsplit

from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from app.platform.auth_cookies import csrf_cookie, session_cookie
from app.platform.config import settings
from app.platform.problems import problem_response

HEADER: Final = "X-CSRF-Token"
UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Paths (inside /api/v2) whose unsafe requests need the token even without a session cookie.
ANONYMOUS_PROTECTED_PREFIX: Final = "/auth/"
FAILURE_DETAIL: Final = "CSRF token missing or invalid"

_KEY_LABEL: Final = b"hireflow/csrf/v1"
_ANONYMOUS: Final = "anonymous"


def _key() -> bytes:
    # Derived (not SECRET_KEY itself) so the CSRF MAC and any other use of the secret never
    # share a key. Computed per call: cheap, and tests may patch the settings.
    return hmac.new(settings.SECRET_KEY.encode(), _KEY_LABEL, hashlib.sha256).digest()


def _binding(session_token: str | None) -> str:
    if not session_token:
        return _ANONYMOUS
    return "session:" + hashlib.sha256(session_token.encode()).hexdigest()


def _mac(binding: str, nonce: str) -> str:
    digest = hmac.new(_key(), f"{binding}|{nonce}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def issue_token(session_token: str | None) -> str:
    """A new CSRF token bound to the session whose cookie value is ``session_token`` (``None``:
    the anonymous browser, before sign-in)."""
    nonce = secrets.token_urlsafe(18)
    return f"{nonce}.{_mac(_binding(session_token), nonce)}"


def verify_token(token: str | None, session_token: str | None) -> bool:
    """Whether ``token`` was issued (with this secret) for this session (or for no session)."""
    if not token or token.count(".") != 1:
        return False
    nonce, mac = token.split(".")
    if not nonce or not mac:
        return False
    return hmac.compare_digest(mac, _mac(_binding(session_token), nonce))


def token_matches(header: str | None, cookie: str | None, session_token: str | None) -> bool:
    """The double-submit check: the header equals the cookie and is a valid token for the
    request's session."""
    if not header or not cookie:
        return False
    return hmac.compare_digest(header.encode(), cookie.encode()) and verify_token(
        header, session_token
    )


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def origin_allowed(connection: HTTPConnection) -> bool:
    """``Origin`` (else the ``Referer``'s origin) is an allowed origin, or there is neither."""
    origin = connection.headers.get("origin")
    if origin is None:
        referer = connection.headers.get("referer")
        if referer is None:
            return True
        origin = _origin_of(referer)
        if origin is None:
            return False
    origin = origin.strip().lower()
    if origin == "null":
        return False
    allowed = {o.rstrip("/").lower() for o in settings.cors_origins_list}
    own = f"{connection.url.scheme}://{connection.url.netloc}".lower()
    return origin == own or origin in allowed


def _route_path(scope: Scope) -> str:
    """The path inside the mounted application (``/auth/login`` for ``/api/v2/auth/login``)."""
    path: str = scope.get("path", "")
    root: str = scope.get("root_path", "")
    return path[len(root) :] if root and path.startswith(root) else path


class CSRFMiddleware:
    """Pure ASGI (no buffering, so the SSE stream and uploads pass through untouched)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            connection = HTTPConnection(scope)
            if not self._allowed(connection, scope):
                response = problem_response(403, FAILURE_DETAIL)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    def _allowed(connection: HTTPConnection, scope: Scope) -> bool:
        if not origin_allowed(connection):
            return False
        session = session_cookie(connection)
        if session is None and not _route_path(scope).startswith(ANONYMOUS_PROTECTED_PREFIX):
            return True  # no credential to abuse (see the module docstring)
        return token_matches(connection.headers.get(HEADER), csrf_cookie(connection), session)
