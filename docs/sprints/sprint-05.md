# Sprint 5 · HireFlow AI foundation

**Epic:** [E1 · Product design & frontend foundation](../specs/EPIC-01-frontend.md)  ·  **Dates:** 2026-11-17 → 2026-11-23  ·  **Status:** done (closed 2026-11-18)

## Sprint goal

> A recruiter opens **HireFlow AI** and sees the new design system and app shell. They can create a campaign (with AI extraction from a job description) and review its candidates in the new ranked table. Everything runs on `/api/v2` through a client generated from OpenAPI, which CI keeps in sync.

This sprint builds the foundation from spec §10 (E1-01 to E1-05). The rest of the hero flow follows in Sprint 6: uploads, the review panel and Person profile, Home and Insights, and E2E tests (E1-06 to E1-09).

## Board

| Ticket | Title | Size | Status |
|---|---|---|---|
| B4 | Upgrade python-multipart (upload parsing DoS) | S | done ([#35](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/35)) |
| S1 | Upgrade FastAPI and Starlette (security) | M | done ([#38](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/38)) |
| E1-01 | Design tokens, shadcn/ui, app shell, `/dev/ui`; rebrand to HireFlow AI | M | done ([#34](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/34)) |
| E1-02 | Backend `/api/v2`: campaigns, applications, uploads, people, home | L | done ([#36](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/36)) |
| E1-03 | Generated API client and CI drift check; Query, problem+json, SSE hook | M | done ([#37](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/37)) |
| E1-04 | Campaign list and new campaign (JD extraction) | M | done ([#39](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/39)) |
| E1-05 | Candidates table: columns, URL filters, sort, pagination, bulk actions, export | L | done ([#40](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/40)) |

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
**Status:** done ([#37](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/37))  ·  **Size:** M  ·  **Branch:** `e1-03-data-layer`

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
**Status:** done ([#39](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/39))  ·  **Size:** M  ·  **Branch:** `e1-04-campaigns`

**Goal:** `/campaigns` and `/campaigns/new` on the new foundation (spec §4).

**Acceptance criteria**
- [x] The list table shows title, role, status, applications, strong matches, processing and last activity, with a status filter and search. Both are kept in the URL.
- [x] The new-campaign form uses react-hook-form + zod: title, role, description and required-skills chips. **"Extract with AI"** pastes a JD into `POST /api/v2/campaigns/parse-jd`, fills role and skills, and has loading and error states.
- [x] The campaign workspace shell `/campaigns/[id]` has a header, a KPI strip and tabs (Candidates, Uploads, Insights, Settings). Uploads and Insights are placeholders until Sprint 6. Settings has edit, status and delete with confirmation.
- [x] Empty, loading and error states for every view. Vitest tests for the form and the URL state.

**Notes:**
- **Where it lives.** `src/features/campaigns/`: `list/`, `new/`, `workspace/`, shared `components/`, query hooks in `api.ts`, the zod schema in `schema.ts`. Routes import only from its `index.ts`. Routes: `src/app/campaigns/{page,new/page,[id]/page}.tsx`, all thin. The old `(legacy)/campaigns/**` pages, `CandidateTable`, `StatsPanel`, `UploadZone` and their `legacy.css` rules are deleted. The legacy home and candidate pages still link to `/campaigns…`, which now opens the new screens.
- **URL state (nuqs).** List: `?status=active,closed&q=…&page=2`. Defaults are left out; statuses are kept in enum order, so one filter is one query key; the search writes the URL debounced (300 ms) and fetches with the debounced value; any filter change goes back to page 1. Workspace: `?tab=uploads|insights|settings`, with candidates the default (left out); a tab change is a history entry. E1-05 must not use `tab`.
- **Candidates slot.** The workspace takes `candidatesTab: ReactNode`; `src/app/campaigns/[id]/page.tsx` passes `<CandidatesPlaceholder />`. Wiring E1-05 in is one line there (`candidatesTab={<CandidatesTab campaignId={id} />}`), so `features/campaigns` never imports `features/applications`.
- **Live updates.** The workspace mounts `useCampaignEvents(campaignId)` once, enabled only after the campaign has loaded (no reconnect loop for an unknown id), and shows Live / Reconnecting in the header. The KPI strip reads the campaign's counts plus `avg_score` from `getCampaignStats`; both refresh from the hook's invalidations.
- **Form.** The fields are shared by create and Settings; the zod limits mirror `CampaignCreate` / `CampaignUpdate`. "Extract with AI" fills the role, merges the skills (case-insensitively, no duplicates), copies the JD into an empty description when it fits (≤ 5,000 characters) and shows the seniority it found. A 422 is mapped with `ApiProblem.fieldErrors()` onto the fields (`required_skills.N` goes on the skills field as "Skill N+1: …"); any other failure is one form-level alert. These mutations set `meta.errorToast: false`, so an error is not shown twice. `status` and `required_skills` are always sent (ADR 0006).
- **Mutations and cache.** Create and update write the returned campaign into `campaigns.detail(id)` and invalidate the campaign lists, `home` and `stats`. Delete marks the campaign's detail, applications and resumes stale without refetching (the workspace is still mounted while it navigates away), invalidates lists, home, stats and people, toasts and goes to `/campaigns`. The confirmation is the new shadcn `alert-dialog` primitive (also in `/dev/ui`); it stays open until the request settles.
- **Header actions.** Edit and Upload resumes switch tabs. Export is left to the Candidates toolbar (E1-05).
- **Tests.** 25 new Vitest tests (64 in total), with a stubbed `fetch` and nuqs's testing adapter: form validation, skill chips, Extract with AI (loading, success, validation and API error), 422 mapping, the form-level error, submit → navigate; the list's loading, empty, filtered-empty and error + retry states, filters and page read from and written to the URL, debounced search; the workspace's default and URL tabs, tab changes in the URL, 404 and error + retry, the stream enabled only for a loaded campaign; Settings PATCH and the delete confirmation (cancel, confirm → `/campaigns`, a failure keeps the dialog open).
- **Follow-ups** ([backlog](../backlog.md#platform-and-tooling)): sort the campaign list by last activity (the API sorts newest first only); the campaign's title instead of `#id` in the breadcrumbs; move `EmptyState` / `ErrorState` to `src/components` once E1-05 has merged.

---

### E1-05 · Candidates table
**Status:** done ([#40](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/40))  ·  **Size:** L  ·  **Branch:** `e1-05-candidates-table`

**Goal:** The hero table from spec §5.1.

**Acceptance criteria**
- [x] TanStack Table columns: select, name/email, score (number plus a bar in the category colour), category badge, top 3 skills (required skills highlighted), experience, stage, updated.
- [x] Default sort by score descending, nulls last. Server-side sort and pagination through `/api/v2`.
- [x] Toolbar: search, category multi-select, score range, skill, stage. **All kept in the URL with nuqs**, and they survive a reload.
- [x] Selection with bulk actions: move stage, reject and export CSV, applied optimistically with rollback on error.
- [x] Clicking a row opens a placeholder panel (the full review panel is E1-07). **J/K** keyboard navigation between rows.
- [x] Smooth with **500 rows** on a page (measured and noted in the PR). Empty, loading and error states.
- [x] Vitest tests for columns, filters ↔ URL and bulk actions.

**Notes:**
- **Where.** `src/features/applications`, public entry `CandidatesTab({ campaignId, uploadHref? })` (plus the presentational `CandidatesTable`). TanStack Table 8.21.3 (pinned). It is mounted as the Candidates tab of `/campaigns/[id]`: the route passes it into the workspace's `candidatesTab` slot, so neither feature imports the other (the placeholder from E1-04 is gone). The temporary dev route that mounted the tab on its own was removed when the tab was wired in; only its fixture mode stays, as `/dev/candidates?rows=500` (404 in production builds, like `/dev/ui`), to re-run the 500-row check.
- **URL state (nuqs).** `q`, `cat` (`strong,moderate,weak,rejected`), `min`, `max` (score 0–100), `skill`, `stage` (API values), `sort` (API values; the default `-score` is left out), `page`, `size` (25 / 50 / 100, default 50; the API caps pages at 100) and `open` (the panel's application). `tab` is left to the workspace. History entries are replaced. A filter, sort or page-size change goes back to page 1; "Clear filters" keeps the sort and page size. Unknown values fall back to defaults and scores are clamped, so a hand-edited URL never causes a 422. Search and skill commit after a 300 ms pause (Enter commits at once): one request per pause, not per key.
- **Sorting.** Score and updated toggle descending → ascending (`-score`/`score`, `-updated_at`/`updated_at`; the API keeps unscored last both ways). Name sorts A→Z only (the API has no descending name sort), so a second click returns to the default. Other columns are not sortable server-side.
- **Bulk actions.** Selection is local and per view (it resets when filters, sort or page change; per page, so ≤ 100 ids). Move to stage and reject patch every cached page of the campaign (`setQueriesData` on `applications.campaignLists`), roll back on error (the global mutation toast shows the problem; the selection stays for a retry) and invalidate the lists, application details and the campaign on settle. The success toast reports `not_found` ids. Export downloads the selection (`ids` + sort) or everything matching the filters (filters + sort) as a blob; the file name is built client-side because CORS does not expose `Content-Disposition`.
- **Panel and keyboard.** A non-modal right sheet (480 px) with name, email, score, category, stage, experience and summary. Row click or Enter opens it; focus stays on the table. J/K move the active row (roving tabindex, the panel follows), but not while typing in a field, inside a menu or with a modifier; Esc closes. A shared link with `open=` for a row on another page loads it with `getApplication`.
- **States.** Skeleton rows while loading, "No candidates yet" with an Upload resumes link (`?tab=uploads`), "No candidates match these filters" with Clear filters, an empty page past the end, and the problem's title and detail with Retry. Rows stay on screen (dimmed, `aria-busy`) while the next page or filter loads.
- **500 rows, measured.** Headless Chrome over CDP, production build (the fixture route enabled locally for the run only), 500 rows, after a warm-up: input → next frame for J/K **~20–23 ms median** (about 6 ms of script), J/K with the panel open **~26–30 ms**, select all / deselect all **33–54 ms**, DOMContentLoaded 0.5–0.6 s. The first version was much slower: select all **300–800 ms**, J/K p95 up to 690 ms. Chrome traces showed React re-rendering 500 Radix checkboxes and long style recalculation. Fixes: rows are `React.memo`, the data cells are memoised apart from the checkbox (selecting or activating re-renders the checkbox only), rows are a plain `<tr>` (no colour transition and no `:has()` variant on 500 rows), and the select column uses a native checkbox in the accent colour. Virtualisation and `content-visibility` were not needed (and `content-visibility` does not apply to table rows). A Vitest benchmark (`candidates-table.perf.test.tsx`, jsdom, React Profiler) runs in CI: mount ~1.1 s, J/K median ~6–10 ms, select all ~170–230 ms. Without row memoisation J/K was 400–500 ms there, which fails its bound.
- **Styling.** Tokens only. The one inline `style` is the score bar's width, set as a CSS variable (`--score`) and used by a `w-(--score)` class; the colour is a `bg-category-*` class.
- **Tests.** 47 new tests in 4 files (URL state and sort mapping, columns, the tab against a fake `/api/v2` on `fetch` covering filters ↔ URL, reload, pagination, bulk actions with the optimistic update and rollback, export, panel and keyboard, states, and the 500-row benchmark); 86 in total.
- **API gaps** (for the backlog): no descending name sort and no sort by experience, category or stage; no stage filter value for "unreviewed" (`null`); CORS does not expose `Content-Disposition`; bulk actions and export take ids only (≤ 500), so "select all matching" across pages would need a filter-based variant; the spec's "only processing / failed" toggle needs a resume-status filter on the applications list (not in this ticket's criteria).

---

## Sprint review & retro

**Goal met:** yes. Seven PRs merged in three waves: #34, #35, #36, then #37 and #38, then #39 and #40. CI is green on `master` (`5089372`). The local Compose stack was rebuilt on `master`. A recruiter can open HireFlow AI, create a campaign with AI extraction from a job description, and review its candidates in the ranked table, all through `/api/v2` and the generated client.

### Demo

- **HireFlow AI shell:**
  - design tokens with light and dark themes, both checked for contrast in CI;
  - shadcn/ui primitives, the sidebar and the ⌘K palette;
  - the `/dev/ui` gallery ([#34](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/34)).
- **`/api/v2`** ([#36](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/36)):
  - its own OpenAPI document, problem+json errors and stable operation ids;
  - 110 contract snapshots, with the v1 snapshots unchanged;
  - query counts asserted in tests.
- **Contract-first client** ([#37](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/37)): the TypeScript client is generated from OpenAPI, and CI fails when it is stale. The check caught S1's contract change on its first real use.
- **Security:** python-multipart ([#35](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/35)), and FastAPI 0.163 with Starlette 1.10, which closes 7 advisories and adds explicit upload limits ([#38](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/38)).
- **Campaigns** ([#39](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/39)): the list, the new-campaign form with "Extract with AI", and the workspace with its KPI strip, tabs and Settings.
- **Candidates table** ([#40](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/40)):
  - filters, sort and page kept in the URL;
  - optimistic bulk actions with rollback;
  - J/K navigation.

  With 500 rows, J/K takes about 20 ms per press and select-all 33–54 ms (measured in Chrome).

### Keep

- **Parallel tickets in separate feature folders, joined through slots at the route** (the workspace's `candidatesTab`). The second PR to merge takes the one-line wiring.
- **The drift check and `mergecheck`.** The drift check turned the S1/E1-03 ordering into a visible, mechanical step. `mergecheck` confirmed that every merge dropped nothing.
- **Measuring before optimising.** Chrome traces showed what made the 500-row table slow (Radix checkboxes and style recalculation), and a jsdom benchmark now keeps it from regressing.

### Drop

- **Visual checks skipped because the API was "unreachable".** The real cause was the backend crashing after S1. The container runs the bind-mounted new code with the old packages baked into its image. Nobody saw that until wave 3.

### Try

- **Rebuild the Compose stack after every merge that changes dependencies**, and check it is healthy before the next wave starts.
- **Allow the agents' dev-server ports in the local CORS setting**, so every UI ticket gets a browser check against the real API.
- **Run timing tests with headroom.** The 500-row benchmark hit Vitest's 5 s default once more test files ran in parallel, so it now has its own timeout. Any new performance test should set one too.

Next: [Sprint 6](sprint-06.md).
