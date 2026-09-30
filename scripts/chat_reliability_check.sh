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

echo '[chat-check] run production chat reliability regressions'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test \
  pytest -q \
  apps/ai_registry/test_chat_reliability.py \
  apps/ai_registry/test_provider_key_selection.py \
  apps/ai_registry/test_dispatch_runtime.py \
  apps/ai_registry/test_router.py \
  apps/ai_registry/test_routing_pools.py \
  apps/ai_registry/test_client_catalog_readonly.py \
  apps/billing/test_pricing_bridge.py \
  apps/procurement/test_chat_reservation_cleanup.py \
  apps/chat/test_default_auto_routing.py \
  apps/chat/test_public_error_codes.py \
  apps/chat/test_reconnect_fast_path.py \
  apps/chat/test_managed_stream_continuity.py \
  apps/chat/test_provider_exhaustion_failover.py \
  apps/chat/test_partial_cancel_billing.py \
  apps/chat/test_preflight_reservation_cleanup.py \
  apps/chat/test_money_safety_django.py

echo '[chat-check] django configuration and migration drift'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py check
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py makemigrations --check --dry-run

echo 'CHAT RELIABILITY CHECK: PASS'
