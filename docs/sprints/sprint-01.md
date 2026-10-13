# Sprint 1 · Safety net & working CI

**Epic:** [E0 · Platform core](../specs/EPIC-00-platform-core.md)  ·  **Dates:** 2026-10-14 → 2026-10-20  ·  **Status:** planned

## Sprint goal

> Before changing any structure, lock in today's API behaviour with tests that run against real Postgres, and get CI running on `master`.

This sprint changes **no product behaviour**. Everything here is groundwork that makes the E0 restructure in Sprints 2–4 safe.

## Board

| Ticket | Title | Size | Status |
|---|---|---|---|
| E0-01 | Fix the local dev environment | S | todo |
| E0-02 | Python tooling: uv, ruff, mypy, pre-commit | M | todo |
| E0-03 | Postgres test harness with testcontainers | M | todo |
| E0-04 | Characterization tests for the current API | L | todo |
| E0-05 | Rewrite CI | M | todo |
| E0-06 | ADRs for E0's key decisions | S | todo |
| E0-07 | PR template and branch protection guide | S | todo |

Suggested order: **01 → 02 → 03 → 04 → 05**, with 06 and 07 fitted in anywhere. 04 depends on 03, and 05 depends on 02 and 03.

---

### E0-01 · Fix the local dev environment
**Status:** todo  ·  **Size:** S  ·  **Branch:** `e0-01-dev-env-fixes`

**Goal:** A fresh clone runs with `cp .env.example .env && make setup`, with no workarounds. This removes the three problems we hit on day one.

**Acceptance criteria**
- [ ] `docker-compose.yml` uses `pgsty/minio` instead of the removed `minio/minio` image.
- [ ] Every published host port is configurable through `.env`, with the usual port as default: `POSTGRES_HOST_PORT`, `REDIS_HOST_PORT`, `MINIO_API_HOST_PORT`, `MINIO_CONSOLE_HOST_PORT`, `BACKEND_HOST_PORT`, `FRONTEND_HOST_PORT`, `FLOWER_HOST_PORT`. All of them are documented in `.env.example`.
- [ ] The Postgres image is `pgvector/pgvector:pg16`.
- [ ] The default `GEMINI_MODEL` in `config.py` and `.env.example` is `gemini-2.5-flash`.
- [ ] `backend/celerybeat-schedule` is removed from git and added to `.gitignore`.
- [ ] `make setup` starts the stack, waits until it is healthy (polling, not `sleep 15`), and runs `alembic upgrade head`. The README's `alembic revision --autogenerate` step is removed.
- [ ] Verified on this machine using non-default ports, without the scratch override file.

**Concept to learn:** Compose variable substitution, healthcheck-driven startup ordering.

**Notes:** Switching Postgres 15 → 16 means running `docker compose down -v` once to delete the local volume. The data is throwaway, so say so in the PR.

---

### E0-02 · Python tooling: uv, ruff, mypy, pre-commit
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e0-02-python-tooling`

**Goal:** One reproducible way to install, lint, format and type-check the backend, enforced the same way locally and in CI.

**Acceptance criteria**
- [ ] `backend/pyproject.toml` and `uv.lock` replace `requirements.txt` and `tests/requirements-test.txt`. Dev dependencies are in a `dev` dependency group.
- [ ] The Dockerfile installs dependencies with `uv sync --frozen`, and the image still builds and runs.
- [ ] Ruff is configured (rules: `E, F, I, B, UP, SIM, ASYNC`) and the whole codebase is formatted with it. **The formatting is its own commit**, so `git blame` can skip it (add the commit to `.git-blame-ignore-revs`).
- [ ] mypy runs in **baseline mode**: it passes on the current code using per-module `ignore_errors` where needed. Strict mode for `app/modules/` is switched on in Sprint 2 when that folder exists.
- [ ] `.pre-commit-config.yaml` runs ruff (lint and format), mypy and basic checks (trailing whitespace, large files, merge conflicts).
- [ ] Makefile targets: `lint`, `format`, `typecheck`, `test`.

**Concept to learn:** Lockfiles and reproducible builds; introducing linters into an existing codebase without a huge diff (baseline first, then tighten).

---

### E0-03 · Postgres test harness with testcontainers
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e0-03-postgres-test-harness`

