# Sprint 6 · HireFlow AI hero flow, end to end

**Epic:** [E1 · Product design & frontend foundation](../specs/EPIC-01-frontend.md)  ·  **Dates:** 2026-11-18 → 2026-11-25  ·  **Status:** done (closed 2026-11-19)

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
| E1-08 | Home dashboard, Insights tab and Talent list | M | done ([#45](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/45)) |
| E1-09 | Playwright E2E + axe in CI (`e2e` job), screenshots artifact | M | done ([#46](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/46)) |
| E1-10 | Remove the old UI, README product tour, close E1 | S | done ([#48](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/48)) |

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
**Status:** done ([#42](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/42))  ·  **Size:** S  ·  **Branch:** `s2-next-upgrade`

**Goal:** Close the critical `next` 16.2.9 advisories ([backlog](../backlog.md#platform-and-tooling)): a middleware/proxy bypass with Turbopack, a Server Actions DoS and SSRF, plus the `postcss` and `sharp` advisories that come through it.

**Acceptance criteria**
- [ ] `next` and `eslint-config-next` are upgraded together to the current 16.x patch release with the fixes, pinned exact, with the lockfile updated. Breaking changes are checked against `node_modules/next/dist/docs/`.
- [ ] `npm audit` shows no critical or high advisory in `next`, `postcss` or `sharp`. What remains is listed in the PR: `axios` goes with the old UI in E1-09, and dev-only tooling is noted.
- [ ] Lint, `tsc`, Vitest, `next build`, the contrast check and the CI drift check all pass. The frontend Docker image builds and the app runs in Compose.

**Concept to learn:** reading a framework's security advisories and changelog to judge upgrade risk.

---

### E1-06 · Uploads tab
**Status:** done ([#43](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/43))  ·  **Size:** M  ·  **Branch:** `e1-06-uploads`

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
**Status:** done ([#44](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/44))  ·  **Size:** L  ·  **Branch:** `e1-07-review-panel`

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
**Status:** done ([#45](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/45))  ·  **Size:** M  ·  **Branch:** `e1-08-home-insights-talent`

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
**Status:** done ([#46](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/46))  ·  **Size:** M  ·  **Branch:** `e1-09-e2e`

**Goal:** Spec §9: prove the hero flow end to end in CI, with automated accessibility checks.

**Acceptance criteria**
- [x] **Playwright** runs against the Compose stack with `AI_PROVIDER=fake` and a deterministic seed. It covers the hero flow:
  1. create a campaign with JD extraction;
  2. upload 3 PDFs and see them process;
  3. use the ranked table, filtering it;
  4. open the review panel and change the stage;
  5. open the Person profile;
  6. export CSV.

  It has no fixed sleeps and retries once in CI only.
- [x] `@axe-core/playwright` runs on every E2E page, with no serious or critical violations.
- [x] Screenshots of the key screens, in light and dark, are uploaded as a CI artifact.
- [x] A new **`e2e`** CI job, to be made a required check once it is green on `master` ([backlog](../backlog.md#platform-and-tooling)).
- [x] **Locally:** one command runs the suite against its own Compose project and ports, without touching the dev stack. The container names stay `resumeai_*` by default.

**Concept to learn:** reliable E2E tests in CI (seeded data, auto-waiting, health checks) and automated accessibility checks.

**Notes:**
- **Split.** This PR is the E2E half: Playwright, axe, screenshots and the `e2e` job. Deleting the old UI, the README product tour and closing E1 move to E1-10.
- **Stack.** `docker-compose.e2e.yml` is an override on `docker-compose.yml`, run as its own Compose project `hireflow-e2e`. `docker-compose.yml` is unchanged, so the dev stack keeps its `resumeai_*` names. The override resets `container_name` (`!reset`), so containers are `hireflow-e2e-<service>-1`. It has its own volumes and publishes only the frontend (13000) and the API (18000). Settings come from `.env.example` (`env_file` plus `--env-file` for interpolation; `.env` is never read), with `AI_PROVIDER=fake` and the E2E CORS origin. Code is baked into the images (no bind mounts, no `--reload`). The frontend is a production build (`frontend/Dockerfile.e2e`, standalone output, `NEXT_PUBLIC_API_URL` baked in), so pages don't compile on first visit while a test waits. Beat and Flower are left out. Health checks are tightened so `up --wait` returns as soon as the services are ready. Needs Compose 2.24.4 or newer (`!reset` / `!override`).
- **One command.** `scripts/e2e.sh` (`make e2e`, or `bash scripts/e2e.sh` without make) runs `up` (build, `--wait`, `alembic upgrade head`, `app.devtools.seed --people 40 --campaigns 3 --seed 42 --if-empty`), `test` (Playwright on the host), then `down -v`. `E2E_KEEP=1` keeps the stack up; there are also `make e2e-up`, `e2e-test` (with `E2E_ARGS="--ui"`) and `e2e-down`. On failure it writes the stack logs to `frontend/e2e-results/compose.log`.
- **Data.** The seed is a small deterministic background (lists aren't empty). The test creates everything it asserts on: a campaign titled with the run id, and three PDFs generated in memory (`e2e/support/resume-pdf.ts`, one page of Helvetica text) with run-unique e-mails, so uploads aren't de-duplicated and People aren't merged across runs. The fake AI's rules fix the outcome: Asha Verma 90 (strong), Rahul Iyer 59 (moderate), Meera Nair 28 (weak). The test asserts the order, the scores, the categories and the CSV.
- **Selectors.** Roles and accessible names only (`table "Candidates"`, `dialog "<name>"`, `combobox "Stage"`, `search "Filter candidates"`, the dropzone's `PDF resumes` input…). No `data-testid` was added. Waiting uses web-first assertions only (Done rows get 60 s, everything else 10 s). Screens are "settled" when no `aria-busy="true"` is left. The stage change is checked optimistically in the table, then again after a reload.
- **axe and screenshots.** `checkScreen` runs axe (all rules; serious and critical fail, as soft assertions so one run reports every page) and takes a full-page screenshot in light and in dark (`emulateMedia`, next-themes follows the system). It covers the campaign list, new campaign (after extraction), the candidates table with the panel, uploads and the Person profile. Output goes to `frontend/e2e-results/screenshots/{light,dark}/NN-name.png`; toasts are hidden in the screenshots. Each page's full axe result is attached to the HTML report.
- **axe findings, fixed.** (1) Required-skill chips in the table had teal text on `bg-primary/10` at 4.44:1 on an active row (`color-contrast`, serious). The chip now has foreground text and keeps its tint and border. (2) The sort buttons were labelled "Sort by name" / "Sort by last update" while showing "Candidate" / "Updated" (`label-content-name-mismatch`, WCAG 2.5.3). They are now "Sort by candidate name" / "Sort by last updated", and the unit tests are updated to match. No other serious or critical violations on any page, in either theme.
- **CI job `e2e`.** Node 22 with the npm cache, `npm ci`, `playwright install --with-deps --only-shell chromium`, `scripts/e2e.sh up`, then `scripts/e2e.sh test` (CI: one retry, `github` reporter). It uploads `e2e-screenshots` always and `e2e-report` (HTML report, traces, `compose.log`) on failure, and runs `down` always. Browsers aren't cached (Playwright advises against it; the apt dependencies can't be cached anyway). Docker layers aren't cached yet ([backlog](../backlog.md#platform-and-tooling)). The `compose` job also validates the E2E override. Not a required check yet.
- **Proof (local, Windows, Docker Desktop).** Two full `scripts/e2e.sh` runs from a removed stack, both green. Run 1: 210 s in total (including the frontend image rebuild; the test took 57 s on a cold stack). Run 2: 66 s in total (cached images; the test took 22 s). A deliberately wrong expectation (Asha's score 91) failed at the ranking assertion with the received row text; it was reverted. `@playwright/test` 1.65.1 and `@axe-core/playwright` 4.14.0 are pinned. Vitest ignores the specs (`src/**/*.test.*` only), `tsc` checks them, and ESLint ignores `e2e-results/`.

---

### E1-10 · Remove the old UI, product tour, close E1
**Status:** done ([#48](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/48))  ·  **Size:** S  ·  **Branch:** `e1-10-close-e1`

**Goal:** Spec §11: no old UI left, a README ready for pitching, and E1 closed.

**Acceptance criteria**
- [x] **The old UI is deleted:**
  - `src/app/(legacy)` (the legacy candidate page and layout), `legacy.css` and the old components;
  - the legacy client `src/lib/api.ts`;
  - `axios` and `react-toastify`.

  The frontend calls only `/api/v2`.
- [x] The E2E suite also visits Home, Insights and Talent, and the axe checks pass on them.
- [x] The README has screenshots (light and dark, from the E2E run) and a short **product tour** for pitching.
- [x] **The E1 definition of done** (spec §11) is checked off, the roadmap marks E1 done, and the remaining E1 follow-ups are in the backlog.

**Notes:**
- **Deleted:** `src/app/(legacy)` (the candidate page, its layout and `legacy.css`; no old components were left, `react-toastify` went in E1-01) and `src/lib/api.ts`, the axios `/api/v1` client. `npm uninstall axios` also drops ten packages only it needed (`follow-redirects`, `form-data`, …) from the lockfile. `src` has no `/api/v1` call and no `axios` or `react-toastify` import; the ESLint ban on them now covers all of `src`, not only features and `lib/api|query`.
- **Old `/candidates/<id>` links.** The old candidate id is the application's id, and `getApplication` returns its `campaign_id`, so `/candidates/[id]` is now a small client page (`CandidateRedirect` in `features/applications`) that looks the application up and replaces the URL with `/campaigns/<campaign>?open=<id>`, the review panel open; an unknown id shows "Candidate not found". Client side, not a server redirect: the Next server can't reach the API at `NEXT_PUBLIC_API_URL` (the browser's URL) inside Compose. Ids of candidates merged during E0 (`legacy_candidate_map`) don't resolve in v2 ([backlog](../backlog.md#platform-and-tooling)).
- **E2E.** The hero flow goes on after the export: **7.** the campaign's Insights (category and funnel summaries, the category chart's table toggle), an old `/candidates/<id>` link landing on `?open=`, **8.** Talent (the list, then a search for the run id: its 3 people, one opened), **9.** Home (the campaign's card and the strong match linking to its review). Each screen gets `checkScreen` (axe in light and dark, screenshots in both themes). One spec, one journey: it adds about 8 s to the test.
- **axe finding, fixed.** Insights' visually hidden data tables kept their scroll container (`overflow-x-auto`), a 1 px scrollable region with nothing to focus (`scrollable-region-focusable`, serious, both themes). Hidden, the container is no longer scrollable. Home and Talent had no serious or critical violations.
- **README product tour** (Home → new campaign with Extract with AI → uploads → ranked candidates and the review panel → Person profile → Insights → Talent → CSV export), with 9 screenshots in `docs/screenshots/`: all 7 screens in light, Home and the candidates panel also in dark, 1440 × 900, 88–151 KB each (no optimiser needed). `checkScreen`'s `docs` option picks them; `E2E_SCREENSHOT_DIR=docs/screenshots make e2e` refreshes them (README, CONTRIBUTING). README's usage section, feature list, project tree and API section are updated for the new frontend.
- **DoD sweep (spec §11).** Every item is checked off with a pointer to its evidence. New: an ESLint rule, `hireflow/no-inline-styles`, makes "no `style={}`" checkable: a `style` prop may only set CSS custom properties (the score and progress bars, shadcn's sidebar, sonner and chart). The only hex in `src` is shadcn's chart selectors for Recharts' `#ccc` / `#fff`. The v2 document exported from the running backend is identical to the committed one, and `npm run api:generate` gives no diff.
- **Proof:** two full `scripts/e2e.sh` runs from a removed stack, both green: 212 s in total with the image rebuilds (the test took 54 s on a cold stack), then 76 s (36 s). The first run of the new steps found the Insights violation above (it failed in both themes before the fix). Deliberately wrong expectations failed where they should: the category summary ("2 strong … 0 weak") at the Insights step and the strong match's link (`?open=<campaign id>`) at the Home step; both reverted. Lint, `tsc`, Vitest (215 tests, 4 new for the redirect), `next build` and the contrast check pass.
- **Kept-stack flakiness (local).** `--repeat-each=10` against a stack that had already served about 15 runs failed 5 of 10, in old and new steps alike (the campaign POST, "Open full profile", the redirect's lookup, CSS not applied in one dark-theme axe pass). In the one traced, the browser's preflight reached the API and the POST on a new connection never did, so it looks like the Docker Desktop port forwarding on this Windows host rather than the app; 5 earlier repeats and every run on a fresh stack passed. In the [backlog](../backlog.md#platform-and-tooling).

---

## Sprint review & retro

**Goal met:** yes, and E1 is closed.
- Six ticket PRs merged in three waves: #42, #43 and #44, then #45 and #46, then #48.
- The E2 spec ([#47](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/47)) was drafted in parallel with E1-10.
- CI is green on `master`, including the new `e2e` job. The local Compose stack was rebuilt after each dependency change: Next 16.4.6, Playwright, and removing `axios`.

### Demo

- **The whole hero flow in HireFlow AI**, through `/api/v2` and the generated client only:
  - create a campaign with "Extract with AI";
  - drop resumes and watch them process live ([#43](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/43));
  - review ranked candidates in the side panel with J/K/S, and open a Person's profile across campaigns ([#44](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/44));
  - Home, Insights and Talent ([#45](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/45)).
- **Playwright and axe in CI** ([#46](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/46), extended in [#48](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/48)):
  - the flow runs in Chromium against an isolated Compose stack with the fake AI provider;
  - accessibility is checked in light and dark on every screen;
  - the job takes about 3.5 minutes.
- **The old UI, `axios` and every `/api/v1` call from the frontend are gone.** Old `/candidates/<id>` links forward to the review panel. The README has a product tour with screenshots from the E2E run.
- **Security:** Next.js 16.4.6 ([#42](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/42)), and `follow-redirects` went with `axios`.

### Keep

- **Splitting a ticket as soon as a dependency shows up.** E1-09 was split in two when it turned out that deleting the old UI needed E1-08's Home. The E2E work went ahead in parallel, and the deletion moved to E1-10.
- **axe in the browser tests.** It found three real problems the unit tests had missed: chip contrast on an active row, sort buttons whose names didn't contain their labels, and a hidden scrollable region on Insights.
- **Proving a new check can fail.** Each E2E addition was broken on purpose once, to see it fail at the right step.
- **Designing the next epic while closing the current one.** The E2 spec was written in parallel with E1-10, so Sprint 7 starts right away.

### Drop

- **Ticket PRs editing shared doc lines** (the sprint board, the roadmap, the end of the backlog). Two parallel PRs needed a shared commit merged with `-s ours` to avoid conflicts on this file. The board edits came from my own instructions to the agents, against our working agreement.

### Try

- **Only a sprint docs PR edits the board, the roadmap and other tickets' sections.** That covers opening a sprint, waves and closing. A ticket PR edits only its own section in the spec or sprint file, and adds backlog items next to related ones.
- **Pitch screenshots from a curated dataset**, not the E2E run's test data. The campaign title currently shows the run id.
- **Make `e2e` a required check** once it has a few green runs on `master`. It has been green on every `master` run so far.

Next: [Sprint 7](sprint-07.md).
