"""The ``/api/v2`` application (EPIC-01 spec §6), mounted by ``app.main`` at ``/api/v2``.

A FastAPI **sub-application** of its own, not routers on the root app, because:

- **v1 stays byte-identical.** Its errors keep FastAPI's ``{"detail": ...}`` shape and the root
  ``/openapi.json`` keeps exactly the v1 operations (both pinned by the characterization
  snapshots). The problem+json handlers (``app.platform.problems``) are installed on this app
  only, and a mounted app's routes are not part of the parent's OpenAPI document.
- **Its own OpenAPI document**, ``/api/v2/openapi.json`` (and ``/api/v2/docs``): the source of
  the generated TypeScript client (E1-03), which can also export it without a server
  (``app.v2.v2_app.openapi()``). It holds only v2 operations, with stable operation ids, and
  declares ``/api/v2`` as its server, so paths read ``/campaigns`` etc.
- **Authentication (E2-05)** is on v2 alone: every route needs a session
  (``app.modules.identity.dependencies.current_principal``, added to each router included here,
  or ``current_session`` for ``/me``), except the anonymous auth endpoints marked ``public``;
  :func:`_check_authentication` refuses to start otherwise (deny by default). The CSRF and
  security-header middlewares wrap this app only, and the document declares the ``cookieAuth``
  and ``csrfToken`` security schemes and the 401/403 problems of each protected operation
  (:func:`_add_security`). v1 is removed rather than secured (E2-12).

It includes the module-owned v2 routers (endpoints that need one module) and the composed ones
from ``app.api.v2`` (endpoints that span modules, ADR 0005). Nothing here may import v1 HTTP code
(import-linter contract ``v2-independent-of-v1``), so removing v1 is a deletion.
"""

from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, iter_route_contexts

from app.api import v2 as composed
from app.modules.hiring import router_v2 as hiring
from app.modules.identity import router_v2 as identity
from app.modules.identity.dependencies import (
    AUTH_DEPENDENCIES,
    current_principal,
    current_session,
)
from app.modules.ingestion import router_v2 as ingestion
from app.modules.ingestion.schemas_v2 import ConnectedEvent, ResumeEvent
from app.platform import csrf
from app.platform.auth_cookies import CSRF_COOKIE_BASE, HOST_PREFIX, SESSION_COOKIE_BASE
from app.platform.config import settings
from app.platform.csrf import CSRFMiddleware
from app.platform.problems import install_problem_handlers, problem_responses, problem_schemas
from app.platform.security_headers import SecurityHeadersMiddleware

TITLE = "HireFlow AI API"
VERSION = "2.0.0"
DESCRIPTION = (
    "The HireFlow AI resource API. Lists return `{items, total, page, page_size}`; errors are "
    "RFC 9457 problem details (`application/problem+json`).\n\n"
    "Authentication: a server-side session in the HttpOnly cookie "
    f"`{HOST_PREFIX}{SESSION_COOKIE_BASE}` (`{SESSION_COOKIE_BASE}` in development over http), "
    "started by `login`. Unsafe requests (POST, PUT, PATCH, DELETE) also send the CSRF token in "
    f"the `{csrf.HEADER}` header: from `getCsrfToken` before sign-in, then `Me.csrf_token` or "
    f"the `{HOST_PREFIX}{CSRF_COOKIE_BASE}` cookie."
)

TAGS: list[dict[str, Any]] = [
    {"name": "campaigns", "description": "Campaigns, their counts and stats, JD parsing."},
    {"name": "applications", "description": "A Person's application to a campaign."},
    {"name": "uploads", "description": "Resume uploads, processing status and live events."},
    {"name": "people", "description": "People across campaigns."},
    {"name": "dashboard", "description": "Workspace counts and the Home screen."},
    {"name": "auth", "description": "Sign-in, sign-out and the CSRF token."},
    {"name": "me", "description": "The signed-in user: profile, password, active agency."},
    {"name": "agency", "description": "The active agency."},
]

# Schemas the document lists although no operation's JSON body refers to them: the problem
# details (referenced from every error response) and the SSE event payloads (sent inside
# text/event-stream), so the generated client has their types.
EXTRA_SCHEMAS = (ConnectedEvent, ResumeEvent)


def _operation_id(route: APIRoute) -> str:
    """Every v2 operation must name its operation id: the generated client's function names
    depend on it. It also names generated schemas (e.g. ``Body_uploadResumes``)."""
    if not route.operation_id:
        raise RuntimeError(f"/api/v2 route {route.path} has no explicit operation_id")
    return route.operation_id


