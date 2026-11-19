# 0006 · Contract-first API client: OpenAPI → TypeScript, checked in CI
**Status:** accepted  ·  **Date:** 2026-11-17

## Context
The new frontend (E1) talks only to `/api/v2` ([spec §6](../specs/EPIC-01-frontend.md#6-api-apiv2)), a FastAPI sub-application with its own OpenAPI document ([ADR 0008](0008-api-v2-sub-application.md)). The old UI's client, `src/lib/api.ts`, is axios with hand-written interfaces. Its types had already drifted from v1 (`Candidate` fields the API no longer sends, optional fields that are always present), and nothing noticed: a wrong type compiles and fails at run time.

Forces:
- **One developer, one repo.** The backend and frontend change in the same PR. A contract change should be visible in that PR's diff, and a forgotten client update should fail CI rather than production.
- **The contract already exists.** FastAPI derives the document from the pydantic models. v2 has stable camelCase `operation_id`s, enums as OpenAPI enums, one `Problem` schema for every error, and the SSE payloads (`ResumeEvent`, `ConnectedEvent`) listed in its components.
- **Errors are RFC 9457 problem details.** Every screen needs the same handling: a toast or an error state with `detail`, field errors on forms, and retries only where they can help.
- **Small runtime.** The client runs in the browser on every screen.

## Decision
- **The backend exports the document offline.** `python -m app.devtools.export_openapi` (`make api-client`) writes `app.v2.v2_app.openapi()` to `frontend/src/lib/api/openapi.v2.json`, with sorted keys, a two-space indent and a trailing newline, so the same contract always gives the same bytes. It sets dummy database URLs and `AI_PROVIDER=fake` before importing the app, so no database, Redis or Gemini key is needed. Importing the app connects to nothing.
- **Types are generated, never written.** `openapi-typescript` (pinned, dev) turns the JSON into `src/lib/api/schema.d.ts` (`npm run api:generate`). It is types only, so it adds no runtime code. Both files are committed. A reviewer sees a contract change as a diff of the document and its types, next to the backend change that caused it.
- **The runtime is `openapi-fetch`** (pinned). `client.ts` builds one client on `NEXT_PUBLIC_API_URL + /api/v2`. Calls are typed by path and method (`api.GET("/campaigns/{campaign_id}", { params: { path: { campaign_id } } })`), including path and query parameters, request bodies and response bodies.
- **Errors are one class.** A middleware turns every failure into an `ApiProblem` and throws it, so query functions get the 2xx body or an exception:
  - a non-2xx response is parsed from its `application/problem+json` body (`status`, `title`, `type`, `detail`, `errors`); a body that is not a problem (a proxy's HTML 502) still gives a problem built from the status line;
  - a request that got no response becomes `status: 0` (`isNetworkError`);
  - an aborted request (Query cancelling) is rethrown unchanged.

  `fieldErrors()` maps `errors[].loc` to react-hook-form paths (`["body", "required_skills", 0]` → `required_skills.0`). Query's retry policy and the toasts read `isNetworkError` / `isClientError` / `isServerError` ([ADR 0007](0007-frontend-state-model.md)).
- **CI drift check.** The `frontend` job installs the backend with uv, runs the export and the generator, and fails on `git diff --exit-code -- src/lib/api/`. A backend change that alters the contract therefore needs `make api-client` and a commit in the same PR. Any frontend code that the change breaks then fails `tsc` in that PR too.
- **Query keys** live next to the client (`query-keys.ts`), one hierarchical factory per resource, typed from the generated query parameters.
- New code may not import `axios`, `react-toastify` or the legacy `@/lib/api`. ESLint enforces this in `src/features/**`, `src/lib/api/**` and `src/lib/query/**`. `src/lib/api.ts` stays, used only by `src/app/(legacy)`, until E1-09 removes the old UI.
  *Update (E1-10):* the old UI, `src/lib/api.ts` and `axios` are deleted; the frontend calls only `/api/v2`, and the ESLint ban on `axios` / `react-toastify` now covers all of `src`.

## Alternatives considered
- **Hand-written types and fetch wrappers** (the v1 approach). This needs no tooling, but it drifts silently, which is the problem we already have.
- **Full SDK generators** (`openapi-generator`, `orval`, `@hey-api/openapi-ts`). They generate a function per operation, and some generate Query hooks. That means much more generated code to review, generated runtime code in the bundle, and generated hooks that hide the cache keys and invalidation, which we want to own ([ADR 0007](0007-frontend-state-model.md)). orval's hooks were the closest call. We may revisit them if writing hooks by hand gets repetitive.
- **Fetching the document from a running server in CI** (`/api/v2/openapi.json`). This needs Postgres, Redis and the app booted just to read a static document. The offline export gives the same document: FastAPI serves `/api/v2/openapi.json` from `v2_app.openapi()`.
- **Generating at build time instead of committing the output.** Contract changes would then be invisible in PRs, and the frontend build would need Python. Committing the files and checking for drift keeps both builds independent.
- **axios with generated types.** Its interceptors could do the problem mapping, but it is a larger runtime that duplicates `fetch`, and it is the library we are removing.
- **tRPC / GraphQL.** Rejected: the backend is FastAPI, and the API should stay a plain HTTP resource API for agencies' integrations later.

## Consequences
- **Easier:** types cannot drift from the backend without CI failing. A contract change shows up in review as JSON and TypeScript diffs. Error handling is written once. The client is a small runtime (`openapi-fetch`) plus types.
- **Harder:** a backend PR that touches a v2 schema must run `make api-client` (which needs Node as well as Python). The `frontend` CI job now sets up uv and installs the backend's runtime dependencies (about a minute, cached).
- **Watch for:**
  - **Generator output must stay stable.** It changes when `openapi-typescript` changes, which is why it is pinned. Bump it in its own PR and commit the regenerated file.
  - **Fields with server defaults.** `openapi-typescript` treats a property with a `default` as required. That is right for responses, which always carry such fields (counts, `Problem.type`), but it also makes request bodies require their defaulted fields: `CampaignCreate.status` and `required_skills` must be sent ([backlog](../backlog.md#platform-and-tooling)).
  - **`Problem.detail` and `errors` are typed as present, but may be omitted.** The backend leaves out `null`s. Use `ApiProblem`'s normalised fields, not the raw `Problem` type.
  - **No runtime validation.** Responses are trusted to match the types. The SSE payloads are the exception: they arrive outside `openapi-fetch` and are checked by `parseResumeEvent`.