**Goal:** Tests run against real Postgres, matching production, instead of SQLite. SQLite cannot run the `JSONB`/`UUID` models, so the current tests cannot pass.

**Acceptance criteria**
- [ ] A session-scoped fixture starts `pgvector/pgvector:pg16` with testcontainers and runs `alembic upgrade head` once. **This also proves the migrations apply cleanly from empty.**
- [ ] A per-test fixture wraps each test in a transaction (using savepoints) and rolls it back, so tests are isolated and fast.
- [ ] The FastAPI `get_db` dependency is overridden to use the test session. The `httpx.AsyncClient` fixture uses `ASGITransport`.
- [ ] Celery dispatch is stubbed in tests (`apply_async` / `delay` are recorded, not sent), and MinIO calls go to an in-memory fake. Tests need only Docker.
- [ ] The existing `tests/test_campaigns.py` runs and passes on the new harness. The SQLite code and `aiosqlite` are removed.
- [ ] `pytest-cov` reports coverage. No gate yet; the gate arrives with `app/modules/` in Sprint 2.
- [ ] A fresh run of the full suite takes under 60 seconds locally, including container start.

**Concept to learn:** testcontainers, transactional test isolation with savepoints, testing against the real database.

---

### E0-04 · Characterization tests for the current API
**Status:** todo  ·  **Size:** L  ·  **Branch:** `e0-04-characterization-tests`

**Goal:** Record exactly what every endpoint returns **today**, so the restructure and the Person/Application switch can be proven not to change the API.

**Acceptance criteria**
- [ ] Snapshot tests (using `syrupy`) cover every current endpoint, including error cases (404, validation 422, invalid upload).
  - **campaigns:** `POST /campaigns`, `GET /campaigns`, `GET /campaigns/{id}`, `PUT /campaigns/{id}`, `DELETE /campaigns/{id}`, `GET /campaigns/{id}/stats`, `POST /campaigns/parse-jd`
  - **candidates:** `GET /campaigns/{id}/candidates` (with each filter and sort option), `GET /campaigns/{id}/candidates/export` (CSV), `GET /candidates/{id}`, `PUT /candidates/{id}/category`, `PATCH /candidates/{id}/notes`, `PATCH /candidates/{id}/pipeline`, `GET /resumes/{id}/candidate`, `PATCH /campaigns/{id}/candidates/bulk-pipeline`, `DELETE /campaigns/{id}/candidates/bulk`, `DELETE /candidates/{id}`
  - **resumes:** `POST /campaigns/{id}/upload` (accepted, rejected because not a PDF, too large, too many files, duplicate), `GET /campaigns/{id}/resumes`, `GET /resumes/{id}`, `POST /campaigns/{id}/resumes/retry-failed`
  - **SSE:** `GET /campaigns/{id}/stream`, with the event payload shape for each resume status
  - **stats and health:** `GET /stats`, `GET /health`, `GET /api/v1/health`
- [ ] Values that change between runs (UUIDs, timestamps) are masked by a shared serializer, so snapshots stay stable.
- [ ] Test data is built with small factory helpers (campaign, resume, candidate), not raw SQL scattered across tests.
- [ ] `parse-jd` and any other endpoint that calls Gemini have the AI call stubbed. **No network calls in tests.**
- [ ] A README note in `tests/characterization/` explains that these snapshots define the API contract and must not be updated casually. Any snapshot change in a PR must be explained in that PR.

**Concept to learn:** Characterization (golden-master) testing; using snapshot tests to define an API contract.

**Notes:** If you find a bug while writing these, **record the buggy behaviour as it is** and log the bug in the Sprint 2 backlog. Characterization tests describe current behaviour, not intended behaviour.

