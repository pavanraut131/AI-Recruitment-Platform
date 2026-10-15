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
| score, score_breakdown, category, summary, strengths, weaknesses, missing_skills, recommendation | as in `candidates` today | |
| pipeline_stage, notes | as in `candidates` today | |
| created_at, updated_at | timestamptz | |
| | | **UNIQUE(campaign_id, person_id)** |

**`legacy_candidate_map`**: candidate_id (PK) → application_id (FK → applications, on delete cascade; candidate_id has no FK so the map outlives `candidates`). This redirects the IDs of legacy candidates that were merged into another application.

### ingestion

**`resumes`** (existing table): add three columns:

- `person_id UUID null`
- `extracted JSONB null`: a snapshot of the AI extraction for this file
- `processing_step varchar(20) null`: one of `extract_text`, `extract_profile`, `resolve_person`, `score`, `save`. It records the current step while processing and the failed step after a failure.

The **status values stay the same** (`pending | extracting | processing | done | failed | quota_exceeded`) because the frontend depends on them (`UploadZone.tsx`). Step-level detail goes in `processing_step` instead.

**`backfill_checkpoints`** (new): name (PK), last_created_at, last_id, updated_at. One row per batch job: the keyset position of the last committed batch (§6.2).

### Cross-module references

References between modules are stored as plain UUIDs **without** foreign key constraints. This keeps each module's tables independent, so a module could be split out later. Data consistency between modules is guaranteed by the service layer and checked by the verification script (§6.3).

## 5. Upload pipeline and person matching

### 5.1 Pipeline

1. **Upload** (`ingestion.router`): validate the file, hash it, apply the existing check for the same file already in this campaign, store it in MinIO, insert a `resumes` row and enqueue `extract_text`.
2. **`extract_text`** (extract_queue): convert the PDF to text, then enqueue `analyse_resume`.
3. **`analyse_resume`** (ai_queue). Each step is safe to retry:
   1. `ai.extract_profile(text)` → store `resumes.extracted`. Skipped if already present.
   2. `talent.resolve_person(identifiers, profile, resume_id)` → `person_id`. Skipped if `resumes.person_id` is already set.
   3. `ai.score(profile, campaign_requirements)` → ScoringResult.
   4. `hiring.upsert_application(campaign_id, person_id, resume_id, scoring)`, an upsert on `(campaign_id, person_id)`.
4. **Progress events** keep their current shape, with `person_id` and `matched_existing: bool` added.

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
- A Gemini quota error (`ResourceExhausted`) sets `status = quota_exceeded` **immediately** and publishes an SSE event, so the UI's existing "Quota Limit" state appears. The retry is scheduled using the retry delay Google provides. Today the worker can sit retrying for minutes before the status changes.

## 6. Migration plan (expand → backfill → contract)

### 6.1 Phases

| Phase | Deliverable | Runtime behaviour |
|---|---|---|
| **1 · Expand** | Alembic migration that creates the new tables and the new `resumes` columns. Nothing is dropped. | The worker **dual-writes**: it writes the legacy `candidates` row and the new tables in one transaction. |
| **2 · Backfill** | `python -m app.modules.ingestion.backfill`: batched, resumable and idempotent | Runs while the app is live |
| **3 · Verify** | `python -m app.modules.ingestion.verify_backfill`: exits with a non-zero code on any failure | Must pass before Phase 4 |

The backfill and verification live in `ingestion` because they coordinate across modules. They read legacy candidates through `hiring.service.iter_legacy_candidates()`, resolve people through `talent.service`, upsert applications through `hiring.service`, and write `resumes` themselves. No module boundary is crossed.
| **4 · Switch** | Compatibility endpoints read from `applications` and `people`. Writes to `candidates` stop. | Frontend unchanged |
| **5 · Contract** | Migration that drops `candidates`, after one full sprint without issues | — |

### 6.2 Backfill algorithm

