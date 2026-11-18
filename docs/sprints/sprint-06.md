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
| S2 | Upgrade Next.js (security) | S | todo |
| E1-06 | Uploads tab: dropzone, live processing list, retry, quota state | M | todo |
| E1-07 | Review side panel and Person profile | L | todo |
| E1-08 | Home dashboard, Insights tab and Talent list | M | todo |
| E1-09 | Playwright E2E + axe in CI, polish, screenshots; remove the old UI | M | todo |

**Order** (stacks no more than two deep):
- **Wave 1:** S2, E1-06 and E1-07 in parallel, branched from `master`.
  - Only S2 changes `package.json` / `package-lock.json`. E1-06 and E1-07 add no npm dependencies, so the lockfile can't conflict.
  - E1-06 works in `features/uploads` and the workspace's Uploads slot. E1-07 works in `features/applications` (the panel) and `features/people` (the profile).
- **Wave 2:** E1-08, after E1-06 and E1-07 merge. It touches the workspace (Insights), `features/people` (Talent) and `/` (Home).
- **Wave 3:** E1-09, last. It deletes the old UI and its dependencies, and adds the `e2e` CI job.

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
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e1-08-home-insights-talent`

**Goal:** Spec §4: the dashboard screens.

**Acceptance criteria**
- [ ] **Home `/`** (from `getHome`): replaces the legacy home page. It shows:
  - active campaigns (status, new uploads, strong matches);
  - uploads still processing across campaigns;
  - recent strong matches, linking to the candidate in its campaign.
- [ ] **Insights tab** (from `getCampaignStats`), using Recharts through shadcn's chart wrapper and coloured from tokens:
  - category distribution;
  - score histogram;
  - top skills against required skills;
  - the processing funnel.
- [ ] **Talent `/talent`** (from `listPeople`): a paginated table of name, latest role, skills, number of applications and last seen, with search on name or email, all in the URL.
- [ ] Empty, loading and error states. Charts have accessible text alternatives. Vitest tests for each screen.

**Concept to learn:** chart accessibility and theming charts with design tokens.

---

### E1-09 · E2E, polish, and removal of the old UI
**Status:** review  ·  **Size:** M  ·  **Branch:** `e1-09-e2e`

**Goal:** Spec §9 and §11: prove the hero flow end to end in CI, then close E1.

**Acceptance criteria**
- [x] **Playwright** runs against the Compose stack with `AI_PROVIDER=fake` and a deterministic seed. It covers the hero flow:
  1. create a campaign with JD extraction;
  2. upload 3 PDFs and see them process;
  3. use the ranked table, filtering it;
  4. open the panel and change the stage;
  5. open the Person profile;
  6. export CSV.

  It has no fixed sleeps and retries once in CI only.
- [x] `@axe-core/playwright` runs on every E2E page, with no serious or critical violations.
- [ ] Screenshots of the key screens in light and dark are uploaded as a CI artifact. Several go into the README with a short **product tour** for pitching. *(Screenshots and the artifact: done. README and product tour: E1-10.)*
- [ ] A new **`e2e`** CI job, made a required check. *(The job: done. Required: after it has been green on `master`, [backlog](../backlog.md#platform-and-tooling).)*
- [ ] **The old UI is deleted:** *(E1-10)*
  - `src/app/(legacy)`, `legacy.css` and the old components;
  - the legacy client `src/lib/api.ts`;
  - `axios` and `react-toastify`.

  The frontend calls only `/api/v2`.
- [ ] **The E1 definition of done** (spec §11) is checked off, the roadmap marks E1 done, and the remaining E1 follow-ups are in the backlog. *(E1-10)*

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

## Sprint 7 (rough backlog)

E2 · Identity & tenancy: needs its spec first (Agency → Clients → users, roles, Postgres row-level security), then removing `/api/v1`. Security follow-ups from the backlog (Pillow and the other `pip-audit` findings, pydantic) before E5.

## Sprint review & retro

*(To be filled in at the end of the sprint.)*

- **Demo:**
- **Keep:**
- **Drop:**
- **Try:**
