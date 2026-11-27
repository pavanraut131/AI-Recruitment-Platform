"""FastAPI dependencies that authenticate ``/api/v2`` requests and bind their tenant (EPIC-02
spec §5.4, §6.3).

Public, like ``service``, but only for HTTP code: import-linter lets ``app.v2``,
``app.api.v2`` and the modules' ``router_v2`` import it, and nothing else.

- :func:`current_session`: the signed-in session (:class:`SignedIn`), with or without an active
  agency. ``401`` without a valid session. For ``/me`` and ``/me/active-agency``, ``POST
  /auth/logout``.
- :func:`current_principal`: who the request acts as, in which agency (:class:`Principal`).
  ``401`` without a valid session, ``403`` *"Choose an agency"* when the session has no active
  agency (or its access to it has ended). Every route with a permission requires it, through
  :func:`require_permission`.
- :func:`tenant_db`: the request's ``AsyncSession`` (``get_db``) **bound to the principal's
  agency**. It replaces ``get_db`` in every v2 router. The principal is loaded first, in a short
  transaction of its own on the same session (identity's tables are global, spec §6.4), which is
  committed before the session is bound, so every later transaction of the request carries the
  agency (``app.platform.tenancy``).
- :func:`access_scope`: the principal's :class:`AccessScope` (every client, or the clients a
  client-limited membership lists, spec §5.3).
- :func:`optional_session` (E2-07): the signed-in session if the request has a valid one, else
  ``None`` (no cookie, or one that names no valid session): accepting an invitation works both
  ways.
- :func:`service_errors` / :func:`http_error`: identity's service errors as HTTP problems, for
  identity's router and the composed ``/team`` endpoints.

**Every v2 route declares who may call it** (spec §5.4; ``app.v2`` refuses to start otherwise),
with exactly one of:

- :func:`require_permission` ``(permission)``: a signed-in member of the active agency whose
  role has the :class:`~app.modules.identity.permissions.Permission` (403 *"You don't have
  permission to …"* otherwise). Most routes, ``/team`` included (``team:read``,
  ``team:manage``);
- :func:`any_member`: any signed-in user, no permission needed (``/me``, ``/me/*``, ``POST
  /auth/logout``; spec §5.2's "any member" row);
- :func:`public`: anonymous (``GET /auth/csrf``, ``POST /auth/login``, password reset, the
  invitation endpoints, and E2-08's providers list and Google / Microsoft flow; accepting an
  invitation also takes an optional session).

Permissions that depend on the request's data (a bulk *delete*; a team change that targets or
grants an owner or admin, ``team:manage_privileged``) are checked in the handler or the service
with :func:`ensure_permission` / ``permissions.has_permission``, on top of the route's declared
one.

An expired, revoked or unknown session cookie is a ``401`` that also clears the cookie. With
``AUTH_REQUIRED=false`` (development and test only, until E2-09) a request **without** a session
cookie acts as the default agency's owner (:data:`DEV_FALLBACK`); a request with a cookie is
always checked.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager

import structlog
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service
from app.modules.identity.permissions import Permission, denied_detail, has_permission
from app.modules.identity.schemas import Principal, Role, SignedIn
from app.platform.auth_cookies import cleared_session_cookie_header, session_cookie
from app.platform.config import settings
from app.platform.database import get_db
from app.platform.problems import FieldError, ProblemError
from app.platform.tenancy import AccessScope, bind

NOT_SIGNED_IN = "Not signed in"
SESSION_ENDED = "Your session has ended. Sign in again."
CHOOSE_AGENCY = "Choose an agency"

# The fallback acts as an owner of the agency the E2-01 migration made of the data that existed
# before E2 (slug "default"): the only fixed agency id left in the app.
# removed with AUTH_REQUIRED in E2-11
_FALLBACK_AGENCY = uuid.UUID("00000000-0000-4000-8000-00000000a001")

DEV_FALLBACK = Principal(
    agency_id=_FALLBACK_AGENCY,
    role=Role.owner,
    scope=AccessScope(_FALLBACK_AGENCY),
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


async def optional_session(request: Request, db: AsyncSession = Depends(get_db)) -> SignedIn | None:
    """The request's valid session, or ``None`` without one (an ended or unknown session cookie
    is ignored here, not a 401)."""
    token = session_cookie(request)
    if token is None:
        return None
    signed_in = await service.authenticate(db, token)
    await db.commit()
    if signed_in is not None:
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
    """The request's session (``get_db``), bound to the principal's agency, which every log line
    of the request then names (spec §6.6). Test overrides of ``get_db`` keep working: the session
    the override yields is bound here."""
    bind(db, principal.agency_id)
    structlog.contextvars.bind_contextvars(agency_id=str(principal.agency_id))
    return db


def access_scope(principal: Principal = Depends(current_principal)) -> AccessScope:
    """What the caller may see: every client of the agency, or the membership's clients."""
    return principal.scope


# ── Who may call a route ──────────────────────────────────────
PermissionDependency = Callable[..., Awaitable[Principal]]

_PERMISSION_DEPENDENCIES: dict[Permission, PermissionDependency] = {}


