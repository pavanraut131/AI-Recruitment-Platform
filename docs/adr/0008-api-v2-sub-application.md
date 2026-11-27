# 0008 · `/api/v2` as a mounted sub-application with its own OpenAPI document
**Status:** accepted  ·  **Date:** 2026-11-17  ·  **Note:** v1 removed in E2-12 (2026-11-27), see the end

## Context
E1 builds a resource API, `/api/v2` ([spec §6](../specs/EPIC-01-frontend.md#6-api-apiv2)), for the new frontend, next to the frozen `/api/v1`. Four forces:

- **v1 must stay byte-identical.** Its 179 characterization snapshots include every error body (FastAPI's `{"detail": ...}`) and the whole root `/openapi.json`. v2 errors must be RFC 9457 problem details instead.
- **The TypeScript client is generated from OpenAPI** (E1-03, ADR 0006). It should see only v2 operations, with stable operation ids and clean schema names, and CI must be able to export the document without a running server.
- **E2 adds authentication**, and v1 is then removed rather than secured (backlog). Whatever carries v2 should take auth on its own.
- **Module boundaries** ([ADR 0001](0001-modular-monolith.md), [ADR 0005](0005-composition-layer.md)): v2 routers are thin; endpoints that need two modules live in `app.api`.

## Decision
- `/api/v2` is a **separate FastAPI application** (`app/v2.py`, `v2_app`) that `app.main` **mounts** at `/api/v2`. It has its own OpenAPI document at `/api/v2/openapi.json` (docs at `/api/v2/docs`), declaring `servers: [{url: "/api/v2"}]` so paths read `/campaigns`, and the same document comes from `app.v2.v2_app.openapi()` offline. A mounted app's routes are not part of the parent's document, so the root `/openapi.json` is unchanged.
- **Problem details** (`app/platform/problems.py`) are installed on `v2_app` only: `HTTPException`s, request validation errors (422 with `errors: [{loc, msg}]`) and unhandled exceptions (500, no URL or exception text) become `application/problem+json`. Every error response in the document references one `Problem` schema; FastAPI's `HTTPValidationError` is never used.
- **Every v2 operation names an explicit camelCase `operation_id`**; the app's `generate_unique_id_function` refuses a route without one (it also names generated schemas, e.g. `Body_uploadResumes`).
- **Where the code lives:** endpoints that need one module are in that module's `router_v2.py`; endpoints that span modules are in `app/api/v2/` (ADR 0005). A module's v2 resources are in `schemas_v2.py`, public like `schemas.py` (import-linter allows `app.modules.<name>.schemas_v2`), so class names (= OpenAPI component names) can be plain (`Campaign`, `CampaignCreate`) without clashing with the frozen v1 ones. Lists use one envelope, `app/platform/pagination.Page[T]`, subclassed per resource (`CampaignPage`).
- A new import-linter contract, **`v2-independent-of-v1`**, forbids v2 HTTP code (`app.v2`, `app.api.v2`, `app.modules.*.router_v2`) from importing v1 routers or `app.main`, so removing v1 is a deletion.

## Alternatives considered
- **v2 routers on the root app with a path-scoped exception handler** (dispatch on `request.url.path.startswith("/api/v2")`): one document for both versions, so the v1 OpenAPI snapshot changes with every v2 endpoint and the client sees v1 operations; and the handler has to special-case paths, which E2's auth would repeat.
- **Root app, but hide v1 from the schema (`include_in_schema=False`)**: changes the v1 snapshot, and FastAPI's 422 handler and response shape are app-wide anyway.
- **A separate service/process for v2**: real isolation, but a second deployable for one developer, and both versions share every module service and the database (ADR 0001).
- **A `v2` subpackage per module** (`hiring/v2/router.py`, `hiring/v2/schemas.py`) instead of `router_v2.py` / `schemas_v2.py`: equivalent, but two files per module do not need a package, and side-by-side files keep v1 and v2 of a module visible together until v1 goes.

## Consequences
- **Easier:** v1 is provably untouched (its snapshots, and its document, are byte-identical); the client generator gets a v2-only document with stable ids; E2 can add auth to `v2_app` (global dependencies, security schemes in its document) without touching v1; deleting v1 after E2 is removing `app.api.routes`, the module `router.py` files and their snapshots.
- **Harder:** a mounted app resolves its own dependency overrides and exception handlers, so the test `client` fixture overrides `get_db` on both apps, and an unhandled v2 exception is logged by the root app's handler (Starlette re-raises after sending the problem). Mounted apps get no lifespan events (v2 needs none today). Starlette's 405 lists only the methods of the first matching route (`PUT /campaigns` answers `Allow: POST` though `GET` exists too), because the v2 campaign routes are split over the hiring and composed routers.
- **Watch for:** schema names must stay unique within the v2 document (pydantic would module-qualify duplicates, which the client would expose); the OpenAPI contract snapshot (`tests/contract_v2`) catches it. Any new top-level package under `app/` must join the `llm-sdk-only-in-ai` contract, as `app.v2` did.

## Note: v1 removed in E2-12 (2026-11-27)
`/api/v1` was removed as this ADR planned: a deletion (`app.api.routes`, the module `router.py` files and v1's schemas, the `/api/v1` characterization tests, `API_V1_PREFIX`), and its paths now answer 404. v2 needed no change; its contract snapshots and OpenAPI document are byte-identical. The `v2-independent-of-v1` contract went with its targets. The decision itself stands: `/api/v2` stays a mounted sub-application with its own document and problem handlers, and the root app keeps only the health checks. One consequence above changed: the root app's catch-all exception handler (v1's, which echoed the URL: B10) is gone, so v2's own 500 handler (`app.platform.problems`) now logs `unhandled_exception` itself.
