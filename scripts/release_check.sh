#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_COMPOSE="${PROJECT_DIR}/docker-compose.test.yml"
PROD_COMPOSE="${PROJECT_DIR}/docker-compose.prod.yml"
cd "$PROJECT_DIR"

cleanup() {
  docker compose -f "$TEST_COMPOSE" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

printf '[1/9] Secret scan\n'
bash ./scripts/security_scan.sh

printf '[2/9] Shell syntax\n'
bash -n install.sh
for script in scripts/*.sh; do
  bash -n "$script"
done

printf '[3/9] Compose syntax\n'
docker compose -f "$TEST_COMPOSE" config >/dev/null
APP_DOMAIN=release-check.example.test \
ACME_EMAIL=ops@example.test \
POSTGRES_DB=release_check \
POSTGRES_USER=release_check \
POSTGRES_PASSWORD=release-check-password \
REDIS_PASSWORD=release-check-redis-password \
PUBLIC_API_URL=https://release-check.example.test/api/v1 \
docker compose --env-file .env.example -f "$PROD_COMPOSE" config >/dev/null

printf '[4/9] Build isolated test stack\n'
docker compose -f "$TEST_COMPOSE" build backend-test
docker compose -f "$TEST_COMPOSE" up -d postgres

printf '[5/9] Backend lint\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test ruff check .

printf '[6/9] Backend tests on PostgreSQL/pgvector\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test pytest -q

printf '[7/9] Django checks and migration drift\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py check
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py makemigrations --check --dry-run

printf '[8/9] Frontend production build\n'
docker build -t ai-workspace-frontend-test frontend

printf '[9/9] Frontend lint\n'
docker run --rm ai-workspace-frontend-test sh -c 'npm run lint'

printf 'RELEASE CHECK: PASS\n'
