# E0 · Platform Core — Design Spec

**Status:** approved  ·  **Date:** 2026-10-13  ·  **Roadmap:** [roadmap.md](../roadmap.md)  ·  **Related ADR:** [0001 Modular monolith](../adr/0001-modular-monolith.md)

## 1. Goal

Turn the existing prototype into a foundation that every later epic can build on safely. That means four things:

1. **Restructure** the backend into a modular monolith with enforced module boundaries.
2. **Split** `Candidate` into **Person** (one record per human, persisting across campaigns) and **Application** (that person applying to one campaign), using a production-grade **expand → backfill → contract** migration.
3. **Add person matching** to the upload pipeline so repeat applicants are recognised.
4. **Make quality enforceable:** real Postgres tests, working CI, and consistent tooling.

**Learning focus:** bounded contexts, zero-downtime data migrations, idempotent jobs, concurrency control, entity resolution, characterization testing, testcontainers and CI quality gates.

## 2. Non-goals

- No frontend changes. The existing UI keeps working through an API compatibility layer, and the UI rebuild is **E1**.
- No tenancy, authentication or `agency_id`. That is **E2**.
- No AI provider abstraction or fallback. That is **E3**. Gemini stays and is only moved behind the `ai` module interface.
- No review queue for duplicate people. Conflicts are recorded only. The queue is **E6**.

## 3. Module structure

```
backend/app/
├── platform/            # shared kernel: settings, DB session, storage, logging, Celery app
└── modules/
    ├── talent/          # Person, PersonIdentifier, IdentityConflict, person matching
    ├── hiring/          # Campaign, Application, pipeline stage, notes, campaign stats, JD parsing
    ├── ingestion/       # upload, resumes table, extract/analyse tasks, SSE progress
    └── ai/              # Gemini client, prompts, ExtractionResult / ScoringResult types
├── api/                 # composition layer: endpoints that span modules (calls services only)
└── devtools/            # seed generator and dev-only scripts (never imported by app code)
```

Each module contains `router.py` (thin HTTP layer), `service.py` (the **public interface**), `models.py`, `schemas.py`, `repository.py` and `tasks.py`, as needed.

**Dependency rules**, enforced by import-linter in CI:

- `ingestion` → `talent`, `hiring`, `ai`
- `hiring` → `talent`
- `talent` and `ai` → nothing else in `modules/`
- Every module → `platform`
- `platform` → nothing in `modules/`
- `devtools` may import anything, and nothing may import `devtools`
- `api` (the composition layer, [ADR 0005](../adr/0005-composition-layer.md)) may import every module's `service` and `schemas`; no module and not `platform` may import `api`
- Only `ai` may import an LLM SDK (`google.*`)
- A module may import only another module's `service` (and the schemas it exposes), never its `models`, `repository` or `tasks`.

Each module owns its tables. No module queries another module's tables directly.

## 4. Data model

### talent

**`people`**

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| full_name | varchar(255) null | |
| github_url, linkedin_url | varchar(500) null | |
| skills, education, work_experience, certifications, projects | JSONB | Profile from the most recently uploaded resume |
| years_of_experience | float null | |
| latest_resume_id | UUID null | Refers to an ingestion-owned resume (no cross-module foreign key) |
| created_at, updated_at | timestamptz | |

**`person_identifiers`**

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| person_id | UUID FK → people, on delete cascade | |
| kind | varchar(10) | `email` or `phone` (CHECK constraint) |
| value_normalized | varchar(320) | |
| raw_value | varchar(320) | Value as written on the resume |
| source_resume_id | UUID null | |
| inferred | boolean, default false | The phone's country was only inferred from `PHONE_DEFAULT_REGION` and it is not a mobile number. Stored, but never auto-matches on its own (Sprint 3 decision, option A; ADR 0004 amendment in E0-15). |
| created_at | timestamptz | |
| | | **UNIQUE(kind, value_normalized)**. Becomes `(agency_id, kind, value_normalized)` in E2. |

**`identity_conflicts`**: id, resume_id, chosen_person_id, conflicting_person_id, reason, created_at, resolved_at (null). This table is written in E0 and consumed in E6.

**`person_resumes`** (added in E0-15): resume_id (PK, refers to an ingestion-owned resume, no foreign key), person_id (FK → people, on delete cascade, indexed), uploaded_at (timestamptz), matched_existing (boolean), conflict_id (FK → identity_conflicts, on delete set null, null), created_at. One row per resolved resume: it makes `resolve_person` idempotent per resume and records the upload time the profile refresh compares (§5.3). `resumes.person_id` holds the same link on the ingestion side, but talent may not read ingestion's table (§3).

### hiring

**`applications`**

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | Reuses the legacy `candidates.id` during backfill |
| campaign_id | UUID FK → campaigns, on delete cascade | |
| person_id | UUID | Refers to talent (no cross-module foreign key) |
| resume_id | UUID | The resume submitted for this application |
| resume_uploaded_at | timestamptz null | Upload time of `resume_id` (added in E0-16): decides whether a later resume replaces the scoring, since hiring may not read `resumes`. Null counts as older than any resume. |
| score, score_breakdown, category, summary, strengths, weaknesses, missing_skills, recommendation | as the legacy `candidates` columns | |
| pipeline_stage, notes | as the legacy `candidates` columns | |
| created_at, updated_at | timestamptz | |
| | | **UNIQUE(campaign_id, person_id)** |

