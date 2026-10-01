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

echo '[chat-check] authoritative architecture contract'
test -s docs/CHAT_ARCHITECTURE.md

echo '[chat-check] frontend stream/cancel/reconnect contract'
grep -Fq '/messages/cancel/' frontend/lib/api.ts
grep -Fq '"cancelled"' frontend/lib/api.ts
grep -Fq 'requestStreamCancellation' frontend/lib/api.ts
grep -Fq 'stream_first_event_timeout' frontend/lib/api.ts
grep -Fq 'reader.cancel("stream_first_event_timeout")' frontend/lib/api.ts

echo '[chat-check] build isolated backend test image'
COMPOSE_BAKE=false docker compose --ansi never --progress plain -f "$TEST_COMPOSE" build backend-test

echo '[chat-check] start isolated PostgreSQL/pgvector'
docker compose --ansi never -f "$TEST_COMPOSE" up -d postgres

echo '[chat-check] run production chat reliability regressions'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test \
  pytest -q \
  apps/ai_registry/test_chat_reliability.py \
  apps/ai_registry/test_unified_readiness.py \
  apps/ai_registry/test_provider_key_selection.py \
  apps/ai_registry/test_dispatch_runtime.py \
  apps/ai_registry/test_special_provider_dispatch.py \
  apps/ai_registry/test_provider_recovery_probe.py \
  apps/ai_registry/test_http_error_classification.py \
  apps/ai_registry/test_model_quarantine.py \
  apps/ai_registry/test_router.py \
  apps/ai_registry/test_router_v2.py \
  apps/ai_registry/test_routing_pools.py \
  apps/ai_registry/test_manual_continuity.py \
  apps/ai_registry/test_auto_continuity.py \
  apps/ai_registry/test_client_catalog_readonly.py \
  apps/billing/test_pricing_bridge.py \
  apps/billing/test_confirmed_usage_guard.py \
  apps/billing/test_model_loss_isolation.py \
  apps/procurement/test_chat_reservation_cleanup.py \
  apps/procurement/test_special_provider_procurement.py \
  apps/chat/test_default_auto_routing.py \
  apps/chat/test_runtime_safety_wiring.py \
  apps/chat/test_runtime_entrypoint_wiring.py \
  apps/chat/test_execution_readiness_after_provider_reserve.py \
  apps/chat/test_asgi_capacity.py \
  apps/chat/test_cost_preview_runtime_parity.py \
  apps/chat/test_manual_selection_race.py \
  apps/chat/test_quarantine_race_failover.py \
  apps/chat/test_terminal_overrun_recovery.py \
  apps/chat/test_cooperative_cancel.py \
  apps/chat/test_durable_cancellation.py \
  apps/chat/test_single_flight_cache_outage.py \
  apps/chat/test_error_contract.py \
  apps/chat/test_web_search_reliability.py \
  apps/chat/test_public_error_codes.py \
  apps/chat/test_reconnect_fast_path.py \
  apps/chat/test_managed_stream_continuity.py \
  apps/chat/test_provider_exhaustion_failover.py \
  apps/chat/test_partial_cancel_billing.py \
  apps/chat/test_preflight_reservation_cleanup.py \
  apps/chat/test_money_safety_django.py \
  apps/admin_ops/test_provider_client_activation.py \
  apps/admin_ops/test_stale_recovery_economics.py \
  apps/admin_ops/test_model_quarantine_diagnostics.py \
  apps/admin_ops/test_diagnostics_share.py \
  apps/admin_ops/test_system_health.py

echo '[chat-check] django configuration and migration drift'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py check
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py makemigrations --check --dry-run

echo 'CHAT RELIABILITY CHECK: PASS'
