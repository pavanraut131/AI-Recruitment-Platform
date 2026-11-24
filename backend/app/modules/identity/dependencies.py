"""FastAPI dependencies that authenticate ``/api/v2`` requests and bind their tenant (EPIC-02
spec §5.4, §6.3).

Public, like ``service``, but only for HTTP code: import-linter lets ``app.v2``,
``app.api.v2`` and the modules' ``router_v2`` import it, and nothing else.

- :func:`current_session`: the signed-in session (:class:`SignedIn`), with or without an active
  agency. ``401`` without a valid session. For ``/me`` and ``/me/active-agency``, ``POST
  /auth/logout``.
- :func:`current_principal`: who the request acts as, in which agency (:class:`Principal`).
  ``401`` without a valid session, ``403`` *"Choose an agency"* when the session has no active
  agency (or its access to it has ended). Every other v2 route requires it (``app.v2`` adds it
  to the routers it includes and refuses to start if a route has neither it nor :func:`public`).
- :func:`tenant_db`: the request's ``AsyncSession`` (``get_db``) **bound to the principal's
  agency**. It replaces ``get_db`` in every v2 router. The principal is loaded first, in a short
  transaction of its own on the same session (identity's tables are global, spec §6.4), which is
  committed before the session is bound, so every later transaction of the request carries the
  agency (``app.platform.tenancy``).
- :func:`access_scope`: the principal's :class:`AccessScope`.
- :func:`public`: the marker for the anonymous endpoints (``GET /auth/csrf``, ``POST
  /auth/login``).

An expired, revoked or unknown session cookie is a ``401`` that also clears the cookie. With
``AUTH_REQUIRED=false`` (development and test only, until E2-09) a request **without** a session
cookie acts as the default agency's owner (:data:`DEV_FALLBACK`); a request with a cookie is
always checked.
"""

from __future__ import annotations

import structlog
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service
from app.modules.identity.schemas import Principal, Role, SignedIn
from app.platform.auth_cookies import cleared_session_cookie_header, session_cookie
from app.platform.config import settings
from app.platform.database import get_db
from app.platform.request_tenant import bind_request
from app.platform.tenancy import DEFAULT_AGENCY_ID, AccessScope

NOT_SIGNED_IN = "Not signed in"
SESSION_ENDED = "Your session has ended. Sign in again."
CHOOSE_AGENCY = "Choose an agency"

DEV_FALLBACK = Principal(
    agency_id=DEFAULT_AGENCY_ID,
    role=Role.owner,
    scope=AccessScope(DEFAULT_AGENCY_ID),
    user_id=None,
    membership_id=None,
    session=None,
)
"""The principal of a request without a session cookie when ``AUTH_REQUIRED=false``."""


def client_info(request: Request) -> service.ClientInfo:
    """The request's client address (from the socket) and user agent."""
    host = request.client.host if request.client else None
    return service.ClientInfo.of(host, request.headers.get("user-agent"))


async def _session(request: Request, db: AsyncSession = Depends(get_db)) -> SignedIn | None:
    """The request's valid session; ``None`` when it has no session cookie; ``401`` (clearing
    the cookie) when the cookie names no valid session."""
    token = session_cookie(request)
    if token is None:
        return None
    signed_in = await service.authenticate(db, token)
    # End the lookup's transaction (and its throttled last_seen_at write) before anything binds
    # this session to a tenant.
    await db.commit()
    if signed_in is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            SESSION_ENDED,
            headers={"set-cookie": cleared_session_cookie_header()},
        )
    structlog.contextvars.bind_contextvars(user_id=str(signed_in.user_id))
    return signed_in


async def current_session(signed_in: SignedIn | None = Depends(_session)) -> SignedIn:
    """The signed-in session (an active agency is not required)."""
    if signed_in is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_SIGNED_IN)
    return signed_in


async def current_principal(signed_in: SignedIn | None = Depends(_session)) -> Principal:
    """Who the request acts as, in which agency."""
    if signed_in is None:
        if not settings.AUTH_REQUIRED:
            return DEV_FALLBACK
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_SIGNED_IN)
    membership = signed_in.membership
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, CHOOSE_AGENCY)
    return Principal(
        agency_id=membership.agency_id,
        role=membership.role,
        scope=membership.scope,
        user_id=signed_in.user_id,
        membership_id=membership.id,
        session=signed_in,
    )


async def tenant_db(
    db: AsyncSession = Depends(get_db), principal: Principal = Depends(current_principal)
) -> AsyncSession:
    """The request's session (``get_db``), bound to the principal's agency."""
    return bind_request(db, principal.agency_id)


def access_scope(principal: Principal = Depends(current_principal)) -> AccessScope:
    """What the caller may see: every client of the agency, or the membership's clients."""
    return principal.scope


def public() -> None:
    """Marks an anonymous endpoint: the v2 app accepts a route without a session dependency only
    with this marker (deny by default)."""


AUTH_DEPENDENCIES = (current_principal, current_session, public)
"""A v2 route must depend on one of these (``app.v2`` checks at start-up)."""
