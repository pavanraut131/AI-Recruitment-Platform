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