**`legacy_candidate_map`**: candidate_id (PK) → application_id (FK → applications, on delete cascade; candidate_id has no FK so the map outlives `candidates`). This redirects the IDs of legacy candidates that were merged into another application. **Since E0-20 it is a historical id map:** `candidates` is dropped, `candidate_id` refers to nothing, no new rows are written, and old ids (bookmarks, links) keep answering with the surviving application (§7).

**`candidates`** (legacy): **dropped by the contract migration `e20c4a7d1b93` (E0-20).** Its text column was copied to `resumes.raw_text` first (for resumes extracted before the switch); everything else it held is on the application or in the resume's extraction snapshot (§7.1). The downgrade re-creates it empty; rebuilding its rows is in the runbook (§8.3).

### ingestion

**`resumes`** (existing table): add three columns:

- `person_id UUID null`
- `extracted JSONB null`: a snapshot of the AI extraction for this file
- `processing_step varchar(20) null`: one of `extract_text`, `extract_profile`, `resolve_person`, `score`, `save`. It records the current step while processing and the failed step after a failure.

E0-19 adds `raw_text text null`: the text extracted from the PDF, which until the read switch lived on the legacy candidate (`candidates.raw_text`). Never part of an API response.

The **status values stay the same** (`pending | extracting | processing | done | failed | quota_exceeded`) because the frontend depends on them (`UploadZone.tsx`). Step-level detail goes in `processing_step` instead.

**`backfill_checkpoints`** (E0-14): name (PK), last_created_at, last_id, updated_at. One row per batch job: the keyset position of the last committed batch (§6.2). **Dropped in E0-20** with the backfill, its only writer.

**`resumes.campaign_id`** refers to hiring's `campaigns`. Its legacy foreign key (`ON DELETE CASCADE`) was the last one between modules; **E0-20 dropped it**, so a campaign delete deletes the resumes explicitly (§7.1).

### Cross-module references

References between modules are stored as plain UUIDs **without** foreign key constraints. This keeps each module's tables independent, so a module could be split out later. Data consistency between modules is guaranteed by the service layer and checked by the verification script (§6.3).

Since E0-20 **no foreign key crosses a module boundary** (the legacy `resumes.campaign_id → campaigns` and the `candidates` keys are gone). `backend/tests/test_no_cross_module_foreign_keys.py` reads every foreign key from `information_schema`, maps each table to its owning module, and fails on any that crosses modules or on a table without an owner.

## 5. Upload pipeline and person matching

### 5.1 Pipeline

