# Sprint 5 · HireFlow AI foundation

**Epic:** [E1 · Product design & frontend foundation](../specs/EPIC-01-frontend.md)  ·  **Dates:** 2026-11-17 → 2026-11-23  ·  **Status:** in progress

## Sprint goal

> A recruiter opens **HireFlow AI** and sees the new design system and app shell. They can create a campaign (with AI extraction from a job description) and review its candidates in the new ranked table. Everything runs on `/api/v2` through a client generated from OpenAPI, which CI keeps in sync.

This sprint builds the foundation from spec §10 (E1-01 to E1-05). The rest of the hero flow follows in Sprint 6: uploads, the review panel and Person profile, Home and Insights, and E2E tests (E1-06 to E1-09).

## Board

| Ticket | Title | Size | Status |
|---|---|---|---|
| B4 | Upgrade python-multipart (upload parsing DoS) | S | done ([#35](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/35)) |
| E1-01 | Design tokens, shadcn/ui, app shell, `/dev/ui`; rebrand to HireFlow AI | M | done ([#34](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/34)) |
| E1-02 | Backend `/api/v2`: campaigns, applications, uploads, people, home | L | done ([#36](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/36)) |
| E1-03 | Generated API client and CI drift check; Query, problem+json, SSE hook | M | review |
| E1-04 | Campaign list and new campaign (JD extraction) | M | todo |
| E1-05 | Candidates table: columns, URL filters, sort, pagination, bulk actions, export | L | todo |

**Order** (stacks no more than two deep, as agreed in the Sprint 4 retro):
- **Wave 1:** B4, E1-01 and E1-02, all independent and branched from `master`.
- **Wave 2:** E1-03, after E1-01 and E1-02 merge.
- **Wave 3:** E1-04 and E1-05 in parallel, after E1-03 merges. They're in separate feature folders.

**Merge method:** squash is fine for leaf PRs. Use **"Create a merge commit"** for any PR that has another PR stacked on it.

---

### B4 · Upgrade python-multipart
**Status:** done ([#35](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/35))  ·  **Size:** S  ·  **Branch:** `b4-python-multipart`

**Goal:** Close the upload-parsing DoS ([backlog](../backlog.md)). python-multipart 0.0.9 parses some 10 MB uploads byte by byte, taking about 30 s of CPU per file.

**Acceptance criteria**
- [x] Upgrade python-multipart to the current release, and FastAPI and Starlette if needed for compatibility. The lockfile is updated.
- [x] A regression test uploads a 10 MB PDF made of characters that look like a multipart boundary. It parses in under 2 s and is skipped in CI if timing is unreliable (document why).
- [x] All upload characterization snapshots pass unchanged.

**Notes:** python-multipart 0.0.9 → 0.0.34; FastAPI 0.111.0 and Starlette 0.37.2 unchanged (they still import the old `multipart` name, which 0.0.34 keeps as a shim). Through the endpoint, a 10 MB upload of repeated `\r\n--` took ~580 s before and ~0.05 s of CPU after. The test bounds CPU time (`time.process_time`), not wall-clock time, so it is reliable enough to run in CI. Starlette's own multipart advisories need a FastAPI upgrade: [backlog](../backlog.md#platform-and-tooling).

---

### E1-01 · Design system and app shell
**Status:** done ([#34](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/34))  ·  **Size:** M  ·  **Branch:** `e1-01-design-system`

**Goal:** The visual foundation from spec §3–§4: tokens, components, the shell and the rebrand.

**Acceptance criteria**
- [x] `src/styles/tokens.css` follows the shadcn variable convention and adds `--category-*`, with light and dark themes. Zinc neutrals and a teal accent. Mapped into Tailwind v4 `@theme`. **No hex values in components.**
- [x] shadcn/ui is initialised. The primitives needed by E1 live in `src/components/ui`: button, input, textarea, select, badge, dialog, sheet, dropdown-menu, command, tabs, table, tooltip, skeleton, sonner, form, checkbox, popover, separator, scroll-area.
- [x] **App shell** (`src/components/app-shell`):
  - a collapsible sidebar with a workspace slot (E2 placeholder), Home, Campaigns and Talent, plus Settings and a theme toggle;
  - a top bar with breadcrumbs;
  - a **⌘K command palette** with navigation and "New campaign" (data search arrives with E1-03).
- [x] `next-themes`: system, light and dark, with no flash on load.
- [x] A `/dev/ui` gallery shows every primitive in every state, in light and dark. It's excluded from production builds.
- [x] **Rebrand to HireFlow AI:** app title, metadata, favicon, README heading, and the `make setup` message. The old screens stay reachable but are wrapped in the new shell, until E1-04 to E1-08 replace them.
- [x] `axios` and `react-toastify` are not used by any new code, and an ESLint rule bans them in `features/`. The old components are removed as their screens are replaced.
- [x] An ESLint `no-restricted-imports` rule for feature-folder boundaries (spec §7).
- [x] WCAG AA contrast is checked for both themes, with the results in the PR.

---

### E1-02 · Backend `/api/v2`
**Status:** done ([#36](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/36))  ·  **Size:** L  ·  **Branch:** `e1-02-api-v2`

**Goal:** The resource API from spec §6, as thin routers over the existing module services.

**Acceptance criteria**
- [x] Every endpoint in the spec §6 table is implemented. v2 routers live with their modules or in `app/api` for composed endpoints. **No business logic in routers.**
- [x] List endpoints return `{ items, total, page, page_size }`. Errors use RFC 9457 `application/problem+json` (one exception handler for `/api/v2`, with validation errors mapped to field paths).
- [x] Explicit, stable `operation_id`s. Enums (category, stage, resume status, campaign status) appear in OpenAPI as enums.
- [x] `GET /api/v2/people/{id}` returns the person with all applications and conflict flags. `GET /api/v2/home` aggregates the Home screen in one call.
- [x] **Contract snapshots** for every v2 endpoint, including errors, in the same pattern as v1. **The v1 snapshots stay byte-identical.**
- [x] import-linter stays green. Query counts are bounded (no N+1) on list endpoints, asserted in tests.

**Notes:** `/api/v2` is a mounted sub-application with its own OpenAPI document (`/api/v2/openapi.json`), [ADR 0008](../adr/0008-api-v2-sub-application.md); the final endpoint table, shapes and query counts are in [spec §6.1](../specs/EPIC-01-frontend.md#61-as-built-e1-02). v2 fixes B5, B7, B8, B9 and part of B10 (v1 keeps them).

---

### E1-03 · API client and data layer
**Status:** review  ·  **Size:** M  ·  **Branch:** `e1-03-data-layer`

**Goal:** A typed, contract-first data layer for every feature (spec §7).

**Acceptance criteria**
- [x] `openapi-typescript` generates `src/lib/api/schema.d.ts` from the backend's OpenAPI, using a `make api-client` / `npm run api:generate` script that exports the schema without a running server.
- [x] `src/lib/api/client.ts` uses `openapi-fetch` with problem+json parsing into a typed error. `query-keys.ts` holds one key factory per resource.
- [x] **CI drift check:** the `frontend` job regenerates the client and fails on `git diff --exit-code`.
- [x] TanStack Query provider with defaults (retry policy, stale times) and devtools in dev. Error toasts via sonner.
- [x] A `useCampaignEvents(campaignId)` hook: one `EventSource` that updates the Query cache, reconnects on drop, and closes on unmount.
- [x] Vitest + Testing Library set up. Tests cover the client's error mapping, the query keys and the SSE hook (with a mocked EventSource).
- [x] ADR 0006 *Contract-first API client* and ADR 0007 *Frontend state model*.

**Notes:**
- **Export and generation.** `python -m app.devtools.export_openapi` writes `v2_app.openapi()` to `frontend/src/lib/api/openapi.v2.json` (sorted keys, stable format, dummy settings and `AI_PROVIDER=fake`, so no database, Redis or Gemini). `npm run api:generate` (openapi-typescript 7.13.0, pinned) writes `schema.d.ts`. `make api-client` runs both. The CI `frontend` job runs the same steps with uv and fails on `git diff --exit-code -- src/lib/api/`. Proved by renaming `Resume.size_bytes` locally: the check failed with the JSON and `schema.d.ts` diff, and `tsc` failed on code that used the old field.
- **Client.** `src/lib/api/client.ts`: openapi-fetch 0.17.0 (pinned). Errors are an `ApiProblem` (`status`, `title`, `type`, `detail`, `errors`, `fieldErrors()`), network failures have status 0, and aborts pass through. `query-keys.ts` has factories for campaigns, applications, resumes, people, home and stats. Request bodies with server defaults come out required in TypeScript (`CampaignCreate.status`, `required_skills`; [backlog](../backlog.md#platform-and-tooling)).
- **Query.** `src/lib/query/`: `DataProviders` in the root layout (Query and the nuqs adapter, added here so that E1-04 and E1-05 don't both edit the layout). Defaults: 30 s stale time, retry only network/5xx (up to 3), refetch on focus. Mutations, and failed background refetches, show a sonner toast with the problem's detail. Devtools in dev only.
- **`useCampaignEvents`** lives in `src/lib/query/` rather than `features/uploads`, because the workspace (E1-04) and the Uploads tab (E1-06) both mount it. It patches resume lists, debounces invalidation of applications, campaign and stats on `done`, reconnects with backoff (1 s → 30 s, jittered, then resyncs) and closes on unmount. It keeps one connection under StrictMode.
- **Tests.** Vitest 5 + Testing Library + jsdom, 39 tests (client error mapping, query keys, Query defaults and toasts, the SSE hook with a mocked `EventSource`). `@/` is resolved by Vite 8's native `resolve.tsconfigPaths`, and JSX by its built-in transform. `@vitejs/plugin-react` is not used: it only adds Fast Refresh, and its optional Babel 8 peer clashes with shadcn's Babel 7 in npm's resolver.
- **Node 22.** The frontend Dockerfile and the CI job moved to Node 22 LTS (backlog item closed). `engines` is `>=22.22.2`, the floor of vitest 5 and jsdom 31.
- ADRs [0006](../adr/0006-contract-first-api-client.md) and [0007](../adr/0007-frontend-state-model.md). New code (`src/features`, `src/lib/api`, `src/lib/query`) may not import `axios`, `react-toastify` or the legacy `@/lib/api`. The legacy client stays for `src/app/(legacy)`.

---

### E1-04 · Campaigns: list and new campaign
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e1-04-campaigns`

**Goal:** `/campaigns` and `/campaigns/new` on the new foundation (spec §4).

**Acceptance criteria**
- [ ] The list table shows title, role, status, applications, strong matches, processing and last activity, with a status filter and search. Both are kept in the URL.
- [ ] The new-campaign form uses react-hook-form + zod: title, role, description and required-skills chips. **"Extract with AI"** pastes a JD into `POST /api/v2/campaigns/parse-jd`, fills role and skills, and has loading and error states.
- [ ] The campaign workspace shell `/campaigns/[id]` has a header, a KPI strip and tabs (Candidates, Uploads, Insights, Settings). Uploads and Insights are placeholders until Sprint 6. Settings has edit, status and delete with confirmation.
- [ ] Empty, loading and error states for every view. Vitest tests for the form and the URL state.

---

### E1-05 · Candidates table
**Status:** todo  ·  **Size:** L  ·  **Branch:** `e1-05-candidates-table`

**Goal:** The hero table from spec §5.1.

**Acceptance criteria**
- [ ] TanStack Table columns: select, name/email, score (number plus a bar in the category colour), category badge, top 3 skills (required skills highlighted), experience, stage, updated.
- [ ] Default sort by score descending, nulls last. Server-side sort and pagination through `/api/v2`.
- [ ] Toolbar: search, category multi-select, score range, skill, stage. **All kept in the URL with nuqs**, and they survive a reload.
- [ ] Selection with bulk actions: move stage, reject and export CSV, applied optimistically with rollback on error.
- [ ] Clicking a row opens a placeholder panel (the full review panel is E1-07). **J/K** keyboard navigation between rows.
- [ ] Smooth with **500 rows** on a page (measured and noted in the PR). Empty, loading and error states.
- [ ] Vitest tests for columns, filters ↔ URL and bulk actions.

---

## Sprint 6 (rough backlog)

E1-06 Uploads tab · E1-07 Review panel and Person profile · E1-08 Home, Insights and Talent · E1-09 Playwright E2E + axe in CI, polish, screenshots, removal of the old UI.

## Sprint review & retro

*(To be filled in at the end of the sprint.)*

- **Demo:**
- **Keep:**
- **Drop:**
- **Try:**
