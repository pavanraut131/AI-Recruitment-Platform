# Sprint 5 · HireFlow AI foundation

**Epic:** [E1 · Product design & frontend foundation](../specs/EPIC-01-frontend.md)  ·  **Dates:** 2026-10-20 → 2026-10-26  ·  **Status:** planned

## Sprint goal

> A recruiter opens **HireFlow AI** and sees the new design system and app shell. They can create a campaign (with AI extraction from a job description) and review its candidates in the new ranked table. Everything runs on `/api/v2` through a client generated from OpenAPI, which CI keeps in sync.

This sprint builds the foundation from spec §10 (E1-01 to E1-05). The rest of the hero flow follows in Sprint 6: uploads, the review panel and Person profile, Home and Insights, and E2E tests (E1-06 to E1-09).

## Board

| Ticket | Title | Size | Status |
|---|---|---|---|
| B4 | Upgrade python-multipart (upload parsing DoS) | S | todo |
| E1-01 | Design tokens, shadcn/ui, app shell, `/dev/ui`; rebrand to HireFlow AI | M | todo |
| E1-02 | Backend `/api/v2`: campaigns, applications, uploads, people, home | L | todo |
| E1-03 | Generated API client and CI drift check; Query, problem+json, SSE hook | M | todo |
| E1-04 | Campaign list and new campaign (JD extraction) | M | todo |
| E1-05 | Candidates table: columns, URL filters, sort, pagination, bulk actions, export | L | todo |

**Order** (stacks no more than two deep, as agreed in the Sprint 4 retro):
- **Wave 1:** B4, E1-01 and E1-02, all independent and branched from `master`.
- **Wave 2:** E1-03, after E1-01 and E1-02 merge.
- **Wave 3:** E1-04 and E1-05 in parallel, after E1-03 merges. They're in separate feature folders.

**Merge method:** squash is fine for leaf PRs. Use **"Create a merge commit"** for any PR that has another PR stacked on it.

---

### B4 · Upgrade python-multipart
**Status:** todo  ·  **Size:** S  ·  **Branch:** `b4-python-multipart`

**Goal:** Close the upload-parsing DoS ([backlog](../backlog.md)). python-multipart 0.0.9 parses some 10 MB uploads byte by byte, taking about 30 s of CPU per file.

**Acceptance criteria**
- [ ] Upgrade python-multipart to the current release, and FastAPI and Starlette if needed for compatibility. The lockfile is updated.
- [ ] A regression test uploads a 10 MB PDF made of characters that look like a multipart boundary. It parses in under 2 s and is skipped in CI if timing is unreliable (document why).
- [ ] All upload characterization snapshots pass unchanged.

---

### E1-01 · Design system and app shell
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e1-01-design-system`

**Goal:** The visual foundation from spec §3–§4: tokens, components, the shell and the rebrand.

**Acceptance criteria**
- [ ] `src/styles/tokens.css` follows the shadcn variable convention and adds `--category-*`, with light and dark themes. Zinc neutrals and a teal accent. Mapped into Tailwind v4 `@theme`. **No hex values in components.**
- [ ] shadcn/ui is initialised. The primitives needed by E1 live in `src/components/ui`: button, input, textarea, select, badge, dialog, sheet, dropdown-menu, command, tabs, table, tooltip, skeleton, sonner, form, checkbox, popover, separator, scroll-area.
- [ ] **App shell** (`src/components/app-shell`):
  - a collapsible sidebar with a workspace slot (E2 placeholder), Home, Campaigns and Talent, plus Settings and a theme toggle;
  - a top bar with breadcrumbs;
  - a **⌘K command palette** with navigation and "New campaign" (data search arrives with E1-03).
- [ ] `next-themes`: system, light and dark, with no flash on load.
- [ ] A `/dev/ui` gallery shows every primitive in every state, in light and dark. It's excluded from production builds.
- [ ] **Rebrand to HireFlow AI:** app title, metadata, favicon, README heading, and the `make setup` message. The old screens stay reachable but are wrapped in the new shell, until E1-04 to E1-08 replace them.
- [ ] `axios` and `react-toastify` are not used by any new code, and an ESLint rule bans them in `features/`. The old components are removed as their screens are replaced.
- [ ] An ESLint `no-restricted-imports` rule for feature-folder boundaries (spec §7).
- [ ] WCAG AA contrast is checked for both themes, with the results in the PR.

---

### E1-02 · Backend `/api/v2`
**Status:** todo  ·  **Size:** L  ·  **Branch:** `e1-02-api-v2`

**Goal:** The resource API from spec §6, as thin routers over the existing module services.

**Acceptance criteria**
- [ ] Every endpoint in the spec §6 table is implemented. v2 routers live with their modules or in `app/api` for composed endpoints. **No business logic in routers.**
- [ ] List endpoints return `{ items, total, page, page_size }`. Errors use RFC 9457 `application/problem+json` (one exception handler for `/api/v2`, with validation errors mapped to field paths).
- [ ] Explicit, stable `operation_id`s. Enums (category, stage, resume status, campaign status) appear in OpenAPI as enums.
- [ ] `GET /api/v2/people/{id}` returns the person with all applications and conflict flags. `GET /api/v2/home` aggregates the Home screen in one call.
- [ ] **Contract snapshots** for every v2 endpoint, including errors, in the same pattern as v1. **The v1 snapshots stay byte-identical.**
- [ ] import-linter stays green. Query counts are bounded (no N+1) on list endpoints, asserted in tests.

---

### E1-03 · API client and data layer
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e1-03-data-layer`

**Goal:** A typed, contract-first data layer for every feature (spec §7).

**Acceptance criteria**
- [ ] `openapi-typescript` generates `src/lib/api/schema.d.ts` from the backend's OpenAPI, using a `make api-client` / `npm run api:generate` script that exports the schema without a running server.
- [ ] `src/lib/api/client.ts` uses `openapi-fetch` with problem+json parsing into a typed error. `query-keys.ts` holds one key factory per resource.
- [ ] **CI drift check:** the `frontend` job regenerates the client and fails on `git diff --exit-code`.
- [ ] TanStack Query provider with defaults (retry policy, stale times) and devtools in dev. Error toasts via sonner.
- [ ] A `useCampaignEvents(campaignId)` hook: one `EventSource` that updates the Query cache, reconnects on drop, and closes on unmount.
- [ ] Vitest + Testing Library set up. Tests cover the client's error mapping, the query keys and the SSE hook (with a mocked EventSource).
- [ ] ADR 0006 *Contract-first API client* and ADR 0007 *Frontend state model*.

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
