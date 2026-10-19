# Sprint 3 · People and applications, side by side

**Epic:** [E0 · Platform core](../specs/EPIC-00-platform-core.md)  ·  **Dates:** 2026-10-16 → 2026-10-22  ·  **Status:** done (closed 2026-10-19)

## Sprint goal

> Every upload creates a Person and an Application **as well as** the legacy Candidate (dual write), and a backfill converts all existing candidates. Verification proves the result matches the seed manifest exactly: **808 People, 1,101 Applications, 4 identity conflicts**.

This sprint covers phases 1–3 of the migration plan (spec §6.1): **expand → backfill → verify**. The API still reads the legacy `candidates` table, so nothing changes for users and the 179 snapshots stay byte-identical. Switching reads and dropping `candidates` (phases 4–5) is Sprint 4.

## Board

| Ticket | Title | Size | Status |
|---|---|---|---|
| E0-13 | Harden the test suite (flaky start-up, Hypothesis flake, optional Gemini key) | S | done (#24) |
| E0-14 | Expand migration: people, identifiers, applications and the new resume columns | M | done (#22) |
| E0-15 | `talent.resolve_person` with advisory locks and the concurrency test | L | done (#23) |
| E0-16 | Dual write in the upload pipeline (`hiring.upsert_application`) | L | done (#25) |
| E0-17 | Backfill and verification, rehearsed against the seed data | L | done (#26) |

**Order:**
- **Wave 1:** E0-13 and E0-14 in parallel.
- **Wave 2:** E0-15, which needs the E0-14 tables.
- **Wave 3:** E0-16 and E0-17 in parallel. Both call `resolve_person` and `upsert_application`. E0-16 owns `upsert_application`, so E0-17 starts once that interface is merged, or the two agree it up front.

## Decision: phone numbers without a country code (decided 2026-10-16: Option A)

E0-11 found that with `PHONE_DEFAULT_REGION=IN`, a US number written as `(415) 555-2671` normalises to the **valid Indian landline** `+914155552671`. Matching on it could merge two different people, which is the data-protection incident ADR 0004 is designed to prevent.

| Option | Rule | Trade-off |
|---|---|---|
| **A (recommended)** | Auto-match on a phone only if it was written **with an explicit country code**, or it parses as a **mobile** number in the default region. Region-inferred landlines are still stored, but marked `inferred`. They never match on their own and can feed the E6 review queue. | Keeps ~all real Indian matches, because resumes list mobiles. Removes the cross-country landline risk. Needs one extra column. |
| B | Match on every valid normalised phone (the spec as written) | Simplest, but keeps the known false-merge risk. |
| C | Match on email only; store phones but never match on them | Safest, but misses people who change email and keep their phone, which is common. |

The decision is recorded as an amendment to [ADR 0004](../adr/0004-identity-matching-policy.md) in E0-15.

---

### E0-13 · Harden the test suite
**Status:** done  ·  **Size:** S  ·  **Branch:** `e0-13-test-hardening`

**Goal:** A reliable suite before the concurrency-heavy work starts.

**Acceptance criteria**
- [ ] **Postgres start-up:** the testcontainers fixture waits until Postgres accepts a real query, retrying through the image's init-restart window, before running migrations. Add a regression note to the fixture docstring.
- [ ] **Hypothesis phone test:** reproduce or explain the one-off failure of `test_normalize_phone_is_idempotent`. Use `--hypothesis-seed`, check the deadline vs the first `phonenumbers` metadata load, and check generator edge cases. Fix the root cause rather than only raising the deadline.
- [ ] **Optional Gemini key:** `GEMINI_API_KEY` becomes optional when `AI_PROVIDER=fake`. Startup fails with a clear message when `AI_PROVIDER=gemini` and the key is missing. Covered by a test.
- [ ] **Stability:** the full suite passes 5 consecutive times locally (report timings).

**Concept to learn:** Diagnosing flaky tests; container readiness vs liveness.

---

### E0-14 · Expand migration
**Status:** done  ·  **Size:** M  ·  **Branch:** `e0-14-expand-migration`

**Goal:** Phase 1 of spec §6.1. The new tables and columns exist, and nothing is removed.

**Acceptance criteria**
- [ ] One Alembic migration creates the tables from spec §4. Each module's models live in its own `models.py`.
  - `talent`: `people`, `person_identifiers`, `identity_conflicts`
  - `hiring`: `applications`, `legacy_candidate_map`
  - `ingestion`: `backfill_checkpoints`
- [ ] The migration also adds `resumes.person_id`, `resumes.extracted` and `resumes.processing_step`.
- [ ] Constraints exactly as in spec §4: `UNIQUE(kind, value_normalized)`, `UNIQUE(campaign_id, person_id)`, and FKs only *within* a module ([ADR 0003](../adr/0003-no-cross-module-foreign-keys.md)).
  - If option A is chosen, add a `person_identifiers.inferred` boolean (default false).
- [ ] The migration is reversible: `upgrade → downgrade → upgrade` is tested.
  - It must not lock `candidates` or `resumes` for long: plain `ADD COLUMN` with nullable columns, and no table rewrite.
- [ ] `alembic check` is clean. import-linter is green. **Snapshots unchanged**, because nothing reads the new tables yet.

**Concept to learn:** Expand/contract migrations; which DDL operations rewrite a table in Postgres and which don't.

---

### E0-15 · `talent.resolve_person` with advisory locks
**Status:** done  ·  **Size:** L  ·  **Branch:** `e0-15-resolve-person`

**Goal:** One function decides which Person a resume belongs to, correctly under concurrency (spec §5.3–5.4).

**Acceptance criteria**
- [ ] `talent.service.resolve_person(session, identifiers, profile, resume_id, uploaded_at) -> ResolveResult` (`person_id`, `matched_existing`, `conflict_id | None`) implements spec §5.3:
  - no match → create a Person
  - one Person matched → attach, add new identifiers, refresh the profile from the most recently *uploaded* resume and keep previous values for missing fields
  - email→A and phone→B → attach to A and record an `identity_conflict`; **never merge**
  - no identifiers → new Person
- [ ] Concurrency per spec §5.4:
  - `pg_advisory_xact_lock(hashtext(kind||':'||value))` on every normalised identifier, in **sorted order**
  - a unique-violation fallback that re-reads once
- [ ] The phone policy decided above is implemented, and ADR 0004 is amended.
- [ ] **Concurrency test:** 20 parallel `resolve_person` calls for the same identity (separate connections, real Postgres) produce exactly 1 Person. The test passes 20 runs in a row. A variant with email↔phone cross-overs proves there are no deadlocks.
- [ ] Unit/integration tests cover every row of the §5.3 table and the profile-refresh rules. mypy strict; import-linter green (`talent` imports nothing else).

**Concept to learn:** Entity resolution under concurrency; advisory locks vs row locks; deadlock avoidance by lock ordering.

---

### E0-16 · Dual write in the upload pipeline
**Status:** done  ·  **Size:** L  ·  **Branch:** `e0-16-dual-write`

**Goal:** Every newly processed resume produces a Person and an Application **in the same transaction** as the legacy Candidate (spec §5.1, §6.1 phase 1).

**Acceptance criteria**
- [ ] `hiring.service.upsert_application(session, campaign_id, person_id, resume_id, scoring) -> Application`:
  - upserts on `(campaign_id, person_id)`
  - a newer resume replaces the scoring
  - an older resume never overwrites a newer one
- [ ] The AI task runs `extract_profile → resolve_person → score → write legacy candidate + upsert application` in one transaction, recording `processing_step` at each step (spec §5.5).
  - Each step is idempotent on retry: skip extraction if `resumes.extracted` is set, and skip resolution if `resumes.person_id` is set.
- [ ] Quota errors set `quota_exceeded` **immediately**, publish the SSE event, and schedule the retry from Google's retry delay (spec §5.5).
- [ ] SSE events add `person_id` and `matched_existing`. These are additive fields, so the existing snapshot shape changes only by those keys, and the PR explains the change.
- [ ] Tests:
  - pipeline with `AI_PROVIDER=fake`: a repeat applicant to two campaigns gives 1 Person and 2 Applications; the same campaign twice gives 1 Application
  - retry after a failure at each step creates no duplicates
- [ ] The legacy API responses are unchanged (snapshots), apart from the documented SSE additions.

**Concept to learn:** Dual writes; idempotent multi-step jobs; keeping old and new models consistent in one transaction.

---

### E0-17 · Backfill and verification
**Status:** done  ·  **Size:** L  ·  **Branch:** `e0-17-backfill`

**Goal:** Convert every legacy candidate into People and Applications while the app keeps running, and prove the result is correct (spec §6.2–6.3).

**Acceptance criteria**
- [ ] `python -m app.modules.ingestion.backfill`:
  - keyset pagination on `(created_at, id)`, batches of 500
  - a checkpoint in `backfill_checkpoints` after each batch
  - uses the **same** `resolve_person` / `upsert_application` as the live pipeline
  - Application IDs reuse the candidate UUID; merged duplicates go to `legacy_candidate_map`
  - fills `resumes.person_id` and `resumes.extracted`
- [ ] **Idempotent and resumable:**
  - killing the process mid-run and restarting gives the same final result
  - a second full run changes nothing
  - both are tested
- [ ] `python -m app.modules.ingestion.verify_backfill` runs every spec §6.3 check, exits non-zero on failure, and compares against `seed-manifest.json` when one is given.
- [ ] **Rehearsal on the seed data:** backfill then verify gives exactly **808 People, 1,101 Applications, 4 identity conflicts, 24 legacy-map rows**. The rehearsal runs as an automated test (smaller seed) and once manually on the Compose stack, **with uploads happening during the backfill**.
- [ ] Runbook `docs/runbooks/person-application-backfill.md`: order of operations, how to resume, how to verify, how to roll back.

**Concept to learn:** Online backfills; keyset pagination; resumable batch jobs; verification as a release gate.

---

## Sprint 4 (rough backlog)

- Phase 4, **switch:** the compatibility layer serves `/candidates*` from Applications + People. Snapshots must stay identical, with merged legacy IDs resolved through `legacy_candidate_map`. Writes to `candidates` stop.
- Phase 5, **contract:** drop `candidates` after a full sprint without issues. Remove the cross-module FKs (ADR 0003) and move campaign delete to `app/api` (ADR 0005).
- Coverage gate ≥ 80 % on `app/modules/`.
- Epic close: E0 definition of done (spec §10), then design kick-off for **E1, the frontend redesign**.
- Remaining bugs B3–B10, and the Node 20 → 22/24 upgrade.

## Sprint review & retro

**Goal met:** yes. All 5 tickets merged and CI green on `master`. The live rehearsal matched the seed manifest exactly.

### Demo

- **Live rehearsal on the Compose stack:** 1,125 legacy candidates backfilled in **40 s** while 40 new uploads ran through the dual-write pipeline. `verify_backfill` **PASSED**: people 808/808, applications 1,101/1,101, identity conflicts 4/4, legacy-map rows 24/24, plus all 7 integrity checks. A second full run changed nothing (identical checksum).
- **`resolve_person`:** 20 parallel workers for one identity always produce exactly 1 Person (20 runs in a row), with no deadlocks on crossed email/phone combinations. Locks are taken in hash-id order.
- **Phone policy Option A** is implemented and recorded in the [ADR 0004 amendment](../adr/0004-identity-matching-policy.md). A US number without a country code no longer matches an Indian number.
- **Dual write:** a repeat applicant to two campaigns gives 1 Person and 2 Applications. A newer resume updates its Application's scoring. Quota errors surface immediately, and the retry uses Google's retry delay.
- **Tests:** 380 → **542**. The flaky Postgres start and the Hypothesis phone test were fixed at the root cause. `AI_PROVIDER=fake` no longer needs a Gemini key.

### Keep

- **Proving each step on the real stack** (migrations on seeded data, the repeat-applicant smoke test, the live backfill rehearsal), not only in tests.
- **Making agents decide and justify trade-offs:** score before resolve (locks held for milliseconds, not across Gemini calls), and one transaction per candidate in the backfill (bounded advisory locks).
- **Stacking dependent tickets** so wave 3 could start before wave 2 merged.

### Drop

- **Resolving conflicts by picking one side.** It dropped E0-13's tests twice (#23, #25). CI only showed an unused-import warning.
- **Squash-merging a PR that has another PR stacked on it.** #25's squash made #26 conflict on every line E0-17 had touched.

### Try

- **Merge-commit** any PR with children stacked on it. Squash is fine for leaf PRs.
- For conflicts, hand the PR to Claude, who checks the result against **both** parents for dropped lines before pushing.
- Keep worktree paths short on Windows: paths over 260 characters break installing `google-generativeai`.
