# E1 · Product design & frontend foundation: Design Spec

**Status:** in review  ·  **Date:** 2026-10-19  ·  **Roadmap:** [roadmap.md](../roadmap.md)  ·  **Depends on:** E0 closed (People + Applications are the read model)

## 1. Goal

Replace the prototype UI with **HireFlow AI**: a calm, data-dense, pitch-ready product for recruitment-agency recruiters. It is built on a foundation the later epics can extend without rework.

Two outcomes:

1. **Product:** the campaign screening flow is excellent end to end: create campaign → upload → watch processing → ranked shortlist → review → move stage. Every candidate opens as a **Person** with their history across campaigns.
2. **Foundation:** a design system with tokens and themes, a typed API client generated from OpenAPI and checked in CI, a consistent data layer, and E2E tests in CI.

**Learning focus:** product and UX design for data-dense tools, design systems, frontend architecture (server state vs URL state vs local state), contract-first API clients, and E2E testing.

## 2. Non-goals

- **Authentication, agencies, clients and roles:** these are **E2**. The shell keeps a workspace-switcher slot and a user-menu slot as placeholders.
- **Semantic search / talent rediscovery:** this is **E6**. `/talent` is a plain list in E1.
- **Kanban pipeline board, shareable client shortlists (E11), interview scheduling.**
- **Mobile layouts:** desktop first (≥ 1280 px). Tablet widths must not break, but they are not optimised.
- **Storybook:** a `/dev/ui` component gallery route (dev builds only) covers the need.

## 3. Brand and visual direction

| Aspect | Decision |
|---|---|
| Name | **HireFlow AI** (replaces "ResumeAI" in the UI, README, page titles and favicon) |
| Feel | Calm and professional, in the style of Linear or Attio. No glows, gradients, decorative animation or "AI sparkle". Density over decoration. |
| Theme | **Light by default**, plus dark. Every colour is a CSS-variable token, with no hex values in components. |
| Neutrals | Zinc scale |
| Accent | **Teal** (primary actions, focus rings, selection, links). One accent only. |
| Category colours | Muted semantic tokens: `strong` (green), `moderate` (amber), `weak` (orange), `rejected` (rose). Used for badges and score bars, never as large fills. |
| Typography | Geist Sans / Geist Mono (already installed). Tables 13 px, body 14–15 px, page titles 20–24 px. Tabular numbers for scores and counts. |
| Spacing and shape | 4/8 px grid, 6 px radius (4 px for small controls), 1 px borders instead of shadows (one subtle shadow level for popovers). |
| Icons | Lucide, 16 px in dense UI |
| Motion | Only for state changes (150 ms), and it respects `prefers-reduced-motion` |

Design tokens live in `src/styles/tokens.css` (the shadcn variable convention: `--background`, `--foreground`, `--primary`, `--muted`, …, plus `--category-*`) and are mapped into Tailwind v4 `@theme`. Dark mode overrides the same variables under `.dark` (`next-themes`, defaulting to system).

## 4. Information architecture

**App shell:**
- A left sidebar (collapsible to icons) with:
  - a workspace slot (E2 placeholder)
  - a search trigger that opens the **⌘K command palette** (jump to campaign or person, plus "New campaign")
  - nav items: Home · Campaigns · Talent
  - Settings and theme toggle at the bottom
- A top bar with breadcrumbs and page actions.

| Route | Screen | Primary content |
|---|---|---|
| `/` | Home | Active campaigns (status, new uploads, strong matches); uploads still processing; recent strong matches across campaigns |
| `/campaigns` | Campaign list | Table: title, role, status, applications, strong matches, processing, last activity. Filter by status; search. |
| `/campaigns/new` | New campaign | Title, role, description. **Paste JD → "Extract with AI"** fills role and required skills (editable chips). |
| `/campaigns/[id]` | **Campaign workspace** | Header (title, role, status, actions: Upload, Export, Edit) + a KPI strip + tabs |
| `/campaigns/[id]?tab=candidates` | Candidates (default tab) | Ranked application table and review side panel (§5) |
| `/campaigns/[id]?tab=uploads` | Uploads | Dropzone, live per-file processing list, retry failed, quota state |
| `/campaigns/[id]?tab=insights` | Insights | Category distribution, score histogram, top skills vs required skills, processing funnel |
| `/campaigns/[id]?tab=settings` | Settings | Edit campaign fields and status (active / closed / archived), delete |
| `/people/[id]` | **Person profile** | Header (name, contact, links, years of experience), AI evaluation for the selected application, extracted profile, **all applications across campaigns**, notes and stage per application |
| `/talent` | Talent | Paginated people list (name, latest role, skills, number of applications, last seen). Search by name/email only (E6 replaces it). |
| `/dev/ui` | Component gallery | Dev builds only. Every component in every state, light and dark. |