- Iterate over `candidates` ordered by `(created_at, id)` in batches of 500, using **keyset pagination**: each batch starts after the last `(created_at, id)` processed.
- Save a checkpoint (`last_created_at`, `last_id`) in a `backfill_checkpoints` table after each batch.
- For each candidate:
  1. Resolve the Person using the same `resolve_person` code as the live pipeline.
  2. `INSERT … ON CONFLICT (campaign_id, person_id) DO UPDATE` the Application.
     - The first candidate for a pair keeps its own ID.
     - When a later candidate for the same pair arrives, the application takes the newer resume's scoring, and the older ID goes into `legacy_candidate_map`.
  3. Set `resumes.person_id` and `resumes.extracted`. The extraction is rebuilt from the candidate's stored fields.
- Running the backfill again after it finishes changes nothing.

### 6.3 Verification checks

- Every `candidates.id` either is an `applications.id` or appears in `legacy_candidate_map`.
- Every application's `person_id` exists in `people`, and its `resume_id` exists in `resumes`.
- Every resume with status `done` has a `person_id`.
- No person has zero identifiers unless all of their source resumes lacked identifiers.
- The counts of people and applications are reported and checked against the expected seed results.

### 6.4 Seed data generator

`python -m app.devtools.seed --people 800 --campaigns 15` uses Faker to produce about 1,000 legacy candidates. The data deliberately includes:

- Emails differing only in letter case.
- Gmail dot and `+tag` variants.
- The same phone written in different formats (`+91 98765 43210`, `09876543210`, `98765-43210`).
- People applying to several campaigns.
- The same person applying twice to one campaign.
- An email/phone conflict between two different people.
- Candidates with no contact details.

The generator also writes a manifest of the expected number of People, Applications and conflicts, which tests and the verification script assert against.

## 7. API compatibility layer

- The existing `/api/v1/candidates*` and `/api/v1/campaigns/{id}/candidates` endpoints keep their **exact response shapes**. They are rebuilt by joining Application with Person.
- A request with a merged legacy ID is resolved through `legacy_candidate_map` and returns the surviving application.
- Characterization tests (§8) prove the response shapes are unchanged.
- No new public endpoints in E0. The new resource-oriented API is designed in E1 alongside the generated client.

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

Coverage gate: **≥ 80 % on `app/modules/`**.

## 9. Tooling, CI and developer experience

- **Python tooling:** `uv` with `pyproject.toml` and `uv.lock` (replacing `requirements.txt`), `ruff` (lint and format), `mypy` (strict for `app/modules/`), `import-linter` and `pre-commit`.
- **Database image:** `pgvector/pgvector:pg16` in Compose and in tests.
- **CI** (`.github/workflows/ci.yml`):
  - Triggers: push to `master` and every pull request. Older runs are cancelled when a newer push arrives.
  - Jobs:
    - `backend-quality`: ruff, mypy, import-linter
    - `backend-test`: pytest with coverage gate
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

- [ ] All characterization tests pass against the restructured code.
- [ ] import-linter contracts pass, and no module imports another module's internals.
- [ ] A backfill rehearsal runs on a live Compose stack with seed data. The upload pipeline keeps working during the backfill, verification passes and the expected counts match the manifest.
- [ ] The concurrency test passes reliably over 20 consecutive runs.
- [ ] The contract migration has dropped `candidates`, and the app still works.
- [ ] CI is green on `master` with all five jobs, and coverage is ≥ 80 % on `app/modules/`.
- [ ] A fresh clone becomes a working stack with `cp .env.example .env && make setup`.
- [ ] ADRs are written for: Person/Application split, no cross-module foreign keys, and the matching policy.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Restructure silently changes API behaviour | Characterization tests are written first and kept green throughout |
| Wrong automatic merge exposes one person's data to another | Exact matching on normalised identifiers only; conflicts are recorded, never merged |
| Dual-write code drifts from the backfill logic | Both call the same `resolve_person` and `upsert_application` services |
| Epic grows too large | No frontend, tenancy or AI-gateway work in E0. Anything new goes into the next epic's backlog. |