def _permission_dependency(permission: Permission) -> PermissionDependency:
    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        ensure_permission(principal, permission)
        return principal

    dependency.__name__ = f"require_{permission.name}"
    dependency.__doc__ = f"The principal, if its role has ``{permission.value}`` (else 403)."
    return dependency


def require_permission(permission: Permission) -> PermissionDependency:
    """The dependency of a route that needs ``permission`` (spec §5.2, §5.4).

    It resolves to the signed-in :class:`Principal` (``current_principal``: 401 without a
    session, 403 *"Choose an agency"* without an active agency), after checking that its role
    has ``permission``: 403 *"You don't have permission to …"* otherwise, before the request's
    body or ids are looked at, so the answer reveals nothing about them. Use it on the route::

        @router.post("", dependencies=[Depends(require_permission(Permission.client_manage))])

    or as a parameter when the handler needs the principal::

        principal: Principal = Depends(require_permission(Permission.campaign_write))

    The same callable is returned for the same permission, so both forms on one route run the
    check once, and ``app.v2`` can find the declaration (:func:`permission_of`).
    """
    dependency = _PERMISSION_DEPENDENCIES.get(permission)
    if dependency is None:
        dependency = _PERMISSION_DEPENDENCIES[permission] = _permission_dependency(permission)
    return dependency


def permission_of(dependency: Callable[..., object]) -> Permission | None:
    """The permission a :func:`require_permission` dependency checks (else ``None``)."""
    return next((p for p, dep in _PERMISSION_DEPENDENCIES.items() if dep is dependency), None)


def ensure_permission(principal: Principal, permission: Permission) -> None:
    """403 unless ``principal``'s role has ``permission``: for a check that depends on the
    request (e.g. the ``delete`` action of a bulk request needs ``application:delete`` on top of
    the route's ``application:review``)."""
    if not has_permission(principal.role, permission):
        raise HTTPException(status.HTTP_403_FORBIDDEN, denied_detail(permission))


def any_member() -> None:
    """Marks a route that any signed-in user may call, with no permission (``/me``, ``/me/*``,
    sign-out: spec §5.2's "any member"). The route still needs a session
    (``current_session``)."""


def public() -> None:
    """Marks an anonymous endpoint: the v2 app accepts a route without a session dependency only
    with this marker (deny by default)."""


# ── Service errors as HTTP problems ───────────────────────────
SERVICE_ERRORS = (
    service.RateLimited,
    service.InvalidCredentials,
    service.NoAgencyAccess,
    service.PermissionDenied,
    service.NotFoundError,
    service.InvalidInput,
    service.ConflictError,
    service.TokenGone,
    service.ProviderUnavailable,
)
"""The identity service errors :func:`http_error` maps (anything else propagates)."""


def http_error(exc: Exception) -> HTTPException | ProblemError:
    """The problem for an identity service error: 429 (with ``Retry-After``), 401, 403, 404,
    409, 410, 502 (a sign-in provider unreachable), or a 422 naming the rejected body field."""
    if isinstance(exc, service.RateLimited):
        return HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            exc.detail,
            headers={"Retry-After": str(exc.retry_after)},
        )
    if isinstance(exc, service.InvalidCredentials):
        return HTTPException(status.HTTP_401_UNAUTHORIZED, exc.detail)
    if isinstance(exc, service.NoAgencyAccess | service.PermissionDenied):
        return HTTPException(status.HTTP_403_FORBIDDEN, exc.detail)
    if isinstance(exc, service.NotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, exc.detail)
    if isinstance(exc, service.ConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, exc.detail)
    if isinstance(exc, service.TokenGone):
        return HTTPException(status.HTTP_410_GONE, exc.detail)
    if isinstance(exc, service.ProviderUnavailable):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, exc.detail)
    if isinstance(exc, service.InvalidInput):
        # A 422 naming the body field the service rejected, like a validation error.
        return ProblemError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "1 invalid field",
            errors=[FieldError(loc=["body", exc.field], msg=exc.detail)],
        )
    raise exc


@contextmanager
def service_errors() -> Iterator[None]:
    """Turn the identity service errors raised inside into their HTTP problems."""
    try:
        yield
    except SERVICE_ERRORS as exc:
        raise http_error(exc) from None


AUTH_DEPENDENCIES = (current_principal, current_session, public)
"""How a v2 route authenticates: one of these (``app.v2`` checks at start-up)."""

ACCESS_MARKERS = (any_member, public)
"""The declarations of a v2 route that needs no permission (``app.v2`` checks at start-up)."""

__all__ = [
    "ACCESS_MARKERS",
    "AUTH_DEPENDENCIES",
    "CHOOSE_AGENCY",
    "DEV_FALLBACK",
    "NOT_SIGNED_IN",
    "SERVICE_ERRORS",
    "SESSION_ENDED",
    "Permission",
    "access_scope",
    "any_member",
    "client_info",
    "current_principal",
    "current_session",
    "ensure_permission",
    "http_error",
    "optional_session",
    "permission_of",
    "public",
    "require_permission",
    "service_errors",
    "tenant_db",
]