def _check_operation_ids(app: FastAPI) -> None:
    """Run ``_operation_id`` for every route now, so a missing id fails at import (start-up),
    not when the document is first generated. Since FastAPI 0.137 ``include_router`` keeps the
    included routes instead of copying them, and their effective settings (the operation id
    among them) are resolved when the routes are first iterated."""
    for _ in iter_route_contexts(app.routes):
        pass


# ── Authentication ────────────────────────────────────────────
def _calls(dependant: Dependant | None) -> set[Callable[..., Any]]:
    """Every dependency callable of a route, recursively."""
    if dependant is None:
        return set()
    found: set[Callable[..., Any]] = set()
    for sub in dependant.dependencies:
        if sub.call is not None:
            found.add(sub.call)
        found |= _calls(sub)
    return found


def _auth_of(route: Any) -> Callable[..., Any] | None:
    """The route's authentication dependency: ``current_principal``, ``current_session`` or the
    ``public`` marker (in that order of precedence), or ``None``."""
    calls = _calls(getattr(route, "dependant", None))
    return next((dep for dep in AUTH_DEPENDENCIES if dep in calls), None)


def _check_authentication(app: FastAPI) -> None:
    """Deny by default (spec §5.4): every v2 route requires a session, unless it is explicitly
    ``public``. Refuses to start otherwise."""
    for route in iter_route_contexts(app.routes):
        if isinstance(route.original_route, APIRoute) and _auth_of(route) is None:
            raise RuntimeError(
                f"/api/v2 route {route.path} requires no session and is not marked public"
            )


def create_v2_app() -> FastAPI:
    app = FastAPI(
        title=TITLE,
        version=VERSION,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        servers=[{"url": settings.API_V2_PREFIX}],
        root_path_in_servers=False,
        generate_unique_id_function=_operation_id,
    )
    install_problem_handlers(app)
    # Outermost last: the security headers also go on the CSRF middleware's 403.
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    for router in identity.routers:  # each endpoint declares its own session or public marker
        app.include_router(router)
    signed_in = [Depends(current_principal)]
    app.include_router(hiring.router, dependencies=signed_in)
    app.include_router(ingestion.router, dependencies=signed_in)
    for router in composed.routers:
        app.include_router(router, dependencies=signed_in)
    _check_operation_ids(app)
    _check_authentication(app)

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = _openapi_document(app)
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


def _openapi_document(app: FastAPI) -> dict[str, Any]:
    document = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
        servers=app.servers,
    )
    schemas: dict[str, Any] = document.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(problem_schemas())
    for model in EXTRA_SCHEMAS:
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
        for name, definition in schema.pop("$defs", {}).items():
            schemas.setdefault(name, definition)
        schemas[model.__name__] = schema
    _add_security(app, document)
    return document


SECURITY_SCHEMES: dict[str, Any] = {
    "cookieAuth": {
        "type": "apiKey",
        "in": "cookie",
        "name": f"{HOST_PREFIX}{SESSION_COOKIE_BASE}",
        "description": "The session cookie set by `login` (HttpOnly). Named "
        f"`{SESSION_COOKIE_BASE}` when the API runs without Secure cookies (development over "
        "http).",
    },
    "csrfToken": {
        "type": "apiKey",
        "in": "header",
        "name": csrf.HEADER,
        "description": "The CSRF token (`getCsrfToken`, `Me.csrf_token` or the CSRF cookie), "
        "required on POST, PUT, PATCH and DELETE.",
    },
}


def _add_security(app: FastAPI, document: dict[str, Any]) -> None:
    """Declare the security schemes, and on each operation which ones it needs and the problems
    they cause: 401 without a valid session; 403 for a missing or invalid CSRF token (unsafe
    methods) and, on agency-scoped operations, for a session without an active agency."""
    document["components"]["securitySchemes"] = SECURITY_SCHEMES
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or not route.include_in_schema:
            continue
        auth = _auth_of(route)
        for method in route.methods or ():
            operation = document["paths"].get(route.path_format, {}).get(method.lower())
            if operation is None:
                continue
            requirement: dict[str, list[str]] = {}
            statuses: list[int] = []
            if auth in (current_principal, current_session):
                requirement["cookieAuth"] = []
                statuses.append(401)
                if auth is current_principal:
                    statuses.append(403)
            if method in csrf.UNSAFE_METHODS:
                requirement["csrfToken"] = []
                statuses.append(403)
            if requirement:
                operation["security"] = [requirement]
            responses = operation.setdefault("responses", {})
            for status, response in problem_responses(*statuses).items():
                responses.setdefault(str(status), response)
            operation["responses"] = dict(sorted(responses.items()))


v2_app = create_v2_app()