## 5. The hero flow in detail

### 5.1 Candidates table (`/campaigns/[id]`, Candidates tab)

- **Columns:** select, name (and email underneath), **score** (number plus a thin bar in the category colour), category badge, top 3 skills (matched required skills highlighted), years of experience, stage, updated.
- **Default sort:** score descending, nulls last. Columns are sortable.
- **Toolbar:** search, category multi-select, score range, skill, stage, "only processing / failed" toggle.
  - Every filter, the sort and the page are kept in the **URL** (`nuqs`), so views are shareable and survive a reload.
- **Selection and bulk actions:** move to stage, reject, export CSV (selected or all filtered).
- **Row click** opens the review panel. **J/K** move between rows, **Esc** closes, **S** focuses the stage selector.
- **Empty, loading and error states** are designed, not left blank. Skeleton rows while loading, and "Upload resumes" as the empty state.

### 5.2 Review side panel

- A right-hand sheet (about 480 px) that keeps the table visible.
- Contents: name and contact; **score breakdown** (sub-score bars from `score_breakdown`); recommendation; strengths / weaknesses / missing skills; extracted skills and experience summary; stage selector and notes (autosaved, with an explicit "Saved" state); "**Also applied to**" (other applications of this Person); "Open full profile".

### 5.3 Uploads

- A dropzone accepting PDFs (client-side checks mirror the server: type, size, count).
- The per-file list moves through **Queued → Uploading → Extracting → Analysing → Done / Failed / Quota limit**, live through SSE.
  - Done rows link to the application.
  - A Failed row shows the step that failed and offers **Retry**.
  - A quota row shows the retry time.
- Duplicates are reported inline with the server's reason.

### 5.4 Person profile (`/people/[id]`)

- **Header** with an application switcher: the AI evaluation shown is for the selected application, defaulting to the most recent one.
- **Sections:** Evaluation · Profile (skills, experience, education, projects, certifications) · Applications (campaign, score, category, stage, applied date) · Notes.
- **Identity conflicts** for this person (from E0, read-only) appear as an info banner: "This resume shared a phone number with another person". The full review UI is E6.

## 6. API: `/api/v2`

The new UI uses **only `/api/v2`**. `/api/v1` is frozen: it serves the characterization contract and the old UI until E1 removes the old UI. After E1, v1 stays (unused by the UI, still covered by its snapshots) until E2 adds authentication. At that point v1 is removed in one ticket rather than given auth twice, which is tracked in the backlog. v2 routers are **thin and reuse the module services**, with no business logic in routers.

