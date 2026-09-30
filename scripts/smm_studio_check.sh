#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.test.yml}"
DC=(docker compose --ansi never -f "$COMPOSE_FILE")

cleanup() {
  "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

printf '[1/5] Build backend test image\n'
COMPOSE_BAKE=false "${DC[@]}" build backend-test

printf '[2/5] Start PostgreSQL and sandbox\n'
"${DC[@]}" up -d postgres sandbox

printf '[3/5] Django system + migration drift checks\n'
"${DC[@]}" run --rm backend-test python manage.py check
"${DC[@]}" run --rm backend-test python manage.py makemigrations --check --dry-run

printf '[4/5] SMM/connection regressions\n'
"${DC[@]}" run --rm backend-test pytest -q \
  apps/connections/test_smm_studio.py \
  apps/connections/test_connection_validation.py \
  apps/connections/test_agent_connection_freeze.py

printf '[5/5] Safe SMM diagnostic\n'
"${DC[@]}" run --rm backend-test python manage.py smm_runtime_check

printf 'SMM_STUDIO_CHECK=PASS\n'
