#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_COMPOSE="${PROJECT_DIR}/docker-compose.test.yml"
cd "$PROJECT_DIR"

cleanup() {
  docker compose -f "$TEST_COMPOSE" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

printf '[1/5] Build backend test image\n'
docker compose -f "$TEST_COMPOSE" build backend-test
docker compose -f "$TEST_COMPOSE" up -d postgres

printf '[2/5] Workspace UX and client journey tests\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test pytest -q \
  apps/chat/test_workspace_ux.py \
  apps/chat/test_client_journey.py \
  apps/chat/test_streaming.py \
  apps/accounts/test_account_data.py \
  apps/accounts/test_mfa.py \
  apps/accounts/test_usage.py

printf '[3/5] Django model and migration checks\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py check
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py makemigrations --check --dry-run

printf '[4/5] Frontend lint\n'
docker build -t ai-workspace-client-check frontend
docker run --rm ai-workspace-client-check sh -c 'npm run lint'

printf '[5/5] Frontend production build\n'
docker run --rm ai-workspace-client-check sh -c 'npm run build'

printf 'CLIENT WORKSPACE CHECK: PASS\n'
