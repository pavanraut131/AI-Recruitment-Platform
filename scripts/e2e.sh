#!/usr/bin/env bash
# End-to-end tests (Playwright) against an isolated Compose stack with the fake AI provider.
#
#   scripts/e2e.sh [run] [playwright args…]  up, test, then down (the default; `make e2e`)
#   scripts/e2e.sh up                        build and start the stack, migrate, seed
#   scripts/e2e.sh test [playwright args…]   run Playwright against the running stack
#   scripts/e2e.sh logs                      the stack's logs
#   scripts/e2e.sh down                      stop it and delete its volumes
#
# E2E_KEEP=1 keeps the stack up after `run` (to debug: `scripts/e2e.sh test --ui`, then `down`).
# The stack is its own Compose project (E2E_PROJECT, default hireflow-e2e) on its own ports
# (E2E_FRONTEND_PORT 13000, E2E_BACKEND_PORT 18000, and E2E_MAILPIT_PORT for Mailpit's HTTP API:
# by default E2E_BACKEND_PORT + 25, so projects on different ports never share it), configured
# from .env.example; it never touches the dev stack or reads .env. Playwright gets
# E2E_MAILPIT_URL to read the emails (invitation and reset links). See docker-compose.e2e.yml.
#
# Playwright runs on the host: `cd frontend && npm ci && npx playwright install chromium` once.
set -euo pipefail

cd "$(dirname "$0")/.."

# Git Bash on Windows: don't rewrite container paths (/tmp/…) into Windows paths.
export MSYS_NO_PATHCONV=1

PROJECT="${E2E_PROJECT:-hireflow-e2e}"
export E2E_FRONTEND_PORT="${E2E_FRONTEND_PORT:-13000}"
export E2E_BACKEND_PORT="${E2E_BACKEND_PORT:-18000}"
export E2E_MAILPIT_PORT="${E2E_MAILPIT_PORT:-$((E2E_BACKEND_PORT + 25))}"
SERVICES=(postgres redis minio mailpit backend worker_extract worker_ai worker_default frontend)
# A small deterministic background dataset (campaigns, People, Applications) so lists are not
# empty. The tests create the data they assert on and do not depend on it.
SEED_ARGS=(--people 40 --campaigns 3 --seed 42)

compose() {
  docker compose -p "$PROJECT" --env-file .env.example \
    -f docker-compose.yml -f docker-compose.e2e.yml "$@"
}

up() {
  echo "▶ Starting the E2E stack (project $PROJECT; frontend :$E2E_FRONTEND_PORT, API :$E2E_BACKEND_PORT, Mailpit :$E2E_MAILPIT_PORT)"
  compose up --build --detach --wait --wait-timeout 300 "${SERVICES[@]}"
  echo "▶ Migrating"
  compose exec -T backend alembic upgrade head
  echo "▶ Seeding"
  compose exec -T backend python -m app.devtools.seed "${SEED_ARGS[@]}" \
    --if-empty --manifest /tmp/seed-manifest.json
}

run_tests() {
  echo "▶ Running Playwright against http://localhost:$E2E_FRONTEND_PORT"
  (cd frontend && E2E_BASE_URL="http://localhost:$E2E_FRONTEND_PORT" \
    E2E_MAILPIT_URL="http://localhost:$E2E_MAILPIT_PORT" npx playwright test "$@")
}

down() {
  echo "▶ Removing the E2E stack and its volumes"
  compose down --volumes --remove-orphans
}

command="${1:-run}"
case "$command" in
  up | test | logs | down | run) shift || true ;;
  -*) command=run ;; # `scripts/e2e.sh --headed`: Playwright args for `run`
  *)
    echo "usage: $0 [run|up|test|logs|down] [playwright args…]" >&2
    exit 2
    ;;
esac

case "$command" in
  up) up ;;
  test) run_tests "$@" ;;
  logs) compose logs --no-color "${SERVICES[@]}" ;;
  down) down ;;
  run)
    if [[ "${E2E_KEEP:-0}" != "1" ]]; then trap down EXIT; fi
    up
    status=0
    run_tests "$@" || status=$?
    if [[ $status -ne 0 ]]; then
      mkdir -p frontend/e2e-results
      compose logs --no-color "${SERVICES[@]}" >frontend/e2e-results/compose.log 2>&1 || true
      echo "✖ E2E failed. Report: frontend/e2e-results/report (npx playwright show-report e2e-results/report)," \
        "stack logs: frontend/e2e-results/compose.log" >&2
    fi
    if [[ "${E2E_KEEP:-0}" == "1" ]]; then
      echo "ℹ E2E_KEEP=1: the stack is still up at http://localhost:$E2E_FRONTEND_PORT. Remove it with: $0 down"
    fi
    exit $status
    ;;
esac
