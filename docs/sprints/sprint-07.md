# Sprint 7 · Tenancy in the data, isolation in the database, sign-in

**Epic:** [E2 · Identity & tenancy](../specs/EPIC-02-identity-tenancy.md)  ·  **Dates:** 2026-11-19 → 2026-11-26  ·  **Status:** in progress

## Sprint goal

> Every row, task, stream and stored file belongs to an **agency**. Postgres row-level security refuses another agency's rows even when the code forgets a filter, and the tenant-isolation suite proves it. People **sign in** with email and password into a server-side session, and `/api/v2` requires one. A temporary `AUTH_REQUIRED=false` keeps the frontend and the E2E job working as the default agency's owner until Sprint 8 adds the login screens.
>
> Also: the backend's dependency advisories are closed before anything is deployed (E5).

## Board

| Ticket | Title | Size | Status |
|---|---|---|---|
| S3 | Backend dependency security upgrades (Pillow, pdfminer.six, pydantic, the rest of `pip-audit`) | M | todo |
| E2-01 | Tenancy expand: `agencies`, `clients`, nullable `agency_id` / `client_id`, writes set them | M | todo |
| E2-02 | Tenancy backfill, verify and contract: NOT NULL, per-agency uniques and matching | M | todo |
| E2-03 | Tenant context: bound sessions, repository filters, Celery, SSE channels and storage keys | L | todo |
| E2-04 | Row-level security: roles, policies, the app role everywhere, the isolation suite | L | todo |
| E2-05 | Identity core: users, sessions, login, CSRF, rate limits, audit; v2 requires a session | L | todo |

**Order** (spec §12):
- **Wave 1:** E2-01 and S3 in parallel.
  - S3 is the only ticket in this wave that changes `pyproject.toml` / `uv.lock`, and the only one that may re-record the v2 OpenAPI snapshot and regenerate the client (pydantic).
  - E2-01 makes no API change, and all snapshots stay as they are.
- **Wave 2:** E2-02 and E2-03 in parallel, after E2-01. E2-02 owns the migrations and `resolve_person`. E2-03 owns `app.platform.tenancy`, the repositories' filters, Celery, SSE and storage.
- **Wave 3:** E2-04 and E2-05 in parallel, after wave 2.
  - E2-04 owns the database roles, the RLS migration and the test harness's switch to the app role.
  - E2-05 owns the `identity` tables and the auth endpoints, and it adds `argon2-cffi`.
  - Whichever merges second rebases on the other. Expect overlap in the test fixtures.

