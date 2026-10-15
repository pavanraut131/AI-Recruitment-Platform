# Sprint 2 · Modules in place

**Epic:** [E0 · Platform core](../specs/EPIC-00-platform-core.md)  ·  **Dates:** 2026-10-14 → 2026-10-20  ·  **Status:** planned

## Sprint goal

> Move the backend into the modular-monolith layout with enforced boundaries, and get the two features that never worked actually working. The API contract stays unchanged apart from deliberate, explained bug-fix snapshot changes.

The 170 characterization snapshots from Sprint 1 are the safety net. A ticket that only moves code must not change a single snapshot.

## Board

| Ticket | Title | Size | Status |
|---|---|---|---|
| B1 | Fix `POST /campaigns/parse-jd` (always 500) | S | todo |
| B2 | Fix the candidate `skill` filter (always 500) | S | todo |
| E0-08 | Module skeleton, `platform/` kernel and enforced boundaries | M | todo |
| E0-09 | Move campaigns and candidates into `hiring` | L | todo |
| E0-10 | Move upload/SSE/workers into `ingestion` and Gemini into `ai` | L | todo |
| E0-11 | Identity normalisation (`talent/identity.py`) | M | todo |
| E0-12 | Seed data generator with expected-results manifest | M | todo |

**Order and parallelism:**

- **Wave 1:** B1, B2, E0-08, E0-11 and E0-12. These touch different files.
  - B1 and B2 land before the move, so each fix is a small, reviewable snapshot change.
  - E0-11 and E0-12 are new code in new folders.
- **Wave 2:** E0-09, which needs E0-08.
- **Wave 3:** E0-10, which needs E0-09. Both edit `main.py` and the router wiring.

---

### B1 · Fix `POST /campaigns/parse-jd` (always 500)
**Status:** todo  ·  **Size:** S  ·  **Branch:** `b1-fix-parse-jd`

**Goal:** JD parsing works. Today the prompt template's literal JSON braces make `PROMPT.format()` raise `KeyError`, so Gemini is never called.

**Acceptance criteria**
- [ ] The prompt is built without `str.format` on a template containing literal braces (escape them, or use `string.Template` / concatenation).
- [ ] A unit test renders the prompt for a JD that itself contains `{` and `}`.
- [ ] The characterization snapshots for parse-jd are updated. The PR explains each changed snapshot (500 → 200 with parsed fields, plus the error cases).
- [ ] A test covers a malformed Gemini response (invalid JSON), with the error behaviour recorded.

**Concept to learn:** Prompt templating pitfalls; treating a bug fix as an explicit contract change.

---

### B2 · Fix the candidate `skill` filter (always 500)
**Status:** todo  ·  **Size:** S  ·  **Branch:** `b2-fix-skill-filter`

**Goal:** `GET /campaigns/{id}/candidates?skill=python` returns candidates whose skills contain the term, case-insensitively.

**Acceptance criteria**
- [ ] The filter uses a correct JSONB expression. Either:
  - an `EXISTS` over `jsonb_array_elements_text(skills)` with `ILIKE`, or
  - `skills::text ILIKE`, with the trade-off documented.
- [ ] The search term's LIKE wildcards (`%`, `_`) are escaped. This also fixes the `search` part of B8 if it shares the helper.
- [ ] The snapshots for the skill filter are updated, and the PR explains them.
- [ ] Tests cover: match, no match, mixed case, a skill containing `_`, and combination with the other filters.

**Concept to learn:** Querying JSONB arrays in Postgres; escaping LIKE patterns.

---