---

### E0-05 · Rewrite CI
**Status:** todo  ·  **Size:** M  ·  **Branch:** `e0-05-ci-rewrite`

**Goal:** CI actually runs (today it watches `main`/`develop`, which don't exist) and gates every PR.

**Acceptance criteria**
- [ ] Triggers: `push` to `master` and every `pull_request`. A `concurrency` group cancels outdated runs.
- [ ] Jobs:
  - `backend-quality`: ruff check, ruff format --check, mypy
  - `backend-test`: pytest with testcontainers and a coverage report uploaded as an artifact
  - `migrations`: start a Postgres service, run `alembic upgrade head` then `alembic check`
  - `frontend`: npm ci, lint, `tsc --noEmit`, `next build`
  - `compose`: `docker compose config --quiet`, with a CI `.env` generated from `.env.example`
- [ ] uv and npm caches are enabled.
- [ ] All five jobs are green on the PR and on `master` after merging.
- [ ] If `alembic check` finds drift between the models and the migrations, fix it with a new migration in this ticket and explain the drift in the PR.

**Concept to learn:** GitHub Actions job design, service containers, caching, concurrency groups.

**Notes:** The `import-linter` step is added in Sprint 2, when there are module boundaries to enforce.

---

### E0-06 · ADRs for E0's key decisions
**Status:** todo  ·  **Size:** S  ·  **Branch:** `e0-06-adrs`

**Goal:** Record the reasoning behind E0's main decisions while it's fresh.

**Acceptance criteria**
- [ ] `docs/adr/0002-person-application-split.md`
- [ ] `docs/adr/0003-no-cross-module-foreign-keys.md`
- [ ] `docs/adr/0004-identity-matching-policy.md`, covering exact matching on normalised email or phone, recording conflicts without merging them, and the review queue deferred to E6
- [ ] Each ADR follows the format in [process.md](../process.md) and lists the alternatives we rejected.

**Concept to learn:** Writing ADRs: explaining trade-offs concisely for a future reader.

---

### E0-07 · PR template and branch protection guide
**Status:** todo  ·  **Size:** S  ·  **Branch:** `e0-07-pr-template`

**Goal:** Every PR follows the definition of done, and `master` can't be broken by a direct push.

**Acceptance criteria**
- [ ] `.github/pull_request_template.md` with sections for: ticket ID, what changed and why, how it was tested, the definition-of-done checklist, and snapshot changes (if any).
- [ ] The README has a "Contributing" section listing the steps to enable branch protection on `master`: require PRs and require the five CI checks.
- [ ] Branch protection is enabled on GitHub by the repo owner after E0-05 merges.

**Concept to learn:** Repository governance, even for a solo project.

---

## Sprint 2 (rough backlog, to be refined at the end of Sprint 1)

- **E0-08:** Create `app/platform/` and the empty `app/modules/{talent,hiring,ingestion,ai}` skeleton. Add import-linter contracts to CI and turn on mypy strict for `modules/`.
- **E0-09:** Move campaigns, candidates, stats and JD parsing into `hiring`. Characterization tests stay green.
- **E0-10:** Move upload, resumes, SSE and the workers into `ingestion`, and Gemini into `ai`. Add `FakeAIService` and the `AI_PROVIDER` setting.
- **E0-11:** `talent/identity.py`: email and phone normalisation with table-driven and Hypothesis property tests.
- **E0-12:** `devtools/seed.py`: a Faker seed generator with deliberate duplicates and an expected-results manifest.
- Plus any bugs found during E0-04.

**Later in E0 (Sprints 3–4):** expand migration and dual writes, `resolve_person` with advisory locks and the concurrency test, backfill and verification, switching the API to the new tables, the contract migration, the live-stack rehearsal, and the coverage gate.

## Sprint review & retro

*(To be filled in at the end of the sprint.)*

- **Demo:**
- **Keep:**
- **Drop:**
- **Try:**
