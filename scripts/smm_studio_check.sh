#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.test.yml}"
ENV_FILE="${ENV_FILE:-.env.test}"
DC=(docker compose)
if [[ -f "$ENV_FILE" ]]; then
  DC+=(--env-file "$ENV_FILE")
fi
DC+=(-f "$COMPOSE_FILE")

cleanup() {
  "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

printf '[1/5] Build backend test image\n'
"${DC[@]}" build backend

printf '[2/5] Start PostgreSQL/Redis\n'
"${DC[@]}" up -d postgres redis

printf '[3/5] Django system + migration drift checks\n'
"${DC[@]}" run --rm backend python manage.py check
"${DC[@]}" run --rm backend python manage.py makemigrations --check --dry-run

printf '[4/5] SMM/connection regressions\n'
"${DC[@]}" run --rm backend pytest -q \
  apps/connections/test_smm_studio.py \
  apps/connections/test_connection_validation.py \
  apps/connections/test_agent_connection_freeze.py

printf '[5/5] Safe SMM diagnostic\n'
"${DC[@]}" run --rm backend python manage.py smm_runtime_check

printf 'SMM_STUDIO_CHECK=PASS\n'