### E0-08 · Module skeleton, `platform/` kernel and enforced boundaries
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e0-08-module-skeleton`

**Goal:** The target layout from spec §3 exists, and CI fails if any module reaches into another module's internals.

**Acceptance criteria**
- [ ] `app/core/*` moves to `app/platform/` (settings, database, logging, storage). `workers/celery_app.py` moves to `app/platform/celery_app.py`.
  - Old import paths keep working through thin re-export shims, each marked with a removal TODO pointing at E0-10, so this ticket stays small.
- [ ] Create `app/modules/{talent,hiring,ingestion,ai}/__init__.py` and `app/devtools/__init__.py`, each with a docstring stating the module's responsibility.
- [ ] `import-linter` is configured in `pyproject.toml` with the contracts from spec §3:
  - layers `ingestion → hiring → talent`
  - `ai` and `talent` independent
  - `platform` must not import `modules`
  - nothing imports `devtools`
  - modules may import only each other's `service` and `schemas`
- [ ] `lint-imports` runs in the `backend-quality` CI job and in pre-commit.
- [ ] mypy runs in `strict` mode for `app.modules.*` and `app.platform.*`.
- [ ] All 170 snapshots are unchanged.

**Concept to learn:** Enforcing architecture with tooling (import-linter contracts); the strangler pattern with shims.

---

### E0-09 · Move campaigns and candidates into `hiring`
**Status:** todo  ·  **Size:** L  ·  **Branch:** `e0-09-hiring-module`

**Goal:** Everything about campaigns, candidates, stats and JD parsing lives in `app/modules/hiring/`, behind a `service.py` interface.

**Acceptance criteria**
- [ ] The `Campaign` and `Candidate` models, schemas and routers (campaigns, candidates, stats, parse-jd) move into `hiring/` as `models.py`, `schemas.py`, `router.py`, `service.py` and `repository.py`.
- [ ] Routers are thin: request parsing and responses only. Queries move to `repository.py`, and business rules to `service.py`.
- [ ] The candidate list/export filter logic is shared between list and export. Keep the current behaviour, including the B8 export bug; fixing it is separate.
- [ ] Alembic still sees every model (`alembic check` is green in CI).
- [ ] import-linter contracts pass, and all snapshots are unchanged (except those already changed by B1/B2).
- [ ] mypy strict passes for `app.modules.hiring`.

**Concept to learn:** Separating router, service and repository; refactoring safely behind characterization tests.

---

### E0-10 · Move upload/SSE/workers into `ingestion` and Gemini into `ai`
**Status:** todo  ·  **Size:** L  ·  **Branch:** `e0-10-ingestion-ai-modules`

**Goal:** The upload pipeline lives in `ingestion`, all LLM access goes through an `ai` service interface, and tests and dev can run with a fake AI provider.

**Acceptance criteria**
- [ ] The `Resume` model, the upload/resumes/SSE router, PDF extraction and the Celery tasks move into `app/modules/ingestion/`. Celery task names stay the same, or both old and new names are registered for one release, so queued tasks don't break.
- [ ] `app/modules/ai/service.py` defines an `AIService` protocol (`extract_profile`, `score`, `parse_job_description`), plus:
  - a `GeminiAIService` implementation
  - a `FakeAIService` with deterministic output
  - a factory selected by a new `AI_PROVIDER` setting (`gemini` | `fake`, default `gemini`)
- [ ] `ingestion` and `hiring` call only `ai.service`. Nothing outside `ai` imports `google.generativeai`, and import-linter enforces this.
- [ ] The `FakeGemini` test fixture is replaced by `AI_PROVIDER=fake` where possible.
- [ ] All re-export shims from E0-08 are removed.
- [ ] All snapshots are unchanged, import-linter passes, and mypy strict passes for the touched modules.
- [ ] An end-to-end smoke check: with `AI_PROVIDER=fake`, run the Compose stack, upload a PDF, and confirm a candidate is created.

**Concept to learn:** Ports and adapters (hexagonal architecture) for LLM providers; renaming Celery tasks safely. This prepares for the E3 AI gateway.

---

### E0-11 · Identity normalisation (`talent/identity.py`)
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e0-11-identity-normalisation`

**Goal:** Pure, heavily tested functions that normalise emails and phone numbers for person matching (spec §5.2, [ADR 0004](../adr/0004-identity-matching-policy.md)).

**Acceptance criteria**
- [ ] `normalize_email(raw) -> str | None` and `normalize_phone(raw, default_region) -> str | None` implement exactly the spec §5.2 rules:
  - Gmail/Googlemail: remove dots and `+tag`, and use `gmail.com`.
  - The `+tag` allowlist providers: remove `+tag` only.
  - Other domains: untouched.
  - Phones: E.164 via `phonenumbers`.
  - Invalid input returns `None`.
- [ ] A new `PHONE_DEFAULT_REGION` setting, default `IN`.
- [ ] Table-driven tests with at least 30 cases, including the tricky ones listed in spec §6.4.
- [ ] Hypothesis property tests:
  - Idempotence: normalising twice gives the same result as once.
  - Formatting variants of the same number normalise to the same value.
  - Non-Gmail local parts keep their dots.
- [ ] mypy strict, 100 % branch coverage for `identity.py`.

**Concept to learn:** Entity-resolution normalisation; property-based testing with Hypothesis.

---

### E0-12 · Seed data generator with expected-results manifest
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e0-12-seed-generator`

**Goal:** Realistic local data and the fixture that proves the Sprint 3 backfill is correct (spec §6.4).

**Acceptance criteria**
- [ ] `python -m app.devtools.seed --people 800 --campaigns 15 --seed 42` uses Faker to create campaigns, resumes and **legacy candidates** in the current schema.
  - It is deterministic for a given `--seed`.
  - It refuses to run when `APP_ENV=production`.
- [ ] The data deliberately includes every case in spec §6.4:
  - emails differing only in case
  - Gmail dot and `+tag` variants
  - phone format variants
  - people applying to several campaigns
  - the same person applying twice to one campaign
  - an email/phone conflict between two people
  - candidates with no contact details
- [ ] It writes `seed-manifest.json` with the expected number of People, Applications and conflicts, computed from what the generator intended (not by running matching code).
- [ ] `make seed` runs it, and `make setup` runs it after migrations. Both are documented in the README.
- [ ] A test runs the generator against the test database and checks the row counts and the manifest's internal consistency.

**Concept to learn:** Designing test data that encodes the edge cases; deterministic generators.

---

## Backlog (not in this sprint)

- B3–B10 from [Sprint 1](sprint-01.md#bugs-found-by-e0-04-recorded-as-is-in-the-characterization-snapshots-full-list-in-pr-9). Fix them after E0-10, when the code sits in its final place.
  - Exception: **B4** (upgrade python-multipart, possible DoS) is a candidate to pull in early if Sprint 2 has room.
- Upgrade Node 20 (end of life) to 22/24 in `frontend/Dockerfile` and CI together.
- **Sprint 3:** expand migration and dual writes; `resolve_person` with advisory locks and the concurrency test; backfill and verification.
- **Sprint 4:** switch the API to the new tables; contract migration; live-stack rehearsal; coverage gate ≥ 80 % on `app/modules/`.

## Sprint review & retro

*(To be filled in at the end of the sprint.)*

- **Demo:**
- **Keep:**
- **Drop:**
- **Try:**
