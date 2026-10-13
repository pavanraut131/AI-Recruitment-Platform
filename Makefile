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

# ── Testing ──────────────────────────────────────────────────
test:
	docker compose exec backend pytest -v

test-local:
	cd backend && pip install aiosqlite && pytest -v

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
