.PHONY: up down build logs migrate shell-backend shell-frontend test lint setup

# ── Docker Compose ───────────────────────────────────────────
up:
	docker compose up --build -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

restart:
	docker compose restart

# ── Database ─────────────────────────────────────────────────
migrate-gen:
	docker compose exec backend alembic revision --autogenerate -m "$(msg)"

migrate-up:
	docker compose exec backend alembic upgrade head

migrate-down:
	docker compose exec backend alembic downgrade -1

# ── Seed data (dev only; refuses when APP_ENV=production) ────
# Fake campaigns and processed resumes, run through the pipeline's matching and scoring
# services (People + Applications), plus backend/seed-manifest.json (expected results; check
# with `make verify`). Refuses if applications exist; `make seed SEED_FLAGS=--reset` deletes
# campaigns, resumes, applications and people first. Override sizes with e.g.
# `make seed SEED_ARGS="--people 100 --campaigns 4"`.
.PHONY: seed
SEED_ARGS ?= --people 800 --campaigns 15 --seed 42
SEED_FLAGS ?=
seed:
	docker compose exec -T backend python -m app.devtools.seed $(SEED_ARGS) $(SEED_FLAGS)

# Cross-module consistency of People, Applications and resumes (spec §6.3), against the seed
# manifest; and stored PDFs that no resume refers to any more (add `--delete` to remove them).
.PHONY: verify storage-orphans
verify:
	docker compose exec -T backend python -m app.modules.ingestion.verify_people_applications --manifest /app/seed-manifest.json

storage-orphans:
	docker compose exec -T backend python -m app.devtools.storage_orphans

# ── Shell access ─────────────────────────────────────────────
shell-backend:
	docker compose exec backend bash

shell-db:
	docker compose exec postgres psql -U resumeai -d resumeai

# ── Backend quality & tests (run on the host via uv) ─────────
# `uv run` syncs backend/.venv from uv.lock (incl. the dev group) before each command.
# Override the uv command if it isn't on PATH, e.g. `make lint UV="python -m uv"`.
.PHONY: lint format typecheck test
UV ?= uv
BACKEND_RUN = $(UV) run --directory backend

lint:
	$(BACKEND_RUN) ruff check .
	$(BACKEND_RUN) ruff format --check .
	$(BACKEND_RUN) lint-imports

format:
	$(BACKEND_RUN) ruff check --fix .
	$(BACKEND_RUN) ruff format .

typecheck:
	$(BACKEND_RUN) mypy app

test:
	$(BACKEND_RUN) pytest -v

# The full suite, then CI's coverage gate: app/modules/ must stay >= COVERAGE_MIN % (the whole
# app's coverage is printed by pytest; only the modules are gated, spec EPIC-00 §8).
.PHONY: coverage
COVERAGE_MIN ?= 80
coverage:
	$(BACKEND_RUN) pytest
	$(BACKEND_RUN) coverage report --include='app/modules/*' --skip-covered --fail-under=$(COVERAGE_MIN)

# ── Frontend ─────────────────────────────────────────────────
# The generated /api/v2 client (E1-03, ADR 0006): export the v2 OpenAPI document without a
# server (no database, Redis or AI key) and regenerate the TypeScript types from it. Commit
# both files; CI runs the same two steps and fails if they differ from what is committed.
.PHONY: api-client
api-client:
	$(BACKEND_RUN) python -m app.devtools.export_openapi
	cd frontend && npm run api:generate

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

# ── End-to-end tests (Playwright + axe, EPIC-01 §9) ─────────
# An isolated Compose project (hireflow-e2e: its own containers, volumes and ports 13000/18000,
# configured from .env.example with AI_PROVIDER=fake) next to the dev stack, never touching it.
# `make e2e` starts it, migrates, seeds, runs the suite and removes it; E2E_KEEP=1 keeps it up
# (then `make e2e-test` to re-run, `make e2e-down` to remove). Playwright args: E2E_ARGS="--headed".
# Needs Node and Chromium on the host once: cd frontend && npm ci && npx playwright install chromium
.PHONY: e2e e2e-up e2e-test e2e-down
E2E_ARGS ?=
e2e:
	bash scripts/e2e.sh run $(E2E_ARGS)

e2e-up:
	bash scripts/e2e.sh up

e2e-test:
	bash scripts/e2e.sh test $(E2E_ARGS)

e2e-down:
	bash scripts/e2e.sh down

# ── Cleanup ──────────────────────────────────────────────────
clean:
	docker compose down -v --remove-orphans
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

# ── Quick start ──────────────────────────────────────────────
# `--wait` blocks until every service with a healthcheck is healthy
# (and the rest are running), failing if any becomes unhealthy.
setup:
	docker compose up --build -d --wait
	$(MAKE) migrate-up
	$(MAKE) seed SEED_FLAGS=--if-empty
	@echo ""
	@echo "✅ HireFlow AI is ready! (ports below are the defaults; see .env)"
	@echo "   Frontend:  http://localhost:3000"
	@echo "   API Docs:  http://localhost:8000/docs"
	@echo "   Flower:    http://localhost:5555"
	@echo "   MinIO:     http://localhost:9001"
