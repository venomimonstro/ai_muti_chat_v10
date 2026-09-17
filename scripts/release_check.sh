#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_COMPOSE="${PROJECT_DIR}/docker-compose.test.yml"
cd "$PROJECT_DIR"

cleanup() {
  docker compose -f "$TEST_COMPOSE" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

printf '[1/7] Secret scan\n'
./scripts/security_scan.sh

printf '[2/7] Build isolated test stack\n'
docker compose -f "$TEST_COMPOSE" build backend-test
docker compose -f "$TEST_COMPOSE" up -d postgres

printf '[3/7] Backend lint\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test ruff check .

printf '[4/7] Backend tests on PostgreSQL/pgvector\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test pytest -q

printf '[5/7] Django checks and migration drift\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py check
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py makemigrations --check --dry-run

printf '[6/7] Frontend production build\n'
docker build -t ai-workspace-frontend-test frontend

printf '[7/7] Frontend lint\n'
docker run --rm ai-workspace-frontend-test sh -c 'npm run lint'

printf 'RELEASE CHECK: PASS\n'
