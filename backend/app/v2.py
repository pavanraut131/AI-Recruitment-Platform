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
- **E2 (auth)** adds authentication to v2 alone: dependencies or middleware on this app, and its
  security schemes in this document, while v1 is removed rather than secured (backlog).

It includes the module-owned v2 routers (endpoints that need one module) and the composed ones
from ``app.api.v2`` (endpoints that span modules, ADR 0005). Nothing here may import v1 HTTP code
(import-linter contract ``v2-independent-of-v1``), so removing v1 is a deletion.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, iter_route_contexts

from app.api import v2 as composed
from app.modules.hiring import router_v2 as hiring
from app.modules.ingestion import router_v2 as ingestion
from app.modules.ingestion.schemas_v2 import ConnectedEvent, ResumeEvent
from app.platform.config import settings
from app.platform.problems import install_problem_handlers, problem_schemas

TITLE = "HireFlow AI API"
VERSION = "2.0.0"
DESCRIPTION = (
    "The HireFlow AI resource API. Lists return `{items, total, page, page_size}`; errors are "
    "RFC 9457 problem details (`application/problem+json`)."
)

TAGS: list[dict[str, Any]] = [
    {"name": "campaigns", "description": "Campaigns, their counts and stats, JD parsing."},
    {"name": "applications", "description": "A Person's application to a campaign."},
    {"name": "uploads", "description": "Resume uploads, processing status and live events."},
    {"name": "people", "description": "People across campaigns."},
    {"name": "dashboard", "description": "Workspace counts and the Home screen."},
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
    app.include_router(hiring.router)
    app.include_router(ingestion.router)
    for router in composed.routers:
        app.include_router(router)
    _check_operation_ids(app)

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
    return document


v2_app = create_v2_app()
