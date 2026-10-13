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
	$(BACKEND_RUN) mypy app workers

test:
	$(BACKEND_RUN) pytest -v

# ── Frontend ─────────────────────────────────────────────────
frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

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
	@echo ""
	@echo "✅ ResumeAI is ready! (ports below are the defaults; see .env)"
	@echo "   Frontend:  http://localhost:3000"
	@echo "   API Docs:  http://localhost:8000/docs"
	@echo "   Flower:    http://localhost:5555"
	@echo "   MinIO:     http://localhost:9001"
