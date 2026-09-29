#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_COMPOSE="${PROJECT_DIR}/docker-compose.test.yml"

cd "$PROJECT_DIR"

cleanup() {
  docker compose --ansi never -f "$TEST_COMPOSE" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

echo '[chat-check] build isolated backend test image'
COMPOSE_BAKE=false docker compose --ansi never --progress plain -f "$TEST_COMPOSE" build backend-test

echo '[chat-check] start isolated PostgreSQL/pgvector'
docker compose --ansi never -f "$TEST_COMPOSE" up -d postgres

echo '[chat-check] run chat reliability regressions'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test \
  pytest -q \
  apps/chat/test_public_error_codes.py \
  apps/chat/test_reconnect_fast_path.py \
  apps/ai_registry/test_chat_reliability.py

echo 'CHAT RELIABILITY CHECK: PASS'
