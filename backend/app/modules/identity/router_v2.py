"""identity HTTP layer of ``/api/v2`` (EPIC-02 spec §4.3, §8.2): sign-in and sign-out, the CSRF
token, the signed-in user (``/me``) and the active agency (``/agency``).

Thin, like the other ``router_v2`` modules: each endpoint calls ``identity.service`` and sets
or clears the cookies (``app.platform.auth_cookies``). Errors are problem details. Each endpoint
declares who may call it (``app.modules.identity.dependencies``): the anonymous ones are
``public``, ``/me`` and sign-out are for ``any_member`` (a session, no permission), and
``/agency`` needs ``workspace:read`` / ``agency:manage``. The CSRF middleware (``app.platform.csrf``) has already checked every unsafe
request before it gets here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service
from app.modules.identity.dependencies import (
    Permission,
    any_member,
    client_info,
    current_session,
    public,
    require_permission,
)
from app.modules.identity.schemas import Principal, SignedIn
from app.modules.identity.schemas_v2 import (
    ActiveAgencyChange,
    Agency,
    AgencyUpdate,
    CsrfToken,
    LoginRequest,
    Me,
    MeUpdate,
    PasswordChange,
)
from app.platform import auth_cookies, csrf
from app.platform.config import settings
from app.platform.database import get_db
from app.platform.problems import FieldError, ProblemError, problem_responses
from app.platform.ratelimit import RateLimitStore, rate_limit_store

auth_router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(prefix="/me", tags=["me"])
agency_router = APIRouter(prefix="/agency", tags=["agency"])

routers = (auth_router, me_router, agency_router)

WORKSPACE_READ = Depends(require_permission(Permission.workspace_read))
AGENCY_MANAGE = Depends(require_permission(Permission.agency_manage))


def _service_error(exc: Exception) -> HTTPException | ProblemError:
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
    if isinstance(exc, service.InvalidInput):
        # A 422 naming the body field the service rejected, like a validation error.
        return ProblemError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "1 invalid field",
            errors=[FieldError(loc=["body", exc.field], msg=exc.detail)],
        )
    raise exc


SERVICE_ERRORS = (
    service.RateLimited,
    service.InvalidCredentials,
    service.NoAgencyAccess,
    service.PermissionDenied,
    service.NotFoundError,
    service.InvalidInput,
)


def _start(response: Response, token: str) -> str:
    """Send a new session's cookies; returns its CSRF token."""
    auth_cookies.set_session_cookie(response, token, settings.SESSION_ABSOLUTE_TIMEOUT)
    csrf_token = csrf.issue_token(token)
    auth_cookies.set_csrf_cookie(response, csrf_token)
    return csrf_token


def _csrf_for(request: Request, response: Response, signed_in: SignedIn) -> str:
    """The request's CSRF cookie if it is valid for this session, else a new one (sent)."""
    current = auth_cookies.csrf_cookie(request)
    if current is not None and csrf.verify_token(current, signed_in.token):
        return current
    token = csrf.issue_token(signed_in.token)
    auth_cookies.set_csrf_cookie(response, token)
    return token


# ── /auth ─────────────────────────────────────────────────────
@auth_router.get(
    "/csrf",
    operation_id="getCsrfToken",
    response_model=CsrfToken,
    dependencies=[Depends(public)],
)
async def get_csrf_token(request: Request, response: Response) -> CsrfToken:
    """A CSRF token for this browser (bound to its session cookie, if any), also set as the CSRF
    cookie. Call it before signing in."""
    token = csrf.issue_token(auth_cookies.session_cookie(request))
    auth_cookies.set_csrf_cookie(response, token)
    return CsrfToken(csrf_token=token)


