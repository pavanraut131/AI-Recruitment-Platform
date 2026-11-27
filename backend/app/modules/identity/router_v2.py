"""identity HTTP layer of ``/api/v2`` (EPIC-02 spec §4.3, §8.2): sign-in and sign-out, the CSRF
token, password reset and invitation acceptance (E2-07), signing in with Google or Microsoft
(E2-08), the signed-in user (``/me``) and the active agency (``/agency``). The team endpoints need hiring too (client names): they are
composed in ``app.api.v2.team``.

Thin, like the other ``router_v2`` modules: each endpoint calls ``identity.service`` and sets
or clears the cookies (``app.platform.auth_cookies``). Errors are problem details. Each endpoint
declares who may call it (``app.modules.identity.dependencies``): the anonymous ones are
``public`` (sign-in, the CSRF token, password reset, the invitation endpoints, the providers and
the OIDC flow), ``/me`` and
sign-out are for ``any_member`` (a session, no permission), and ``/agency`` needs
``workspace:read`` / ``agency:manage``. The CSRF middleware (``app.platform.csrf``) has already
checked every unsafe request before it gets here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service
from app.modules.identity.dependencies import (
    SERVICE_ERRORS,
    Permission,
    any_member,
    client_info,
    current_session,
    optional_session,
    public,
    require_permission,
)
from app.modules.identity.dependencies import http_error as _service_error
from app.modules.identity.schemas import OidcLoginError, OidcProvider, Principal, SignedIn
from app.modules.identity.schemas_v2 import (
    ActiveAgencyChange,
    Agency,
    AgencyUpdate,
    AuthProvider,
    AuthProviders,
    CsrfToken,
    InvitationAccept,
    InvitationPreview,
    InvitationToken,
    LoginRequest,
    Me,
    MeUpdate,
    OidcStart,
    OidcStartRequest,
    PasswordChange,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetRequested,
)
from app.platform import auth_cookies, csrf
from app.platform.config import settings
from app.platform.database import get_db
from app.platform.problems import problem_responses
from app.platform.ratelimit import RateLimitStore, rate_limit_store

auth_router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(prefix="/me", tags=["me"])
agency_router = APIRouter(prefix="/agency", tags=["agency"])

routers = (auth_router, me_router, agency_router)

WORKSPACE_READ = Depends(require_permission(Permission.workspace_read))
AGENCY_MANAGE = Depends(require_permission(Permission.agency_manage))


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


# ── /auth: password reset (E2-07) ─────────────────────────────
@auth_router.post(
    "/password-reset",
    operation_id="requestPasswordReset",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PasswordResetRequested,
    responses=problem_responses(422, 429),
    dependencies=[Depends(public)],
)
async def request_password_reset(
    payload: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
    store: RateLimitStore = Depends(rate_limit_store),
    client: service.ClientInfo = Depends(client_info),
) -> PasswordResetRequested:
    """Email a link to choose a new password, if an account exists for this address. The
    answer is the same either way (202), and so is its timing: the email is sent in the
    background. The link (`/reset-password#token=...`) works once, for 30 minutes. 429 with
    Retry-After after 3 requests per address or 10 per IP in an hour."""
    try:
        await service.request_password_reset(db, store, email=payload.email, client=client)
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None
    return PasswordResetRequested(detail=service.RESET_REQUESTED)


@auth_router.post(
    "/password-reset/confirm",
    operation_id="confirmPasswordReset",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=problem_responses(410, 422, 429),
    dependencies=[Depends(public)],
)
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    response: Response,
    db: AsyncSession = Depends(get_db),
    store: RateLimitStore = Depends(rate_limit_store),
    client: service.ClientInfo = Depends(client_info),
) -> None:
    """Choose a new password with the token from a reset link. Every session of the account
    ends (this browser's cookies are cleared too: sign in with the new password) and a
    confirmation email is sent. 410 when the link expired or was used; 422 when the password
    does not meet the policy (the link still works)."""
    try:
        await service.confirm_password_reset(
            db, store, token=payload.token, new_password=payload.new_password, client=client
        )
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None
    auth_cookies.clear_session_cookie(response)
    auth_cookies.clear_csrf_cookie(response)


# ── /auth: invitations (E2-07) ────────────────────────────────
@auth_router.post(
    "/invitations/preview",
    operation_id="previewInvitation",
    response_model=InvitationPreview,
    responses=problem_responses(410, 422, 429),
    dependencies=[Depends(public)],
)
async def preview_invitation(
    payload: InvitationToken,
    db: AsyncSession = Depends(get_db),
    store: RateLimitStore = Depends(rate_limit_store),
    client: service.ClientInfo = Depends(client_info),
) -> InvitationPreview:
    """What an invitation link offers: the agency, who sent it, the role and address, and
    whether an account exists for the address (then sign in before accepting). 410 when the
    invitation expired, was revoked, replaced or already accepted."""
    try:
        return await service.preview_invitation(db, store, payload.token, client=client)
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None


@auth_router.post(
    "/invitations/accept",
    operation_id="acceptInvitation",
    response_model=Me,
    responses=problem_responses(403, 409, 410, 422, 429),
    dependencies=[Depends(public)],
)
async def accept_invitation(
    payload: InvitationAccept,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    signed_in: SignedIn | None = Depends(optional_session),
    store: RateLimitStore = Depends(rate_limit_store),
    client: service.ClientInfo = Depends(client_info),
) -> Me:
    """Accept an invitation and start a new session in its agency (new session and CSRF
    cookies). Signed in: you join with your account, whose email must be the invited one (403
    otherwise). Not signed in: a new account is created from display_name and password (409
    if an account exists for the address: sign in first). 409 if you are already a member;
    410 when the invitation is no longer valid."""
    try:
        token = await service.accept_invitation(
            db,
            store,
            token=payload.token,
            signed_in=signed_in,
            carried_token=auth_cookies.session_cookie(request),
            display_name=payload.display_name,
            password=payload.password,
            client=client,
        )
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None
    csrf_token = _start(response, token)
    accepted = await service.authenticate(db, token)
    assert accepted is not None  # just created
    return await service.me(db, accepted, csrf_token)


# ── /auth: Google and Microsoft (E2-08) ───────────────────────
def _frontend(path: str) -> str:
    return settings.FRONTEND_URL.strip().rstrip("/") + path


CALLBACK_RESPONSES: dict[int | str, dict[str, Any]] = {
    302: {
        "description": "To the frontend: `FRONTEND_URL` + `next`, signed in (new session and "
        "CSRF cookies), or `FRONTEND_URL/login?error={OidcLoginError}&provider={provider}`."
    },
    **problem_responses(404, 422),
}


@auth_router.get(
    "/providers",
    operation_id="listAuthProviders",
    response_model=AuthProviders,
    dependencies=[Depends(public)],
)
async def list_auth_providers() -> AuthProviders:
    """The sign-in providers this deployment offers, for "Continue with Google / Microsoft"
    buttons. Only configured ones are listed; the others' endpoints answer 404."""
    return AuthProviders(
        providers=[
            AuthProvider(id=provider, name=name) for provider, name in service.oidc_providers()
        ]
    )


@auth_router.post(
    "/oidc/{provider}/start",
    operation_id="startOidcLogin",
    response_model=OidcStart,
    responses=problem_responses(404, 410, 422, 429, 502),
    dependencies=[Depends(public)],
)
async def start_oidc_login(
    provider: OidcProvider,
    payload: OidcStartRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    store: RateLimitStore = Depends(rate_limit_store),
    client: service.ClientInfo = Depends(client_info),
) -> OidcStart:
    """Start signing in with Google or Microsoft (OpenID Connect, authorization code + PKCE).
    Sets a short-lived, encrypted cookie for the flow (10 minutes); navigate the browser to
    `authorization_url`. The provider sends it back to `completeOidcLogin`. With
    `invitation_token`, signing in also accepts that invitation. 404 for a provider that is not
    configured (see `listAuthProviders`); 410 when the invitation is no longer valid; 429 with
    Retry-After when throttled; 502 when the provider cannot be reached."""
    try:
        started = await service.start_oidc_login(
            db,
            store,
            provider,
            next_path=payload.next,
            invitation_token=payload.invitation_token,
            client=client,
        )
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None
    auth_cookies.set_oidc_cookie(response, started.sealed_flow, service.OIDC_STATE_TTL)
    return OidcStart(authorization_url=started.authorization_url)


@auth_router.get(
    "/oidc/{provider}/callback",
    operation_id="completeOidcLogin",
    status_code=status.HTTP_302_FOUND,
    response_class=RedirectResponse,
    responses=CALLBACK_RESPONSES,
    dependencies=[Depends(public)],
)
async def complete_oidc_login(
    provider: OidcProvider,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
    store: RateLimitStore = Depends(rate_limit_store),
    client: service.ClientInfo = Depends(client_info),
) -> RedirectResponse:
    """Where Google or Microsoft send the browser back (not called by the frontend). Checks
    `state` against the flow's cookie, exchanges `code`, validates the ID token, finds the
    account (a linked identity, the invitation, or an existing account with the address the
    provider verified), then redirects: signed in to `next`, or to the login page with an
    `error` (`OidcLoginError`). Any session cookie the browser sent is revoked; the flow's
    cookie is deleted either way. 404 for a provider that is not configured."""
    try:
        outcome = await service.complete_oidc_login(
            db,
            store,
            provider,
            sealed_flow=auth_cookies.oidc_cookie(request),
            state=state,
            code=code,
            error=error,
            carried_token=auth_cookies.session_cookie(request),
            client=client,
        )
    except SERVICE_ERRORS as exc:
        raise _service_error(exc) from None
    if isinstance(outcome, service.OidcSignedIn):
        redirect = RedirectResponse(_frontend(outcome.next), status.HTTP_302_FOUND)
        _start(redirect, outcome.token)
    else:
        failure: OidcLoginError = outcome.error
        redirect = RedirectResponse(
            _frontend(f"/login?error={failure.value}&provider={provider.value}"),
            status.HTTP_302_FOUND,
        )
    auth_cookies.clear_oidc_cookie(redirect)
    return redirect


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