**Conventions:**
- **Resource shapes:** `Application` (id, campaign_id, person summary, scoring, category, stage, notes, timestamps), `Person` (id, name, links, profile, identifiers' raw values, applications summary), `Campaign` (incl. counts).
- **Lists** return an envelope `{ items, total, page, page_size }`. v1 returns bare arrays with no total, which the UI needs.
- **Errors** use one shape, RFC 9457 `application/problem+json`: `{ type, title, status, detail, errors? }`. Validation errors map field paths.
- **Operation ids** are explicit and stable (`listCampaignApplications`, `getPerson`, …) because the generated client uses them as function names.
- **Enums** (category, stage, resume status, campaign status) are declared as OpenAPI enums, so the TS client gets literal unions.

**Endpoints:**

| Resource | Endpoints |
|---|---|
| Campaigns | `GET/POST /campaigns`, `GET/PATCH/DELETE /campaigns/{id}`, `GET /campaigns/{id}/stats`, `POST /campaigns/parse-jd` |
| Applications | `GET /campaigns/{id}/applications` (filters, sort, page), `GET /campaigns/{id}/applications/export` (CSV), `GET/PATCH/DELETE /applications/{id}`, `POST /campaigns/{id}/applications/bulk` (stage / reject / delete) |
| Uploads | `POST /campaigns/{id}/uploads`, `GET /campaigns/{id}/resumes`, `POST /campaigns/{id}/resumes/retry-failed`, `GET /campaigns/{id}/events` (SSE; campaign-filtered, which depends on the B3 fix in E0-21) |
| People | `GET /people` (page, `q` on name/email), `GET /people/{id}` (incl. applications and conflict flags) |
| Dashboard | `GET /stats`, `GET /home` (the Home screen aggregates in one call) |

v2 gets its own **characterization-style contract tests** (syrupy snapshots), in the same pattern as v1.

### 6.1 As built (E1-02)

**The v2 application and its OpenAPI document** ([ADR 0008](../adr/0008-api-v2-sub-application.md)). `/api/v2` is a FastAPI sub-application (`backend/app/v2.py`, `v2_app`) mounted by `app.main`. It has its own document, **`/api/v2/openapi.json`** (Swagger UI at `/api/v2/docs`), with `servers: [{url: "/api/v2"}]`, so paths below are relative to `/api/v2`; `app.v2.v2_app.openapi()` returns the same document without a server (for E1-03's generator). The root `/openapi.json` and every v1 response are unchanged (the 179 v1 snapshots are byte-identical). Problem details are installed on the v2 app only.

**Where the code lives.** Endpoints that need one module: `app/modules/hiring/router_v2.py` (create campaign, parse-jd) and `app/modules/ingestion/router_v2.py` (uploads, resumes, retry, events). Endpoints that span modules: `app/api/v2/` (ADR 0005): `campaigns.py`, `applications.py`, `people.py`, `dashboard.py`, plus `schemas.py` for composed resources. Each module's v2 resources are in its `schemas_v2.py` (public, like `schemas.py`). Business rules stay in the module services (new functions: `hiring.service` campaign/application resources, insights, bulk; `ingestion.service` v2 uploads, resume pages, tallies; `talent.service.list_people` / `get_person` / `count_people`). Import-linter: `schemas_v2` joins the public interfaces, and the new contract `v2-independent-of-v1` keeps v2 code off v1 routers (9 contracts).

| Method | Path | operationId | Tag | Response (2xx) | Errors |
|---|---|---|---|---|---|
| GET | `/campaigns` | `listCampaigns` | campaigns | `CampaignPage` | 422 |
| POST | `/campaigns` | `createCampaign` | campaigns | 201 `Campaign` | 422 |
| GET | `/campaigns/{campaign_id}` | `getCampaign` | campaigns | `Campaign` | 404, 422 |
| PATCH | `/campaigns/{campaign_id}` | `updateCampaign` | campaigns | `Campaign` | 404, 422 |
| DELETE | `/campaigns/{campaign_id}` | `deleteCampaign` | campaigns | 204 | 404, 422 |
| GET | `/campaigns/{campaign_id}/stats` | `getCampaignStats` | campaigns | `CampaignStats` | 404, 422 |
| POST | `/campaigns/parse-jd` | `parseJobDescription` | campaigns | `ParsedJobDescription` | 422, 502 |
| GET | `/campaigns/{campaign_id}/applications` | `listCampaignApplications` | applications | `ApplicationPage` | 404, 422 |
| GET | `/campaigns/{campaign_id}/applications/export` | `exportCampaignApplications` | applications | `text/csv` | 404, 422 |
| POST | `/campaigns/{campaign_id}/applications/bulk` | `bulkApplicationAction` | applications | `BulkApplicationResult` | 404, 422 |
| GET | `/applications/{application_id}` | `getApplication` | applications | `Application` | 404, 422 |
| PATCH | `/applications/{application_id}` | `updateApplication` | applications | `Application` | 404, 422 |
| DELETE | `/applications/{application_id}` | `deleteApplication` | applications | 204 | 404, 422 |
| POST | `/campaigns/{campaign_id}/uploads` | `uploadResumes` | uploads | 202 `UploadResult` | 404, 422 |
| GET | `/campaigns/{campaign_id}/resumes` | `listCampaignResumes` | uploads | `ResumePage` | 404, 422 |
| POST | `/campaigns/{campaign_id}/resumes/retry-failed` | `retryFailedResumes` | uploads | `RetryResult` | 404, 422 |
| GET | `/campaigns/{campaign_id}/events` | `streamCampaignEvents` | uploads | `text/event-stream` | 404, 422 |
| GET | `/people` | `listPeople` | people | `PersonPage` | 422 |
| GET | `/people/{person_id}` | `getPerson` | people | `Person` | 404, 422 |
| GET | `/stats` | `getStats` | dashboard | `Stats` | — |
| GET | `/home` | `getHome` | dashboard | `Home` | — |

Every endpoint can also answer 500 (problem details). 422 covers invalid path ids, query parameters and bodies.

**Errors.** `application/problem+json`: `{type, title, status, detail?, errors?}`; `type` is `about:blank` and `title` the HTTP status phrase; `errors` (422 only) has one `{loc, msg}` per invalid field, `loc` being the path (`["query", "page_size"]`, `["body", "required_skills", 0]`). Unknown routes (404) and wrong methods (405) are problems too, and a 500 never echoes the URL or the exception (B10). Example:

```json
{"type": "about:blank", "title": "Unprocessable Entity", "status": 422, "detail": "1 invalid field",
 "errors": [{"loc": ["query", "page_size"], "msg": "Input should be less than or equal to 100"}]}
```

**Lists.** `{items, total, page, page_size}`, named per resource (`CampaignPage`, `ApplicationPage`, `ResumePage`, `PersonPage`). `page` ≥ 1, 1 ≤ `page_size` ≤ 100 (default 20); out of range is a 422.

**Resources** (component names in the v2 document):

- **`Campaign`**: `id, title, role, description, required_skills, status, created_at, updated_at, last_activity_at` (latest change to the campaign, an application or a resume) and `counts {resumes, in_progress, failed (incl. quota), new_resumes_24h, applications, strong_matches}`. `CampaignCreate` / `CampaignUpdate` validate lengths, the status enum and unknown fields (B5); in a PATCH, fields may be omitted but only `description` may be `null`. List filters: `status` (repeatable), `q` (title or role); newest first.
- **`CampaignStats`** (Insights): `resumes` (count per status), `applications {total, by_category, by_stage (incl. unreviewed)}`, `avg_score` (mean of scored applications, B9 fixed), `score_histogram` (10 buckets of 10), `top_skills [{skill, count, required}]`.
- **`Application`**: `id, campaign_id, resume_id, person {id, name, email}, score, score_breakdown, category, summary, strengths, weaknesses, missing_skills, recommendation, skills, years_of_experience, pipeline_stage, notes, created_at, updated_at`. The person's name/email, skills and experience are from the application's current resume (§7.1 of EPIC-00, as v1). List filters: `q` (name/email), `category` and `stage` (repeatable, any of), `min_score`, `max_score`, `skill`, `min_experience`; `sort` = `-score` (default) / `score` (unscored last both ways) / `-updated_at` / `updated_at` / `name` (case-insensitive, unnamed last); ties by id. The export takes the same filters and sort (B8 fixed) and optional repeatable `ids` (a selection); it has v1's CSV columns. `ApplicationUpdate`: `pipeline_stage`, `notes` (both nullable), `category` (override, not nullable). v2 ids are application ids only: merged legacy candidate ids do not resolve (only v1 needs `legacy_candidate_map`).
- **Bulk**: request `BulkApplicationAction`, a tagged union on `action`: `{action: "stage", ids, stage}`, `{action: "reject", ids}` (stage `rejected_manual`), `{action: "delete", ids}` (with resumes and PDFs); 1–500 ids. Response `{action, affected, not_found}`: ids that are not this campaign's applications are reported, not acted on; duplicates count once.
- **`Resume`**: `id, campaign_id, filename, size_bytes, status, processing_step, error_message, retry_count, application_id` (once scored), `uploaded_at, updated_at`. Upload answers `UploadResult {accepted: [Resume], rejected: [{filename, reason}]}`; too many files is a 422 (v1: 400). Retry answers `{retried: [resume id]}`.
- **Events** (`GET /campaigns/{id}/events`): the per-campaign Redis channel of E0-21 (only that campaign's resumes; 404 for an unknown campaign, B7). Events `connected` (data `ConnectedEvent {campaign_id}`) and `resume_update` (data `ResumeEvent {resume_id, status, filename, error, candidate_name, score, category, person_id, matched_existing}`): v1's payload, validated and always with every field (`null` where a status has none); an invalid payload is logged and skipped. Both schemas are in the document's components for the client. The check's read transaction ends before streaming, so an open stream holds no database connection.
- **`Person`** (`GET /people/{id}`): `id, name, github_url, linkedin_url, profile {skills, education, work_experience, certifications, projects, years_of_experience}, identifiers [{kind, value (raw), inferred}], conflicts [{id, role (chosen | conflicting), resume_id, other_person_id, reason, created_at, resolved_at}], has_unresolved_conflicts, created_at, updated_at`, and `applications [{id, campaign {id, title, status}, resume_id, score, category, pipeline_stage, created_at, updated_at}]`, newest first. The list item (`PersonListItem`): `id, name, email (first seen), latest_role (first work-experience title), skills, years_of_experience, application_count, last_applied_at, created_at, updated_at`; most recently updated first; `q` matches the name or any email (raw or normalised), case-insensitively, wildcards literal.
- **`Stats`**: `campaigns {total, active, closed, archived}`, `resumes` (per status), `applications {total, by_category, by_stage}`, `people`.
- **`Home`**: `active_campaigns` (10 most recently updated active campaigns, as `Campaign`), `processing {total, items}` (the 10 newest in-progress resumes across campaigns, each a `Resume` with `campaign {id, title, status}`), `recent_strong_matches` (10 newest strong-match applications: `{id, campaign, person, score, category, pipeline_stage, created_at}`).

**Bugs fixed in v2 only** (v1 is frozen and keeps them, [backlog](../backlog.md)): B5 (campaign validation → 422), B7 (unknown campaign → 404 on every campaign-scoped endpoint), B8 (export applies every filter), B9 (mean score), B10 (invalid category/stage → 422; the 500 body does not echo the URL).

**Query counts** (asserted in `tests/contract_v2/test_query_counts.py`, the same for 3 and 30 rows): campaigns list 4; applications list 4 (5 with profile filters or `sort=name`); export 4; resumes list 4; people list 4; Person 4; campaign stats 7; stats 5; home 8.

## 7. Frontend architecture

| Concern | Choice | Why |
|---|---|---|
| Framework | Next.js 16 App Router, React 19, TypeScript `strict` | Already in place |
| Components | **shadcn/ui** (Radix + Tailwind v4), copied into `src/components/ui` | The code is owned in the repo, accessible primitives, themeable by tokens |
| Server state | **TanStack Query v5** | Caching, retries, invalidation, optimistic stage changes. Replaces `useEffect` fetching. |
| API client | **`openapi-typescript` + `openapi-fetch`** generated from the backend's OpenAPI | Types can't drift. A tiny runtime. |
| Client drift check | CI job `frontend`: export OpenAPI from the backend → regenerate → `git diff --exit-code src/lib/api/schema.d.ts` | Contract-first: a backend change without a client update fails CI |
| URL state | **nuqs** | Filters, sort, page and tabs are shareable and survive reloads |
| Tables | **TanStack Table v8** | Headless: sorting, selection, column visibility |
| Forms | **react-hook-form + zod** | Typed validation; server errors map from problem+json |
| Live updates | `useCampaignEvents(campaignId)`: one `EventSource` per open workspace, updating the Query cache (`setQueryData` / `invalidateQueries`) | Progress without polling; one connection per campaign |
| Toasts | **sonner** (shadcn) | Replaces `react-toastify` |
| Charts | **Recharts** via shadcn chart wrappers, themed by tokens | Already a dependency |
| Theming | **next-themes** | System / light / dark |
| Removed | `axios`, `react-toastify`, `src/components/*` (old), `globals.css` overrides, all inline `style={}` | Replaced as above |

**Rendering:** the app shell (sidebar, top bar) is a Server Component. Data screens are Client Components using Query, because the data is user-specific and live, and E2 adds auth. Server-side prefetching is a later optimisation.

**Folder layout (`frontend/src`):**

```
app/                 routes (thin: layout, params, compose features)
features/
  campaigns/         list, new, workspace tabs, hooks (useCampaign, useCampaignStats)
  applications/      table, columns, filters, review-panel, bulk actions, hooks
  uploads/           dropzone, processing list, useCampaignEvents
  people/            profile, talent list, hooks
  home/
components/
  ui/                shadcn primitives (generated, lightly edited)
  app-shell/         sidebar, top bar, command palette, theme toggle
lib/
  api/               schema.d.ts (generated), client.ts (openapi-fetch + problem+json handling), query-keys.ts
  utils/
styles/tokens.css
```

**Rule:** routes import from `features/*`. Features never import from each other's internals; shared pieces go to `components/` or `lib/`. This is enforced by ESLint `no-restricted-imports`, the frontend equivalent of the backend's import-linter.

## 8. Accessibility and quality bar

- **WCAG 2.2 AA:** contrast checked for both themes, a visible focus ring (teal), everything reachable by keyboard (the table, panel and palette included), labelled controls, live regions for upload progress.
- **Every data view has defined states:** loading (skeleton), empty, error (problem+json detail plus retry) and partial (some uploads failed).
- **Performance:** the candidate table stays smooth with **500 rows** per page (TanStack Table + `content-visibility` / virtualisation if needed). Measured, not assumed.

## 9. Testing

| Layer | Tooling | Scope |
|---|---|---|
| Unit / component | **Vitest + Testing Library** | Hooks (Query + SSE), filters ↔ URL, table columns, review panel, forms |
| API contract | pytest + syrupy (backend) | Every v2 endpoint snapshotted, as for v1 |
| Client drift | CI diff of `schema.d.ts` | The generated client matches the backend |
| **E2E** | **Playwright** in CI against the Compose stack with `AI_PROVIDER=fake` | The hero flow: create campaign (with JD extraction) → upload 3 PDFs → see processing → ranked table → filter → open panel → change stage → open Person profile → export CSV |
| Accessibility | **@axe-core/playwright** on every E2E page | No serious or critical violations |
| Visual | Playwright screenshots of key screens in light and dark, uploaded as a CI artifact | For review, not a pixel-diff gate (to avoid flakiness) |

A new CI job, **`e2e`** (Compose up with fake AI, seed a small dataset, run Playwright), becomes a required check after E1-09.

## 10. Plan (two sprints after E0 closes)

| Ticket | Title | Size |
|---|---|---|
| E1-01 | Design tokens, shadcn/ui, app shell (sidebar, ⌘K, theme), `/dev/ui` gallery; delete old styles; rebrand to HireFlow AI | M |
| E1-02 | Backend `/api/v2` (campaigns, applications, uploads, people, home), problem+json errors, operation ids, contract tests | L |
| E1-03 | Generated API client + CI drift check; Query provider, query keys, problem+json handling; `useCampaignEvents` SSE hook | M |
| E1-04 | Campaign list and new campaign (JD extraction) | M |
| E1-05 | Candidates table: columns, URL filters, sort, pagination, bulk actions, export | L |
| E1-06 | Uploads tab: dropzone, live processing list, retry, quota state | M |
| E1-07 | Review side panel and Person profile (incl. applications across campaigns) | L |
| E1-08 | Home dashboard and Insights tab; Talent list | M |
| E1-09 | Playwright E2E + axe in CI (`e2e` job), visual polish pass, README screenshots; delete the old UI and stop using v1 from the frontend | M |

**Order:** E1-01 and E1-02 in parallel → E1-03 → E1-04 to E1-08 (largely parallel by feature folder) → E1-09.

## 11. Definition of done for the epic

- [ ] The hero flow passes in Playwright E2E in CI, with no serious or critical axe violations.
- [ ] No `style={}`, no hex colours in components, no `axios` / `react-toastify`. Every colour comes from tokens, and light and dark both pass contrast.
- [ ] The frontend talks only to `/api/v2` through the generated client. The CI drift check is green.
- [ ] Every v2 endpoint has contract snapshots. v1 snapshots are unchanged.
- [ ] The candidate table handles 500 rows smoothly (measured).
- [ ] README has screenshots (light and dark) and a short "product tour" for pitching.
- [ ] ADRs: 0006 *Contract-first API client (OpenAPI → TS)*, 0007 *Frontend state model (Query / URL / local)*.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Building v2 duplicates v1 effort | v2 routers are thin over the existing services; v1 stays frozen and is not extended |
| The redesign drifts into a feature race | Non-goals (§2) are explicit; new ideas go to the backlog, not into E1 |
| E2E flakiness in CI | Fake AI provider, a deterministic seed, `--wait` health checks, Playwright auto-waiting, no fixed sleeps, a retry of 1 in CI only |
| Generated client churn | Stable operation ids; regeneration is part of the backend PR whenever the API changes |
