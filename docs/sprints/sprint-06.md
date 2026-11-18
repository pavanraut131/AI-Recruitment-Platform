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
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e1-09-e2e-polish`

**Goal:** Spec §9 and §11: prove the hero flow end to end in CI, then close E1.

**Acceptance criteria**
- [ ] **Playwright** runs against the Compose stack with `AI_PROVIDER=fake` and a deterministic seed. It covers the hero flow:
  1. create a campaign with JD extraction;
  2. upload 3 PDFs and see them process;
  3. use the ranked table, filtering it;
  4. open the panel and change the stage;
  5. open the Person profile;
  6. export CSV.

  It has no fixed sleeps and retries once in CI only.
- [ ] `@axe-core/playwright` runs on every E2E page, with no serious or critical violations.
- [ ] Screenshots of the key screens in light and dark are uploaded as a CI artifact. Several go into the README with a short **product tour** for pitching.
- [ ] A new **`e2e`** CI job, made a required check.
- [ ] **The old UI is deleted:**
  - `src/app/(legacy)`, `legacy.css` and the old components;
  - the legacy client `src/lib/api.ts`;
  - `axios` and `react-toastify`.

  The frontend calls only `/api/v2`.
- [ ] **The E1 definition of done** (spec §11) is checked off, the roadmap marks E1 done, and the remaining E1 follow-ups are in the backlog.

**Concept to learn:** reliable E2E tests in CI (seeded data, auto-waiting, health checks) and automated accessibility checks.

---

## Sprint 7 (rough backlog)

E2 · Identity & tenancy: needs its spec first (Agency → Clients → users, roles, Postgres row-level security), then removing `/api/v1`. Security follow-ups from the backlog (Pillow and the other `pip-audit` findings, pydantic) before E5.

## Sprint review & retro

*(To be filled in at the end of the sprint.)*

- **Demo:**
- **Keep:**
- **Drop:**
- **Try:**
