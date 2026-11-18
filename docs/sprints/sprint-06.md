# Sprint 6 · HireFlow AI hero flow, end to end

**Epic:** [E1 · Product design & frontend foundation](../specs/EPIC-01-frontend.md)  ·  **Dates:** 2026-11-18 → 2026-11-25  ·  **Status:** in progress

## Sprint goal

> A recruiter can run the **whole hero flow** in HireFlow AI:
> 1. create a campaign;
> 2. upload resumes and watch them process live;
> 3. review ranked candidates in a side panel;
> 4. move them through stages and open a Person's profile across campaigns;
> 5. see Home and Insights.
>
> Playwright runs this flow in CI. The old UI and every `/api/v1` call from the frontend are gone, and **E1 closes** (spec §11).

## Board

| Ticket | Title | Size | Status |
|---|---|---|---|
| S2 | Upgrade Next.js (security) | S | done ([#42](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/42)) |
| E1-06 | Uploads tab: dropzone, live processing list, retry, quota state | M | done ([#43](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/43)) |
| E1-07 | Review side panel and Person profile | L | done ([#44](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/44)) |
| E1-08 | Home dashboard, Insights tab and Talent list | M | doing |
| E1-09 | Playwright E2E + axe in CI (`e2e` job), screenshots artifact | M | doing |
| E1-10 | Remove the old UI, README product tour, close E1 | S | todo |

**Order** (stacks no more than two deep):
- **Wave 1:** S2, E1-06 and E1-07 in parallel, branched from `master`.
  - Only S2 changes `package.json` / `package-lock.json`. E1-06 and E1-07 add no npm dependencies, so the lockfile can't conflict.
  - E1-06 works in `features/uploads` and the workspace's Uploads slot. E1-07 works in `features/applications` (the panel) and `features/people` (the profile).
- **Wave 2:** E1-08, after E1-06 and E1-07 merge. It touches the workspace (Insights), `features/people` (Talent) and `/` (Home).
- **Wave 2 (revised):** E1-08 and E1-09 in parallel, after wave 1 merged (2026-11-18).
  - E1-09 was split. The E2E suite and the `e2e` CI job don't depend on E1-08, but deleting the old UI does, because `/` is the legacy home page until E1-08 replaces it.
  - E1-09 keeps the E2E work. The deletion and the close-out move to **E1-10**.
  - Only E1-09 changes `package.json` / `package-lock.json` (Playwright and axe). E1-08 adds no npm dependencies.
- **Wave 3:** E1-10, after E1-08 and E1-09 merge.

**Working agreements** (from the [Sprint 5 retro](sprint-05.md#sprint-review--retro)):
- **Rebuild the local Compose stack** after any merge that changes dependencies.
- **Every UI ticket is checked in a browser against the running API.** The local `CORS_ORIGINS` allows the agents' dev ports, 3101–3103.
- **Timing tests get their own timeout.**
- **Each PR edits only its own ticket section here.** The board table is updated after merging.

---

### S2 · Upgrade Next.js (security)
**Status:** todo  ·  **Size:** S  ·  **Branch:** `s2-next-upgrade`

**Goal:** Close the critical `next` 16.2.9 advisories ([backlog](../backlog.md#platform-and-tooling)): a middleware/proxy bypass with Turbopack, a Server Actions DoS and SSRF, plus the `postcss` and `sharp` advisories that come through it.

**Acceptance criteria**
- [ ] `next` and `eslint-config-next` are upgraded together to the current 16.x patch release with the fixes, pinned exact, with the lockfile updated. Breaking changes are checked against `node_modules/next/dist/docs/`.
- [ ] `npm audit` shows no critical or high advisory in `next`, `postcss` or `sharp`. What remains is listed in the PR: `axios` goes with the old UI in E1-09, and dev-only tooling is noted.
- [ ] Lint, `tsc`, Vitest, `next build`, the contrast check and the CI drift check all pass. The frontend Docker image builds and the app runs in Compose.

**Concept to learn:** reading a framework's security advisories and changelog to judge upgrade risk.

---

### E1-06 · Uploads tab
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e1-06-uploads`

**Goal:** Spec §5.3: upload PDFs to a campaign and watch them process live.

**Acceptance criteria**
- [ ] **`features/uploads` exports `UploadsTab({ campaignId })`.** The route mounts it in a new `uploadsTab` slot of the workspace, replacing E1-04's placeholder, the same way as `candidatesTab`.
- [ ] **Dropzone** (react-dropzone, already a dependency): accepts PDFs, with click and keyboard as well as drag. Client checks mirror the server: type, size (`MAX_FILE_SIZE_MB`) and count per batch, with the server's limits read from one place.
- [ ] **Per-file list:** Queued → Uploading → Extracting → Analysing → Done / Failed / Quota limit.
  - Upload progress uses `uploadResumes`.
  - Live status comes from the `useCampaignEvents` cache patches, with nothing polled.
  - **Done** rows link to the candidate (`?tab=candidates&open=<application id>`).
  - **Failed** rows show the step that failed (`processing_step`) and the error.
  - A **"Retry failed"** action calls `retryFailedResumes`.
  - Quota rows say when to retry.
- [ ] **Rejected files** (duplicates, wrong type, too large) appear inline with the server's reason.
- [ ] Upload progress is announced through a live region. There are empty, loading, error and partial (some failed) states.
- [ ] Vitest tests: client-side checks, the list's state machine driven by mocked SSE events, retry, rejected files, and the slot wiring.

**Concept to learn:** turning a stream of server events into UI state (an event-driven state machine on top of a query cache).

---

### E1-07 · Review side panel and Person profile
**Status:** todo  ·  **Size:** L  ·  **Branch:** `e1-07-review-panel`

**Goal:** Spec §5.2 and §5.4: review a candidate without leaving the table, then see the Person across campaigns.

**Acceptance criteria**
- [ ] **Review panel** (`features/applications`): replaces E1-05's placeholder sheet and keeps its URL key (`open`), J/K and Esc. It shows:
  - name and contact;
  - the **score breakdown** as sub-score bars from `score_breakdown`;
  - the recommendation;
  - strengths, weaknesses and missing skills;
  - extracted skills and the experience summary;
  - "Also applied to" (the Person's other applications);
  - "Open full profile".
- [ ] **Stage selector and notes** in the panel, saved with `updateApplication`:
  - the stage change is optimistic, and the table row updates too;
  - notes autosave (debounced), with explicit "Saving…", "Saved" and error states;
  - **S** focuses the stage selector.
- [ ] **Person profile `/people/[id]`** (`features/people`):
  - a header with name, contact, links and years of experience, plus an **application switcher** (defaulting to the most recent application);
  - sections **Evaluation · Profile · Applications · Notes**;
  - an info banner when the Person has unresolved identity conflicts.
- [ ] Empty, loading and error states. Vitest tests: panel content, the stage change and its rollback, notes autosave states, the S shortcut, and the profile's application switcher.

**Concept to learn:** autosave UX (debounce, optimistic writes, and visible save state), and route-level composition across features.

---

### E1-08 · Home, Insights and Talent
**Status:** review  ·  **Size:** M  ·  **Branch:** `e1-08-home-insights-talent`

**Goal:** Spec §4: the dashboard screens.

**Acceptance criteria**
- [x] **Home `/`** (from `getHome`): replaces the legacy home page. It shows:
  - active campaigns (status, new uploads, strong matches);
  - uploads still processing across campaigns;
  - recent strong matches, linking to the candidate in its campaign.
- [x] **Insights tab** (from `getCampaignStats`), using Recharts through shadcn's chart wrapper and coloured from tokens:
  - category distribution;
  - score histogram;
  - top skills against required skills;
  - the processing funnel.
- [x] **Talent `/talent`** (from `listPeople`): a paginated table of name, latest role, skills, number of applications and last seen, with search on name or email, all in the URL.
- [x] Empty, loading and error states. Charts have accessible text alternatives. Vitest tests for each screen.

**Concept to learn:** chart accessibility and theming charts with design tokens.

**Notes:**
- **Home** (`features/home`, route `src/app/page.tsx`; the legacy `(legacy)/page.tsx` and its two home-only rules in `legacy.css` are gone, the legacy candidate page still builds). Three sections from `GET /home`:
  - **Active campaigns** as cards: status, new uploads in 24 h, strong matches, processing (plus failed) and last activity; the title link covers the card and opens the workspace.
  - **Recent strong matches** as a table (candidate, campaign, score bar, stage, when scored); the name links to `/campaigns/<id>?open=<application id>`, the review panel's URL (`applicationHref`).
  - **Processing**: the newest in-progress resumes with their step (Queued / Extracting / Analysing), each linking to its campaign's Uploads tab (`campaignHref(id, "uploads")`), and "Showing the 10 newest of N".
  - **First run:** when all three are empty, Home asks `GET /stats` once; with no campaigns at all it shows "Create your first campaign", otherwise the sections with quiet one-line empties ("No active campaigns…"). Home has no event stream, so it **refetches every 5 s only while something is processing** (`refetchInterval`), and mutations and the workspace's live events already invalidate `home`.
- **Insights** (`features/campaigns/insights`, replacing E1-04's placeholder; the empty state's "Upload resumes" switches to the Uploads tab). Four charts from `GET /campaigns/{id}/stats` (the query the KPI strip already uses, so the workspace's events keep both fresh):
  - **category distribution**: horizontal bars in the `--category-*` tokens, plus "Not scored" when some applications have no category;
  - **score histogram**: ten buckets, "0–9" … "90–100", in `--chart-1`;
  - **top skills**: required skills in teal (`--chart-1`), the others neutral (`--chart-4`), with a legend, and the required skills that are not among the top ones listed under the chart;
  - **processing funnel**: uploaded → processed → scored → reviewed (moved to a stage), each with its share of the uploads, and the unfinished resumes by status (queued, extracting, analysing, failed, quota limit).
- **Chart wrapper and tokens.** `src/components/ui/chart.tsx` is shadcn's chart file, written by hand: `shadcn add chart` would also have pinned `recharts@3.8.0` in `package.json` (recharts ^3.8.1 is already there). One change: the legend swatch's colour is a CSS variable, not an inline `backgroundColor`. Series colours are `ChartConfig` entries such as `var(--category-strong)`, which the wrapper scopes to the chart as `--color-<key>`, so dark mode needs no extra code (checked in both themes).
- **Chart accessibility.** Each chart is a labelled region with a visible one-sentence summary ("66 applications: 4 strong, 23 moderate and 36 weak matches, 3 rejected.") and a data table that is always in the accessibility tree (visually hidden). A **Table** toggle (`aria-pressed`, `aria-controls`) shows the table in place of the chart. The SVG is `aria-hidden` with Recharts' keyboard layer off, so nothing hidden is focusable. Hover tooltips are for the mouse; the table is the keyboard and screen-reader route.
- **Talent** (`features/people/talent`, `/talent` replaces the coming-soon page). Name (and email) linking to `/people/<id>`, latest role, up to 3 skills plus "+N", applications and last seen (`last_applied_at`); a row click opens the profile too. `q` and `page` are in the URL (nuqs, defaults left out); the search is debounced (300 ms) together with its page reset, so typing a search on page 3 sends one request (`page=1&q=…`), not one for page 1 of the old search first. Empty ("No people yet"), no match ("Clear search"), past the last page ("Go to the first page"), skeleton and error-with-retry states.
- **Shared code.** `features/campaigns` now uses `src/components/data-states.tsx` (its copy is deleted, backlog item). The campaign status badge moved to `src/components/campaign/status-badge.tsx` and the status vocabulary to `src/lib/campaigns.ts` (Home shows the badge), the search debounce hook to `src/lib/use-debounced-value.ts` (campaign list and Talent), and `campaignHref(id, tab?)` in `src/lib/routes.ts` takes a tab.
- **Browser check** (`next dev -p 3101` against the local API, headless Chrome): Home (10 active campaigns, 10 strong matches, nothing processing) in light and dark; the ML Engineer campaign's Insights in light and dark (26 bars, every fill a `var(--color-…)` that resolves to the theme's token), with the Top skills table toggled; Talent with 812 people, Next → `?page=2` (21–40), search `example` → `?q=example` (4 people), kept after a reload, a row link to the profile, and an empty search in dark. No console errors or failed API calls.
- **Tests.** 32 new tests in 4 files (Home sections, links, first run, polling and states; Insights summaries, data tables, toggle, token colours, empty and processing-only states, retry and refresh on invalidation; the Insights model; Talent rows, links, search and page ↔ URL, debouncing and states); 211 in total.
- **API gaps** (in the [backlog](../backlog.md#platform-and-tooling)): no event stream across campaigns (Home polls while processing); `Home` does not say whether any campaign exists (Home asks `/stats` for the first run); `top_skills` has the top 10 only, so required skills outside it have no count; `listPeople` has one sort order (most recently updated).

---

### E1-09 · Playwright E2E and axe in CI
**Status:** doing  ·  **Size:** M  ·  **Branch:** `e1-09-e2e`

**Goal:** Spec §9: prove the hero flow end to end in CI, with automated accessibility checks.

**Acceptance criteria**
- [ ] **Playwright** runs against the Compose stack with `AI_PROVIDER=fake` and a deterministic seed. It covers the hero flow:
  1. create a campaign with JD extraction;
  2. upload 3 PDFs and see them process;
  3. use the ranked table, filtering it;
  4. open the review panel and change the stage;
  5. open the Person profile;
  6. export CSV.

  It has no fixed sleeps and retries once in CI only.
- [ ] `@axe-core/playwright` runs on every E2E page, with no serious or critical violations.
- [ ] Screenshots of the key screens, in light and dark, are uploaded as a CI artifact.
- [ ] A new **`e2e`** CI job, to be made a required check once it is green on `master`.
- [ ] **Locally:** one command runs the suite against its own Compose project and ports, without touching the dev stack. The container names stay `resumeai_*` by default.

**Concept to learn:** reliable E2E tests in CI (seeded data, auto-waiting, health checks) and automated accessibility checks.

---

### E1-10 · Remove the old UI, product tour, close E1
**Status:** todo  ·  **Size:** S  ·  **Branch:** `e1-10-close-e1`

**Goal:** Spec §11: no old UI left, a README ready for pitching, and E1 closed.

**Acceptance criteria**
- [ ] **The old UI is deleted:**
  - `src/app/(legacy)` (the legacy candidate page and layout), `legacy.css` and the old components;
  - the legacy client `src/lib/api.ts`;
  - `axios` and `react-toastify`.

  The frontend calls only `/api/v2`.
- [ ] The E2E suite also visits Home, Insights and Talent, and the axe checks pass on them.
- [ ] The README has screenshots (light and dark, from the E2E run) and a short **product tour** for pitching.
- [ ] **The E1 definition of done** (spec §11) is checked off, the roadmap marks E1 done, and the remaining E1 follow-ups are in the backlog.

---

## Sprint 7 (rough backlog)

E2 · Identity & tenancy: needs its spec first (Agency → Clients → users, roles, Postgres row-level security), then removing `/api/v1`. Security follow-ups from the backlog (Pillow and the other `pip-audit` findings, pydantic) before E5.

## Sprint review & retro

*(To be filled in at the end of the sprint.)*

- **Demo:**
- **Keep:**
- **Drop:**
- **Try:**