**Working agreements** (from the [Sprint 6 retro](sprint-06.md#sprint-review--retro)):
- **Only a sprint docs PR edits the board, the roadmap and other tickets' sections.** A ticket PR ticks its own acceptance criteria in the spec (§12), may add notes under its own section here, and adds backlog items next to related ones.
- **Rebuild the local Compose stack** after any merge that changes dependencies or adds an init script (S3, E2-04, E2-05).
- **Migrations** are tested upgrade → downgrade → upgrade. Every PR with a migration says in its body how it was rehearsed on the Compose stack with the seed data.
- **Snapshots:** the v1 characterization snapshots and the v2 contract snapshots stay unchanged unless the ticket says otherwise. Any change is listed and explained in the PR.
- **Security changes** are checked against the spec's checklist (§10). The PR says which items it covers.

---

### S3 · Backend dependency security upgrades
**Status:** todo  ·  **Size:** M  ·  **Branch:** `s3-backend-security-deps`

**Goal:** Close the backend's known advisories before E5 ([backlog](../backlog.md#platform-and-tooling)). Uploaded PDFs are untrusted input, so the PDF parsing stack comes first.

**Acceptance criteria**
- [ ] Pillow (10.3.0, 53 advisories) and pdfminer.six (20231228) are upgraded to releases without known advisories. If pdfplumber pins them, it is upgraded too, and PDF extraction is re-checked on the sample PDFs and the ingestion tests.
- [ ] The other `pip-audit` findings are upgraded where a fix exists: filelock, pyasn1, protobuf, tornado (flower), certifi, python-dotenv, and dev-only pytest and virtualenv. What can't be fixed is listed with the reason.
- [ ] pydantic (2.9.2) and pydantic-settings are upgraded to the current 2.x. If the v2 OpenAPI document changes, the snapshot is re-recorded with an explanation and the TypeScript client is regenerated, so the drift check passes.
- [ ] `pip-audit` on `uv export --all-groups` reports nothing fixable. The full test suite, mypy, ruff and import-linter pass, and the Compose stack runs the hero flow (`make e2e`).

**Concept to learn:** reading advisories for a transitive dependency, and judging how far an upgrade may move a public contract.

---

### E2-01 · Tenancy expand
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e2-01-tenancy-expand`

Goal and acceptance criteria: [spec §12, E2-01](../specs/EPIC-02-identity-tenancy.md#e2-01--tenancy-expand). Design: spec §3 (tables, module ownership) and §7.1 (phase 1).

---

### E2-02 · Tenancy backfill, verify and contract
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e2-02-tenancy-contract`

Goal and acceptance criteria: [spec §12, E2-02](../specs/EPIC-02-identity-tenancy.md#e2-02--tenancy-backfill-verify-and-contract). Design: spec §3.3, §7.1 (phases 2–4) and §7.3. Takes the `resumes (agency_id, campaign_id)` index from the backlog.

**Notes:**
- Deploy order and commands: [runbook](../runbooks/tenancy-backfill.md). Deploy the E2-02 code, run the backfill and the verification, then `alembic upgrade head`; the contract migration refuses to run before the backfill has finished.
- `resolve_person(..., agency_id=...)` takes the agency explicitly; it must be the session's. Since the merge with E2-03 the callers pass `agency_id_of(session)` and the matching lookups use `tenant_filter`. Until the contract the default agency also matches identifiers not yet backfilled (`tenant_filter`'s transition branch, removed with E2-04).
- After this merges, rebuild nothing (no new dependency), but a database at E2-01 needs the backfill before `alembic upgrade head`. On a fresh volume, `make setup` works as before (empty tables pass the check).

---

### E2-03 · Tenant context
**Status:** todo  ·  **Size:** L  ·  **Branch:** `e2-03-tenant-context`

Goal and acceptance criteria: [spec §12, E2-03](../specs/EPIC-02-identity-tenancy.md#e2-03--tenant-context). Design: spec §6.3, §6.5 and §6.6.

**Notes:**
- **Overlap with E2-02** (whichever merges second rebases): `talent.service` (the four `agency_id=` lines in `_resolve_locked` / `_attach` read `agency_id_of(session)`; E2-02's explicit agency parameter replaces them), `talent.repository` (E2-03 filters the people reads and `get_person_resume`, and leaves `find_identifiers`, `lock_person` and the lock keys to E2-02), the test fixtures that now bind their sessions (`tests/conftest.py`, `tests/talent/conftest.py`, `tests/ingestion/conftest.py`, the concurrency test's workers), and `reset_stuck_sync`, which now returns each row's agency. E2-03 adds no migration and does not touch `verify_people_applications`.
- After E2-02's contract, the `agency_id IS NULL` branch of `tenancy.tenant_filter` is dead; E2-04 removes it (backlog).

---

### E2-04 · Row-level security
**Status:** todo  ·  **Size:** L  ·  **Branch:** `e2-04-rls`

Goal and acceptance criteria: [spec §12, E2-04](../specs/EPIC-02-identity-tenancy.md#e2-04--row-level-security). Design: spec §6, §7.2 and §11.1–11.2; [ADR 0009](../adr/0009-tenant-isolation-row-level-security.md) is accepted in this ticket.

**Notes:**
- Deploy order and commands: [runbook](../runbooks/tenancy-rls.md) (drain the queues, `make db-roles`, migrate as the owner, recreate the services). Its section 8 is the exact list for an existing local stack; no `.env` edit is needed (Compose builds the URLs from `POSTGRES_DB` and two password variables with defaults). No new dependency, so no image rebuild, but the postgres container is recreated (new mounts) and the backend services are recreated (new URLs).
- **Merged with E2-05** (which merges first, PR #58): `e2c04a7f3b18` now follows `e2c05b3f7a21`. The identity tables are global (on `app.platform.rls.GLOBAL_TABLES`) and keep the grants E2-05's migration gives them (`audit_events`: `SELECT, INSERT` only; E2-04's downgrade leaves them alone). The identity CLI connects as the owner (`MIGRATION_DATABASE_URL`); the seed and `seed_users` stay on the app role (identity tables are global; the default agency's data through a bound session). The API sweep signs in for real as an owner of the default agency (`users` / `auth_client`). One local upgrade covers both: runbook section 8 (rebuild for `argon2-cffi`, `make db-roles`, migrate, recreate).

---

### E2-05 · Identity core
**Status:** todo  ·  **Size:** L  ·  **Branch:** `e2-05-identity-core`

Goal and acceptance criteria: [spec §12, E2-05](../specs/EPIC-02-identity-tenancy.md#e2-05--identity-core). Design: spec §4, §5.4, §8 and §10; [ADR 0010](../adr/0010-server-side-sessions.md) is accepted in this ticket.

**Notes:**
- **After pulling:** rebuild the images (new dependency `argon2-cffi`), run `alembic upgrade head` (migration `e2c05b3f7a21`), then `make seed` (or the seed command with `--if-empty`) to get the sign-in accounts. A local `.env` needs no change: the dev Compose backend defaults `AUTH_REQUIRED` to `false`, so the frontend keeps working without signing in. Set `AUTH_REQUIRED=true` in `.env` to try the session for real (README: "Signing in locally").
- **Overlap with E2-04** (whichever merges second rebases): both migrations follow `e2b02c7a4d19`, so the second one's `down_revision` moves to the first. E2-05's grants run only when `hireflow_app` exists and leave `audit_events` with `SELECT, INSERT` (E2-04's acceptance criterion for that table); E2-04's isolation suite should put the five new tables on the global allowlist (§6.4). The test fixtures meet in `tests/conftest.py` (the `client` fixture now overrides `current_principal` with an owner of the default agency) and the new `tests/identity/conftest.py` (`auth_client` opens an unbound session per request, which E2-04's app role and RLS need too). `tenant_db` moved from `app.platform.request_tenant` to `app.modules.identity.dependencies`.
- `AUTH_REQUIRED=false` is temporary: E2-09 removes it and the E2E stack signs in.

---

### D1 · Redesign rollout
**Status:** in review  ·  **Branch:** `d1-redesign`

The approved direction (three mock-ups, `docs/design/source`) across the whole frontend, with no API change: paper and ink greys with one forest-green accent (light and dark, `check:contrast` green), Instrument Sans with tabular figures, hairlines instead of boxed cards, no KPI grids, scores as a number, a 5-segment meter and a plain word (Strong, Possible, Unlikely, Not a match), recruiter words for stages (New, Phone screen, Not a fit). Home is a "today" page with one sentence and a review queue; the workspace collapses the sidebar to an icon rail and its review panel leads with the next-stage action. Style guide and voice rules: [`docs/design/README.md`](../design/README.md); standalone mock-ups in `docs/design/mockups/`. Data the mock-ups use that the API lacks (a cross-campaign queue, current role, stage counts per campaign, median score, last-processed time) went to the [backlog](../backlog.md) as D1 items. README screenshots refreshed from a friendly-names E2E run.

---

## Sprint review & retro

*(To be filled in at the end of the sprint.)*

- **Demo:**
- **Keep:**
- **Drop:**
- **Try:**
