# Sprint 4 · Switch, contract, close E0

**Epic:** [E0 · Platform core](../specs/EPIC-00-platform-core.md)  ·  **Dates:** 2026-10-20 → 2026-10-26  ·  **Status:** planned

## Sprint goal

> The API serves candidates from **People + Applications**. The legacy `candidates` table and the last cross-module foreign key are gone, and the 179 characterization snapshots are still byte-identical. E0's definition of done (spec §10) is met and the epic is closed.

This sprint covers phases 4–5 of the migration (spec §6.1): **switch → contract**. It starts by closing the go/no-go gate in [runbook §8](../runbooks/person-application-backfill.md).

## Board

| Ticket | Title | Size | Status |
|---|---|---|---|
| E0-18 | Close the switch gate: mirror recruiter writes and deletes, re-sync | M | in review |
| E0-19 | Switch reads to People + Applications (phase 4) | L | in review |
| E0-20 | Contract: drop `candidates` and the last cross-module FK (phase 5) | M | in review |
| E0-21 | Close E0: coverage gate, SSE campaign filter (B3), epic definition of done | M | todo |

**Order:** E0-18 → E0-19 → E0-20 → E0-21. The chain is strictly sequential, each PR stacked on the previous one. **Merge stacked PRs with "Create a merge commit"** (Sprint 3 retro).

## Decision: when to contract (decided 2026-10-19: Option A — contract this sprint after a live rehearsal)

Spec §6.1 says to drop `candidates` "after one full sprint without issues". That rule protects real users from a bad switch. We have no production users yet.

| Option | What | Trade-off |
|---|---|---|
| **A (recommended)** | Contract in this sprint, right after the switch passes a live rehearsal on the Compose stack. | E0 closes this sprint and E1 starts next. The rollback path (re-create `candidates` from People + Applications) is documented, but it is lost once the table is dropped. |
| B | Run on the switched reads for a full sprint, then contract in Sprint 5. | Practises the real-world cadence, but E0 stays open another sprint. |

---

### E0-18 · Close the switch gate
**Status:** in review  ·  **Size:** M  ·  **Branch:** `e0-18-switch-gate`

**Goal:** People + Applications stay complete while the legacy endpoints are still live, so the switch loses no recruiter work.

**Acceptance criteria**
- [x] `PATCH /candidates/{id}/category`, `/notes`, `/pipeline` and `PATCH …/candidates/bulk-pipeline` also update the candidate's application, resolved directly by id or via `legacy_candidate_map`. This happens in the same transaction, through `hiring.service`. The category follows the same rule as the backfill.
- [x] `DELETE /candidates/{id}` and bulk delete delete the application when **no other** legacy candidate maps to it. Otherwise they keep it and remove only the map row. The rule is documented and tested.
- [x] `backfill --resync-recruiter-fields` re-applies `pipeline_stage` / `notes` from `candidates` (newest non-null per field, `carry_recruiter_fields`). It is idempotent.
- [x] `verify_backfill` gains a check that recruiter fields on applications equal the rule's result over their candidates.
- [x] All snapshots are byte-identical; the API is unchanged.
- [ ] Every runbook §8 item is ticked, with evidence in the PR. *(E0-18 closes the two code items; the environment items are re-run by the pre-switch procedure in runbook §8 at the start of E0-19.)*

**Concept to learn:** Keeping two write models consistent during a migration window.

---

### E0-19 · Switch reads to People + Applications
**Status:** in review  ·  **Size:** L  ·  **Branch:** `e0-19-read-switch`

**Goal:** Phase 4. Every legacy endpoint is served from the new model, and nothing a client can observe changes (spec §7).

**Acceptance criteria**
- [x] These endpoints are served from `applications` + `people` (+ `resumes.extracted` for per-resume contact fields): `GET /campaigns/{id}/candidates` (all filters, sort, pagination), CSV export, `GET /candidates/{id}`, `GET /resumes/{id}/candidate`, the `PATCH`/`DELETE` endpoints, and campaign/global stats.
  - Names, emails, phones and URLs come from the application's resume snapshot, so responses match the legacy candidate exactly. *(Field mapping: spec §7.1. The Person's profile is deliberately not used: it is cross-campaign.)*