1. **Upload** (`ingestion.router`): validate the file, hash it, apply the existing check for the same file already in this campaign, store it in MinIO, insert a `resumes` row and enqueue `extract_text`.
2. **`extract_text`** (extract_queue): convert the PDF to text, store it in `resumes.raw_text` (before E0-19: on a placeholder legacy candidate), then enqueue `analyse_resume`.
3. **`analyse_resume`** (ai_queue). Each step is safe to retry:
   1. `ai.extract_profile(text)` → store `resumes.extracted`. Skipped if already present.
   2. `ai.score(profile, campaign_requirements)` → ScoringResult. Not stored on its own: a retry scores again.
   3. `talent.resolve_person(identifiers, profile, resume_id)` → `person_id`. Skipped if `resumes.person_id` is already set.
   4. `hiring.upsert_application(campaign_id, person_id, resume_id, resume_uploaded_at, scoring)`, an upsert on `(campaign_id, person_id)`: a newer resume (by `uploaded_at`, ties by resume id) or the same one replaces the scoring, an older one changes nothing, and the recruiter's fields are never written. Since the read switch (E0-19) **no legacy candidate is written** and a new application gets a fresh id. Until then (E0-16..E0-18) this step also wrote the legacy candidate, a new application reused its id, and when the pair already had an application the candidate's id went into `legacy_candidate_map` (E0-17, the backfill's rule). A resume extracted before the switch and analysed after it still has its placeholder candidate: that candidate is linked the same way (never written), so every candidate keeps leading to exactly one application (§6.3).

   Steps 3 and 4 are one transaction, committed once. Scoring runs *before* resolution (E0-16): it does not depend on the Person, and `resolve_person`'s advisory locks are held until the commit, so resolving first would hold them across the multi-second scoring call and serialise every other upload of the same person. `resumes.processing_step` follows the steps and is cleared when the resume is `done`.
4. **Progress events** keep their current shape; the `done` event adds `person_id` and `matched_existing: bool` (the only event sent after the Person is known). They go over Redis pub/sub on **one channel per campaign**, `campaign:{campaign_id}:events`: the tasks publish each event on its resume's campaign channel, and `GET /campaigns/{id}/stream` subscribes to that campaign's channel only, so a stream never receives (and the API process never sees) another campaign's events (**B3, fixed in E0-21**; until then every event went to one global channel, `resume:all_updates`, and every stream forwarded all of them, a cross-tenant leak once E2 adds agencies). A resume deleted while it was processing publishes nothing (its campaign is unknown). The unused per-resume channel `resume:status:{id}` was removed at the same time.

### 5.2 Normalisation (`talent/identity.py`, pure functions)

- **Email:** trim and lowercase.
  - `gmail.com` / `googlemail.com`: remove dots and any `+tag` from the local part (the part before the @), and treat the domain as `gmail.com`.
  - `outlook.com`, `hotmail.com`, `live.com`, `icloud.com`: remove `+tag` only.
  - All other domains: no further changes.
  - Invalid addresses are discarded.
- **Phone:** parse with `phonenumbers` using `PHONE_DEFAULT_REGION` (default `IN`) and store in E.164 format. Unparseable or invalid numbers are discarded.
  - **Match-eligible or inferred** (`classify_phone`, [ADR 0004 amendment](../adr/0004-identity-matching-policy.md#amendment-2026-10-16-phone-numbers-without-a-country-code), option A): a phone is match-eligible if it was written with an explicit country code (`+…` or an international dialling prefix such as `00…`) or is a mobile number (`MOBILE` / `FIXED_LINE_OR_MOBILE`). Otherwise (a landline whose country was inferred from `PHONE_DEFAULT_REGION`, e.g. `(415) 555-2671` → `+914155552671`) it is stored with `inferred = true` and never matches.

### 5.3 Matching rules

| Situation | Result |
|---|---|
| No identifier matches | Create a Person and its identifiers |
| All matches point to one Person | Attach to it, add new identifiers, refresh its profile |
| Email and phone match different People | Attach to the email match and insert an `identity_conflicts` row. Never merge automatically. |
| No usable identifiers | Create a new Person |

Only match-eligible identifiers take part in matching: every valid email, and match-eligible phones (§5.2). An inferred phone never matches and never causes a conflict, on the new resume or on a stored row. It is stored for the resolved Person unless the value already belongs to another Person (one owner per value; logged, not an error). An inferred row is confirmed (`inferred = false`) only when its own Person is matched on the email and the new resume writes the number in a match-eligible way.

**Profile refresh:** take the profile from the most recently *uploaded* resume (`uploaded_at`, ties broken by resume id). A field the new extraction lacks (`null`, `""` or `[]`) keeps its previous value. A resume processed after a newer one never overwrites the newer values: it only fills fields that are still empty, and `latest_resume_id` keeps pointing at the newer resume.

**Idempotent per resume:** a resume already resolved (a `person_resumes` row exists) returns the stored result and writes nothing, so a retried pipeline step cannot create a second Person, identifier or conflict.

### 5.4 Concurrency

- `resolve_person` runs in the **caller's** transaction and never commits; the caller commits together with its own writes. It needs `READ COMMITTED` (the default), so that reads after a lock wait see what the previous holder committed.
- It first takes `pg_advisory_xact_lock(hashtext(kind || ':' || value))` for each normalised identifier (match-eligible **and** inferred) plus `resume:<resume_id>`, before reading anything. The locks are taken in ascending order of the **lock id** (the `hashtext` value), not of the key text, so the order is global even when two keys hash to the same id; a transaction that holds a lock never waits for a lower one, so two workers cannot deadlock.
- `hashtext` is a 32-bit hash, so different keys can collide. A collision only makes two unrelated resolutions wait for each other; correctness rests on `UNIQUE(kind, value_normalized)`, not on the locks being distinct.
- After the advisory locks it locks at most one `people` row (the chosen Person, `FOR NO KEY UPDATE`, which does not block the `FOR KEY SHARE` taken by inserting a foreign key to it). Nothing it does after that waits on another resolution, so the row lock cannot close a cycle either.
- Reads and writes run in a SAVEPOINT. If an insert still hits `UNIQUE(kind, value_normalized)` (only possible for a writer that bypasses `resolve_person`), it rolls back to the savepoint, re-reads and retries once; a second violation is raised to the caller.

### 5.5 Error handling

- A failure sets `status = failed` and leaves `processing_step` at the step that failed. The existing retry-failed endpoint resumes from that step, and the earlier steps are skipped because each is safe to re-run (§5.1).
- A Gemini quota error (`ResourceExhausted`) sets `status = quota_exceeded` **immediately** and publishes an SSE event, so the UI's existing "Quota Limit" state appears. The retry is scheduled using the retry delay Google provides (`AIQuotaExceeded.retry_after`, else 65 s); the Gemini adapter no longer retries quota errors itself. Before E0-16 the worker could sit retrying for minutes before the status changed. When the task's retries are used up the resume stays `quota_exceeded` (not `failed`).

## 6. Migration plan (expand → backfill → contract)

### 6.1 Phases

| Phase | Deliverable | Runtime behaviour |
|---|---|---|
| **1 · Expand** | Alembic migration that creates the new tables and the new `resumes` columns. Nothing is dropped. | The worker **dual-writes**: it writes the legacy `candidates` row and the new tables in one transaction. |
| **2 · Backfill** | `python -m app.modules.ingestion.backfill`: batched, resumable and idempotent. *Removed in E0-20 (historical).* | Runs while the app is live |
| **3 · Verify** | `python -m app.modules.ingestion.verify_backfill`: exits with a non-zero code on any failure. *Since E0-20 `verify_people_applications`, without the legacy checks (§6.3).* | Must pass before Phase 4 |
| **4 · Switch** | Compatibility endpoints read from `applications` and `people`. Writes to `candidates` stop. **Done (E0-19)**, see the notes below and §7. | Frontend unchanged |
| **5 · Contract** | Migration that drops `candidates`, after one full sprint without issues. **Done (E0-20)**, right after the switch's live rehearsal (Sprint 4 decision, option A: no production users yet); see the notes below. | Frontend unchanged |

The backfill and verification lived in `ingestion` because they coordinate across modules. They read legacy candidates through `hiring.service`, resolved people through `talent.service`, upserted applications through `hiring.service`, and wrote `resumes` themselves. No module boundary was crossed.

**Phase 4 done-notes (E0-19).**

- **Reads:** every legacy candidate endpoint (list with all filters, sort and pagination; CSV export; detail; `GET /resumes/{id}/candidate`; the recruiter `PATCH`/`PUT`; bulk pipeline; deletes) and the campaign and global stats are served from `applications` plus the extraction snapshot of each application's current resume (`resumes.extracted`), composed in `app.api` (§7 has the field-by-field mapping). The 179 characterization snapshots are byte-identical; the test factories build People + Applications.
- **Writes to `candidates` stopped:** the extract step stores the PDF's text on the resume (`resumes.raw_text`, expand migration `e19a0c5d2b71`), the AI step writes only the Person and the Application, and a new application gets a fresh id with no map row. The E0-18 mirror code (recruiter writes and deletes recomputed from candidates) is removed; the API writes applications directly. The table, the backfill and `verify_backfill` stay until phase 5 (§6.3 for what verify checks after the switch); `backfill --resync-recruiter-fields` is retired (it would overwrite post-switch edits).
- **Timestamps:** a data migration (`e19b7f3a9c42`, part of the switch deploy) gives each application the earliest `created_at` and latest `updated_at` of its legacy candidates, so the switched responses carry the timestamps the legacy ones did.
- **Equivalence on live data:** `app.devtools.capture_api` / `compare_api` (runbook §8); a local rehearsal on the Compose-size seed (1,125 candidates, edits and deletes through the E0-18 API) compared 2,467 responses: 2,267 identical, 200 expected (the 24 merged legacy ids and the rows, lists, exports and stats that contain them), 0 other differences.
- **Behaviour change (decided 2026-10-19, option A):** a resume that is still being analysed (or failed after its text was extracted) no longer shows as an empty placeholder row; 8 snapshots record it (§7.1).

**Phase 5 done-notes (E0-20).**

- **Migration `e20c4a7d1b93`:** copies the text of resumes extracted before the switch from `candidates.raw_text` to `resumes.raw_text` (where it is `NULL`), then drops the foreign key `resumes.campaign_id → campaigns`, the `candidates` table and `backfill_checkpoints`, under `lock_timeout`. `legacy_candidate_map` stays as a historical id map (§4). The downgrade re-creates both tables **empty** with their original schema and the foreign key; it does not restore rows (rebuild SQL: runbook §8.3). Tested upgrade → downgrade → upgrade in `tests/migrations/test_contract_e0_20.py`.
- **No cross-module foreign key remains** (ADR 0003): checked on the whole schema by `tests/test_no_cross_module_foreign_keys.py`.
- **Campaign delete** moved to `app.api` (ADR 0005): hiring deletes the campaign and its applications (same-module cascade, and their map rows), ingestion deletes the campaign's resumes, in one transaction; the stored PDFs are deleted after the commit. Candidate deletes delete their PDFs the same way. This fixes B6 (§7.1).
- **Removed:** the backfill command, the legacy candidate model, repository and service functions (reading candidates, linking them, the recruiter-field rule), the pipeline's fallback to the candidate's text and its legacy-id linking, `make_legacy_candidate`. `verify_backfill` became `verify_people_applications` (§6.3). The seed creates People and Applications through the pipeline's services (§6.4).
- **Snapshots:** every response is byte-identical, the OpenAPI document included. The three delete snapshots that record object storage now record the PDFs being deleted (B6): campaign delete 1 → 0 objects left, bulk delete 4 → 2, candidate delete 2 → 1.

### 6.2 Backfill algorithm

*Historical: the backfill ran in E0-17 to E0-19 and was removed with `candidates` in E0-20. Kept as the record of how the existing People, Applications and `legacy_candidate_map` rows were made.*

- Iterate over `candidates` ordered by `(created_at, id)` in batches of 500, using **keyset pagination**: each batch starts after the last `(created_at, id)` processed.
- Save a checkpoint (`last_created_at`, `last_id`) in a `backfill_checkpoints` table after each batch.
- For each candidate:
  1. Resolve the Person using the same `resolve_person` code as the live pipeline.
  2. `INSERT … ON CONFLICT (campaign_id, person_id) DO UPDATE` the Application.
     - The first candidate for a pair keeps its own ID.
     - When a later candidate for the same pair arrives, the application takes the newer resume's scoring, and the older ID goes into `legacy_candidate_map`.
  3. Set `resumes.person_id` and `resumes.extracted`. The extraction is rebuilt from the candidate's stored fields.
  4. Carry the recruiter's fields (`pipeline_stage`, `notes`): per field, the value of the newest candidate (by resume `uploaded_at`) of that application that has one (not `NULL`; an empty note counts); a candidate without a value never clears one. The category travels with the scoring: it is the category of the candidate of the application's current resume. The same rule (`hiring.service.expected_recruiter_fields`) is applied by every later write, see §6.5.
- Only candidates whose resume is `done` are backfilled; the others are skipped and counted (the pipeline links them when they finish). A resume already resolved by the dual write keeps its resolution.
- **One transaction per candidate**, the checkpoint committed after each batch (E0-17). A batch-sized transaction would hold every advisory lock of the batch until its commit (about 3 per candidate) and, making many `resolve_person` calls in one transaction, would void the lock-ordering argument of §5.4 against live uploads.
- Running the backfill again after it finishes changes nothing.
- Runbook: [`docs/runbooks/person-application-backfill.md`](../runbooks/person-application-backfill.md).

### 6.3 Verification checks

**Since E0-20:** `python -m app.modules.ingestion.verify_people_applications [--manifest seed-manifest.json]` (read-only, exit `0` / `1`, `2` for bad input). It checks: `applications_have_people`, `applications_have_resumes` (exists, same campaign, same person), `done_resumes_have_people` (agreeing with talent's `person_resumes`), `identifiers_unique`, `people_without_identifiers_justified`, `people_have_resumes`, `applications_have_extractions`, and with a manifest the expected people, applications and identity conflicts (in the manifest's `campaign_ids` only). `legacy_candidate_map` rows are counted, not checked. Without foreign keys between modules (ADR 0003) it is the check that the services kept them consistent. Removed with `candidates`: `candidates_lead_to_one_application` and the manifest's `legacy_candidate_map_rows` / `legacy_candidates` counts.

The history of the checks (as `verify_backfill`, E0-17 to E0-19):

- Every `candidates.id` either is an `applications.id` or appears in `legacy_candidate_map`.
- Every application's `person_id` exists in `people`, and its `resume_id` exists in `resumes`.
- Every resume with status `done` has a `person_id`.
- No person has zero identifiers unless all of their source resumes lacked identifiers (a region-inferred phone, option A, does not count as one).
- The counts of people and applications are reported and checked against the expected seed results.
- Also checked (E0-18): every application's `pipeline_stage`, `notes` and `category` equal the §6.2 rule over its candidates (`recruiter_fields_consistent`). **Removed in E0-19:** after the switch the recruiter edits the application, so it no longer equals the rule over its frozen candidates. It was the pre-switch gate and is run by the E0-18 code (runbook §8).
- After the switch (E0-19): candidates created before it still each lead to one application (a delete removes the application with its resumes, and their candidates go with the resumes through the foreign key); applications created after it have no candidate and are only counted (`without a legacy candidate (new)`); `applications_have_extractions` checks that every application's current resume has the extraction the read path shows; `people_without_identifiers_justified` skips deleted resumes.
- Also checked (E0-17): no `(kind, value_normalized)` twice; an application's resume is in its campaign and resolved to its person; a candidate's application is in its campaign and belongs to its resume's person; every person was resolved from a resume. With a seed manifest that lists `campaign_ids`, the expected counts are taken in those campaigns only, so a rehearsal can upload elsewhere while it runs.

### 6.4 Seed data generator

`python -m app.devtools.seed --people 800 --campaigns 15` uses Faker to produce about 1,100 processed resumes (until E0-20: legacy candidates, which the backfill turned into People and Applications). Since E0-20 it stores each resume with its extraction snapshot and runs it through the pipeline's own services, `talent.resolve_person` and `hiring.upsert_application` (with generated scoring instead of the AI), so the matching code under test produces the People and Applications. The data deliberately includes:

- Emails differing only in letter case.
- Gmail dot and `+tag` variants.
- The same phone written in different formats (`+91 98765 43210`, `09876543210`, `98765-43210`).
- People applying to several campaigns.
- The same person applying twice to one campaign.
- An email/phone conflict between two different people.
- Resumes with no contact details.

The generator also writes a manifest of the expected number of People, Applications and conflicts, derived from its ground truth without running any matching code, which tests and the verification script assert against. (Inserting People and Applications directly would have been simpler, but the seed would then write its own expectations and the comparison would check nothing.)

### 6.5 Switch gate: mirrored legacy writes and deletes (E0-18)

*Superseded by the read switch (E0-19): from then on the API writes applications directly and nothing writes `candidates`; the post-switch rules are in §7. Kept as the record of the gate.*

Between the backfill and the read switch the legacy endpoints are still live, so every legacy write keeps the new model complete, in the same transaction, through `hiring.service`. A candidate's application is the one with the candidate's id, else the one `legacy_candidate_map` redirects it to; a candidate with neither (its resume is still processing) is written alone.

- **Recruiter updates** (`PUT /candidates/{id}/category`, `PATCH /candidates/{id}/notes`, `PATCH /candidates/{id}/pipeline`, `PATCH /campaigns/{id}/candidates/bulk-pipeline`): write the candidate, then **recompute** the application's `pipeline_stage`, `notes` and `category` from all its candidates with the §6.2 rule (never "copy the new value", so a merged application stays right whichever candidate is edited). A manual category is therefore mirrored only from the candidate of the application's current resume.
- **Deletes** (`DELETE /candidates/{id}`, `DELETE /campaigns/{id}/candidates/bulk`):
  - the application is deleted with its **last** candidate (its map rows go by `ON DELETE CASCADE`);
  - otherwise it is kept, only the deleted candidates' `legacy_candidate_map` rows are removed, and the recruiter fields are recomputed from the remaining candidates;
  - if the deleted candidate's id was the application's id, the application **keeps that id** (the remaining candidates' map rows point at it; renaming a primary key would break them). After the switch, the deleted id still resolves to the surviving application;
  - if the deleted candidate held the application's current resume, the application takes the resume and stored scoring of the newest remaining candidate, as the backfill would. So `applications_have_resumes` stays green.
- **Upload pipeline:** after linking a candidate it recomputes the same fields (a recruiter may have edited the candidate while its resume was processing).
- **Locks:** candidates are locked before their applications (`FOR UPDATE`, id order), the order the pipeline writes them in; the recomputation reads the candidates only while holding the application's lock, so two concurrent edits of one application's candidates cannot each miss the other.
- **Re-sync:** `python -m app.modules.ingestion.backfill --resync-recruiter-fields` recomputes every application with the same rule (idempotent). Run it once after E0-18 is deployed to repair edits made before it, then verify (runbook §8).
- The endpoints that order candidates by resume upload time (ingestion's data) are composed in `app.api` (ADR 0005); paths, methods, names and responses are unchanged.

## 7. API compatibility layer

- The existing `/api/v1/candidates*` and `/api/v1/campaigns/{id}/candidates` endpoints keep their **exact response shapes**. They are rebuilt from the Application and the extraction snapshot of its current resume (E0-19, table below).
- A request with a merged legacy ID is resolved through `legacy_candidate_map` and returns the surviving application.
- Characterization tests (§8) prove the response shapes are unchanged.
- No new public endpoints in E0. The new resource-oriented API is designed in E1 alongside the generated client.

### 7.1 Field mapping (E0-19)

A legacy "candidate" is now an **application**: one row per (campaign, person). Per-resume facts come from the extraction snapshot of the application's **current** resume (`applications.resume_id` → `resumes.extracted`, an `ExtractionResult` dumped to JSON): exactly what the pre-switch pipeline copied onto that resume's candidate. The judgement and the recruiter's work come from the application. The Person's profile (`people`) is **not** used: it follows the person's newest resume in *any* campaign, so it can differ from what this application was scored on. Hiring builds the responses (`hiring.service.candidate_detail` / `candidate_list_item`); ingestion hands over the snapshots (`ingestion.service.resume_profiles`); `app.api` composes the endpoints.

| Response field (`CandidateDetail`; `CandidateListItem` is a subset) | Source after the switch | Before the switch |
|---|---|---|
| `id` | `applications.id` (the legacy candidate's id for backfilled rows; a merged legacy id answers with this id) | `candidates.id` |
| `resume_id` | `applications.resume_id` (the newest resume of the application) | `candidates.resume_id` |
| `campaign_id` | `applications.campaign_id` | `candidates.campaign_id` |
| `name`, `email`, `phone`, `github_url`, `linkedin_url` | `resumes.extracted->>…` of `applications.resume_id` | `candidates.*` (same values: written from the same extraction) |
| `skills`, `education`, `work_experience`, `certifications`, `projects` | `resumes.extracted->…` (`[]` when absent) | `candidates.*` |
| `years_of_experience` | `(resumes.extracted->>'years_of_experience')::float` | `candidates.years_of_experience` |
| `summary`, `strengths`, `weaknesses`, `missing_skills`, `recommendation`, `score`, `score_breakdown` | `applications.*` (scoring of the current resume) | `candidates.*` |
| `category` | `applications.category` (AI category of the current resume, or the recruiter's override) | `candidates.category` |
| `notes`, `pipeline_stage` | `applications.*` (the recruiter edits these directly) | `candidates.*` |
| `created_at`, `updated_at` | `applications.*`; aligned once with the legacy candidates at the switch (earliest `created_at`, latest `updated_at`, migration `e19b7f3a9c42`) | `candidates.*` |
| `raw_text` | not in any response (moved to `resumes.raw_text`) | not in any response |

Other endpoints:

| Endpoint | After the switch |
|---|---|
| List (`GET /campaigns/{id}/candidates`) | Application filters (`category`, `min_score`, `max_score`, `pipeline_stage`) in hiring's query; profile filters (`search` on name/email, `skill`, `min_experience`) on the snapshot in ingestion, with the legacy semantics (B2: `ILIKE`, wildcards escaped, per array element). Order: `score DESC NULLS LAST`, then `id` (legacy ties came back in arbitrary order; the id makes pages stable). At most 4 queries per request, whatever the page size (2 without profile filters). |
| CSV export | Same rows and columns from the same sources; B8 kept (no `skill` / `min_experience`). |
| `GET /resumes/{id}/candidate` | The application whose current resume it is; for an older resume of a merged application, the application of its Person in its campaign; 404 `Candidate not yet processed` while it has none. |
| `PUT …/category`, `PATCH …/notes`, `PATCH …/pipeline` | Write the application (legacy ids resolved); the response is its `CandidateDetail`. |
| Bulk pipeline | Updates the campaign's applications the ids name (own or merged legacy ids), each once; returns how many applications. |
| `DELETE /candidates/{id}`, bulk delete | Delete the application, its current resume and every other resume of its Person in its campaign (each was one legacy candidate of the row the recruiter sees), and its map rows with it. Bulk returns how many applications were deleted. Since E0-20 the resumes' stored PDFs are deleted after the commit (B6 fixed; until then they stayed in storage). |
| `DELETE /campaigns/{id}` (E0-20) | Composed in `app.api`: hiring deletes the campaign and its applications (and their map rows), ingestion the campaign's resumes, in one transaction; then the stored PDFs. Same 204 / 404 responses. |

**Deletes and object storage (E0-20).** The rows go first, in one transaction; the PDFs only after it commits (`ingestion.service.delete_stored_files`). Object storage is not part of the transaction, so one failure mode is unavoidable, and this order picks the harmless one: a failed commit deletes nothing (deleting the PDFs first could lose the file of a row that survives), and a storage failure after the commit leaves a PDF that nothing refers to. That failure never fails the request (the delete has happened): it is logged (`stored_file_delete_failed`) and `python -m app.devtools.storage_orphans` reports such objects (with `--delete`, removes them; objects younger than `--min-age-minutes`, default 10, are skipped as possible uploads in flight).
| Campaign stats | Category counts and average scores over applications; top skills over the snapshots' skills (oldest application first). Resume counts from ingestion, unchanged. |
| Global stats | `total_candidates` counts applications. |

**Legacy-id decision.** A merged legacy id (`legacy_candidate_map`) answers `GET`, `PUT`, `PATCH`, bulk pipeline and `DELETE` with the surviving application, and the response carries the **application's id**, not the requested one: the client then holds the id the list shows. Tested in `tests/hiring/test_read_path.py` and `tests/ingestion/test_read_switch.py`.

**Visible differences on real data (expected).** A person's merged legacy candidates show as one row (the application, with the newest resume's data), so lists, exports and stats of their campaigns have fewer rows and `total_candidates` drops by the number of merged-away ids. `compare_api` reports these separately (runbook §8).

**Behaviour change: no placeholder rows (decided 2026-10-19, option A).** Before the switch the extract step created a placeholder candidate (ids and text only) before AI scoring, so a resume being analysed appeared in the list as an empty, unscored row ("Unknown" in the frontend's table), in the CSV export as an empty line, `GET /resumes/{id}/candidate` answered 200 with it, `total_candidates` counted it, and one that then failed (e.g. "not a resume") kept its placeholder for good. After the switch an application exists only once a resume is scored: such resumes have no row and no line, are not counted, and `GET /resumes/{id}/candidate` answers 404 `Candidate not yet processed` until then. The alternative, keeping placeholders as applications with a nullable `person_id`, was rejected: E1's Uploads tab shows in-progress resumes (upload progress already comes from SSE, `UploadZone`), and a nullable `person_id` would weaken the model (an application *is* a person's application) for a display quirk. The characterization tests record the new behaviour with a resume still being analysed instead of a fabricated unscored candidate; the 8 changed snapshots are listed in the E0-19 PR (`test_resume_being_analysed_has_no_candidate_yet` replaces `test_get_candidate_placeholder`).

## 8. Testing

| Layer | Scope | Approach |
|---|---|---|
| Characterization | Current responses of `/campaigns`, `/candidates` and upload | Written **first**, before any refactoring. Snapshot tests that must stay green throughout. |
| Unit | Normalisation, matching rules | Table-driven tests and Hypothesis property tests (normalising twice gives the same result; formatting variants normalise to the same value) |
| Integration | Repositories, services, routers | testcontainers `pgvector/pgvector:pg16`. Migrations run once per session; each test is rolled back. |
| Migration | Expand → seed → backfill → verify → contract; downgrade | Plus `alembic check` for drift between models and migrations |
| Concurrency | 20 parallel `resolve_person` calls for one identity | Asserts exactly one Person |
| AI | No network | `FakeAIService` with deterministic output, selected with `AI_PROVIDER=fake` |
| End-to-end smoke | Upload a PDF → Application exists | Against the Compose stack with `AI_PROVIDER=fake` |

Coverage gate: **≥ 80 % on `app/modules/`**. Enforced since E0-21 by the `Coverage gate` step of the `backend-test` CI job (`coverage report --include='app/modules/*' --fail-under=80`, also `make coverage`); the whole-app figure is reported, not gated.

## 9. Tooling, CI and developer experience

- **Python tooling:** `uv` with `pyproject.toml` and `uv.lock` (replacing `requirements.txt`), `ruff` (lint and format), `mypy` (strict for `app/modules/`), `import-linter` and `pre-commit`.
- **Database image:** `pgvector/pgvector:pg16` in Compose and in tests.
- **CI** (`.github/workflows/ci.yml`):
  - Triggers: push to `master` and every pull request. Older runs are cancelled when a newer push arrives.
  - Jobs:
    - `backend-quality`: ruff, mypy, import-linter
    - `backend-test`: pytest, then the coverage gate on `app/modules/` (§8)
    - `migrations`: upgrade to head on an empty database, plus `alembic check`
    - `frontend`: lint, type check, build
    - `compose`: validate the Compose config
  - Branch protection on `master` is configured manually by the owner, with steps documented in the README.
- **Developer setup fixes:**
  - MinIO image changed to `pgsty/minio`.
  - Host ports read from `.env` (`POSTGRES_HOST_PORT`, `REDIS_HOST_PORT`, `MINIO_API_HOST_PORT`, `MINIO_CONSOLE_HOST_PORT`, `BACKEND_HOST_PORT`, `FRONTEND_HOST_PORT`, `FLOWER_HOST_PORT`), with the usual ports as defaults.
  - Default `GEMINI_MODEL` changed to `gemini-2.5-flash`.
  - README: remove the `alembic revision --autogenerate` setup step and document `make setup`.
  - Delete the committed `celerybeat-schedule` file and add it to `.gitignore`.
  - `make setup` starts everything, runs migrations and loads seed data.

## 10. Definition of done for the epic

**Status (E0-21, 2026-10-19): all met.** Two items are re-confirmed on `master` once the Sprint 4 chain ([#29](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/29), [#30](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/30), [#31](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/31), E0-21) is merged: CI green on `master` with the coverage gate, and the fresh-clone setup.

- [x] All characterization tests pass against the restructured code.
  *Evidence:* the restructure ([#18](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/18), [#19](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/19), [#20](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/20)) left all 179 snapshots byte-identical ([#20](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/20): `git diff master -- backend/tests/characterization/__snapshots__` empty); the read switch and contract kept every response body and the OpenAPI document identical, with each intended change listed in its PR (8 placeholder-row snapshots in [#30](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/30), 3 storage values for B6 in [#31](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/31), the B3 snapshot in E0-21). Suite: [`backend/tests/characterization`](../../backend/tests/characterization/README.md), run by `backend-test` on every PR.
- [x] import-linter contracts pass, and no module imports another module's internals.
  *Evidence:* 8 contracts in `backend/pyproject.toml` ([#18](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/18) added 5, [#20](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/20) added 3; each shown to break on a deliberate violation), including "Modules import only each other's service and schemas"; `lint-imports` → 8 kept, 0 broken in [#31](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/31) and E0-21; run by `backend-quality`.
- [x] A backfill rehearsal runs on a live Compose stack with seed data. The upload pipeline keeps working during the backfill, verification passes and the expected counts match the manifest.
  *Evidence:* [#26](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/26) "Live rehearsal on the Compose stack": 1,125 seeded candidates backfilled in 40 s while 40 live uploads ran; `verify_backfill --manifest` PASSED with people 808/808, applications 1,101/1,101, conflicts 4/4, map rows 24/24, all integrity checks; a second run changed nothing. [Sprint 3 review](../sprints/sprint-03.md#sprint-review--retro), runbook §7.
- [x] The concurrency test passes reliably over 20 consecutive runs.
  *Evidence:* `backend/tests/talent/test_resolve_person_concurrency.py` (20 threads, one identity → exactly 1 Person; crossed email/phone → no deadlock): "Passed 20 runs in a row" in [#23](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/23); re-run on the E0-21 branch: 20 consecutive runs of the file (5 tests each), all passed.
- [x] The contract migration has dropped `candidates`, and the app still works.
  *Evidence:* [#31](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/31) "Live contract check on the Compose stack": `alembic upgrade` to `e20c4a7d1b93`, `candidates` and `resumes_campaign_id_fkey` gone, `verify_people_applications` integrity checks PASS, `compare_api` 2,468/2,468 identical, campaign delete with PDFs and a new upload work. Tests: `tests/migrations/test_contract_e0_20.py`, `tests/test_no_cross_module_foreign_keys.py`.
- [x] CI is green on `master` with all five jobs, and coverage is ≥ 80 % on `app/modules/`.
  *Evidence:* all five jobs (`backend-quality`, `backend-test`, `migrations`, `frontend`, `compose`) green on `master` through [#27](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/27) (the last merge) and required by branch protection since Sprint 1; the gate itself is E0-21: a `Coverage gate` step in `backend-test` fails below 80 % on `app/modules/` (97 % at E0-21, whole app 94 %), shown to fail with the threshold at 99. *Confirmed on `master` after merge.*
- [x] A fresh clone becomes a working stack with `cp .env.example .env && make setup`.
  *Evidence:* [#2](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/2) (no workarounds: `--wait`, migrations, healthchecks) and [#17](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/17) (`make setup` seeds with `--if-empty`, safe to re-run); [Sprint 1 demo](../sprints/sprint-01.md#sprint-review--retro). Since then every migration runs from an empty database in the `migrations` job and the Compose config is validated by `compose`. *Re-run from a fresh clone on `master` after merge* (E0-20 changed how the seed creates People and Applications, covered by its tests, not the setup steps).
- [x] ADRs are written for: Person/Application split, no cross-module foreign keys, and the matching policy.
  *Evidence:* [0002](../adr/0002-person-application-split.md), [0003](../adr/0003-no-cross-module-foreign-keys.md), [0004](../adr/0004-identity-matching-policy.md) (with the phone amendment from [#23](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/23)), all accepted ([#4](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/4), [#10](https://github.com/pavanraut131/AI-Recruitment-Platform/pull/10)); also [0001](../adr/0001-modular-monolith.md) and [0005](../adr/0005-composition-layer.md).

## 11. Risks

| Risk | Mitigation |
|---|---|
| Restructure silently changes API behaviour | Characterization tests are written first and kept green throughout |
| Wrong automatic merge exposes one person's data to another | Exact matching on normalised identifiers only; conflicts are recorded, never merged |
| Dual-write code drifts from the backfill logic | Both call the same `resolve_person` and `upsert_application` services |
| Epic grows too large | No frontend, tenancy or AI-gateway work in E0. Anything new goes into the next epic's backlog. |