@auth_router.post(
    "/login",
    operation_id="login",
    response_model=Me,
    responses=problem_responses(401, 403, 422, 429),
    dependencies=[Depends(public)],
)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    store: RateLimitStore = Depends(rate_limit_store),
    client: service.ClientInfo = Depends(client_info),
) -> Me:
    """Sign in with email and password. Any session cookie the browser sent is revoked and a new
    session starts (new session and CSRF cookies). 401 with one message for every wrong email or
    password; 403 when the account has no active agency; 429 with Retry-After when throttled."""
    try:
        token = await service.login(
            db,
            store,
            email=payload.email,
            password=payload.password,
            carried_token=auth_cookies.session_cookie(request),
            client=client,
        )
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None
    csrf_token = _start(response, token)
    signed_in = await service.authenticate(db, token)
    assert signed_in is not None  # just created
    return await service.me(db, signed_in, csrf_token)


@auth_router.post(
    "/logout",
    operation_id="logout",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(any_member)],
)
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_db),
    signed_in: SignedIn = Depends(current_session),
    client: service.ClientInfo = Depends(client_info),
) -> None:
    """Sign out: the session is revoked on the server and both cookies are cleared."""
    await service.logout(db, signed_in, client)
    auth_cookies.clear_session_cookie(response)
    auth_cookies.clear_csrf_cookie(response)


# ── /me ───────────────────────────────────────────────────────
@me_router.get("", operation_id="getMe", response_model=Me, dependencies=[Depends(any_member)])
async def get_me(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    signed_in: SignedIn = Depends(current_session),
) -> Me:
    """The signed-in user, their agencies, the active agency, role and client scope, and the
    session's CSRF token."""
    return await service.me(db, signed_in, _csrf_for(request, response, signed_in))


@me_router.patch(
    "",
    operation_id="updateMe",
    response_model=Me,
    responses=problem_responses(422),
    dependencies=[Depends(any_member)],
)
async def update_me(
    payload: MeUpdate,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    signed_in: SignedIn = Depends(current_session),
) -> Me:
    """Change your display name."""
    try:
        updated = await service.update_me(db, signed_in, payload.display_name)
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None
    return await service.me(db, updated, _csrf_for(request, response, updated))


@me_router.post(
    "/password",
    operation_id="changePassword",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=problem_responses(422, 429),
    dependencies=[Depends(any_member)],
)
async def change_password(
    payload: PasswordChange,
    response: Response,
    db: AsyncSession = Depends(get_db),
    signed_in: SignedIn = Depends(current_session),
    store: RateLimitStore = Depends(rate_limit_store),
    client: service.ClientInfo = Depends(client_info),
) -> None:
    """Change your password (the current one is required). Every session of yours ends, other
    browsers are signed out, and this one continues in a new session (new cookies)."""
    try:
        token = await service.change_password(
            db,
            store,
            signed_in,
            current_password=payload.current_password,
            new_password=payload.new_password,
            client=client,
        )
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None
    _start(response, token)


@me_router.put(
    "/active-agency",
    operation_id="setActiveAgency",
    response_model=Me,
    responses=problem_responses(404, 422),
    dependencies=[Depends(any_member)],
)
async def set_active_agency(
    payload: ActiveAgencyChange,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    signed_in: SignedIn = Depends(current_session),
    client: service.ClientInfo = Depends(client_info),
) -> Me:
    """Switch the session to another of your agencies (404 for an agency you are not an active
    member of). Clear any cached data of the previous agency."""
    try:
        await service.set_active_agency(db, signed_in, payload.agency_id, client)
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None
    switched = await service.authenticate(db, signed_in.token)
    if switched is None:  # revoked meanwhile
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session has ended. Sign in again.")
    await db.commit()
    return await service.me(db, switched, _csrf_for(request, response, switched))


# ── /agency ───────────────────────────────────────────────────
@agency_router.get("", operation_id="getAgency", response_model=Agency)
async def get_agency(
    db: AsyncSession = Depends(get_db),
    principal: Principal = WORKSPACE_READ,
) -> Agency:
    """The active agency."""
    try:
        return await service.get_agency_v2(db, principal)
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None


@agency_router.patch(
    "", operation_id="updateAgency", response_model=Agency, responses=problem_responses(422)
)
async def update_agency(
    payload: AgencyUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = AGENCY_MANAGE,
    client: service.ClientInfo = Depends(client_info),
) -> Agency:
    """Rename the active agency (``agency:manage``: owners)."""
    try:
        return await service.update_agency_v2(db, principal, payload.name, client)
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None