- [x] Legacy ids that were merged resolve through `legacy_candidate_map` and return the surviving application (spec §7). Tested. *(The response carries the application's id.)*
- [x] **Writes to `candidates` stop.** The pipeline no longer creates legacy candidates. Applications get fresh ids. *(The text moved to `resumes.raw_text`, expand migration `e19a0c5d2b71`.)*
- [x] The test factories create People + Applications instead of candidates. **All 179 snapshots stay byte-identical.** Any unavoidable difference must be explained in the PR and approved before merge. *(Byte-identical through the factory switch. One intended change, decided 2026-10-19 (option A): resumes still being analysed no longer have an empty placeholder row; 8 snapshots record it, spec §7.1 and the PR list them.)*
- [x] Performance: the candidate list query plan uses the new indexes (`EXPLAIN` in the PR). There are no N+1 queries for person data. *(`tests/ingestion/test_read_switch.py::test_the_list_queries_use_the_indexes`, `tests/hiring/test_read_path.py`.)*
- [ ] **Live rehearsal on the Compose stack:** seed → backfill → verify → deploy the switch → hit every endpoint and compare responses with a pre-switch capture (a script in `devtools/`). *(Tooling: `app.devtools.capture_api` / `compare_api`, procedure in runbook §8.2. Rehearsed locally on a throwaway Postgres: 2,467 responses, 0 unexpected differences. The Compose run is the reviewer's.)*

**Concept to learn:** Compatibility layers; proving behavioural equivalence with recorded responses.

---

### E0-20 · Contract
**Status:** in review  ·  **Size:** M  ·  **Branch:** `e0-20-contract`

**Goal:** Phase 5. Remove the legacy model and the last schema coupling between modules (ADR 0003).

**Acceptance criteria**
- [x] A migration drops `candidates`. `legacy_candidate_map` is kept, because it is needed for old ids and bookmarks. The downgrade re-creates the table **empty**; the runbook documents how to rebuild it from People + Applications if ever needed. *(`e20c4a7d1b93`; it first copies pre-switch resume text to `resumes.raw_text`, and also drops `backfill_checkpoints`. Rebuild SQL: runbook §8.3 "After E0-20", run by `tests/migrations/test_contract_e0_20.py`.)*
- [x] The cross-module FK `resumes.campaign_id → campaigns` is dropped. *(No cross-module FK remains: `tests/test_no_cross_module_foreign_keys.py` checks `information_schema` against a table → module map.)*
  - Campaign delete moves to `app/api` (ADR 0005): `hiring` deletes the campaign and its applications, and `ingestion` deletes its resumes and stored PDFs, in one transaction. *(The rows in one transaction; the PDFs after the commit, since storage is not transactional: a failed storage delete is logged and left as an orphan for `app.devtools.storage_orphans`, never a failed request. Spec §7.1.)*
  - This also fixes B6 for this path: PDFs left in storage after delete. *(Candidate and bulk deletes too.)*
- [x] Legacy-only code is removed: dual-write paths, legacy candidate repository functions and the `backfill` command. `verify_backfill` becomes `verify_people_applications`, with no legacy checks. *(Also `make_legacy_candidate` and the seed's legacy path: the seed now runs the pipeline's services, so its manifest still checks the matching code.)*
- [x] Snapshots are byte-identical. `alembic check` is clean. import-linter shows no exceptions for legacy code. *(Every response body and the OpenAPI document are unchanged; the three delete snapshots that record object storage now record the PDFs being deleted (B6), listed in the PR.)*

**Concept to learn:** The contract phase; deciding what a rollback really requires.

---

### E0-21 · Close E0
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e0-21-close-e0`

**Goal:** E0's definition of done (spec §10) is met and visible.

**Acceptance criteria**
- [ ] A CI coverage gate of **≥ 80 % on `app/modules/`** (`--cov-fail-under` scoped to modules).
- [ ] **B3 fixed:** the SSE stream only delivers events for the requested campaign. This is a data leak once E2 adds tenants. The snapshot change is explained.
- [ ] Every spec §10 item is ticked with a link to its evidence. The roadmap marks E0 ✅.
- [ ] README "Architecture" section: a module diagram, the request and upload flows, and links to ADRs 0001–0005.
- [ ] The E0 retro and lessons are captured in this file.

---

## Next: E1 · Product design & frontend foundation

Design work for E1 can start **during** this sprint. It is research and mockups, not code, so it doesn't conflict with E0's backend tickets:

- **Screens:** agency → clients → campaigns → talent pool → person, and the key screens.
- **Visual direction:** shown as mockups to choose from.
- **Design system:** tokens, components, light and dark themes.

## Sprint review & retro

*(To be filled in at the end of the sprint.)*

- **Demo:**
- **Keep:**
- **Drop:**
- **Try:**
