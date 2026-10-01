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

echo '[chat-check] frontend stream/cancel/reconnect/activity contract'
grep -Fq '/messages/cancel/' frontend/lib/api.ts
grep -Fq '"cancelled"' frontend/lib/api.ts
grep -Fq 'requestStreamCancellation' frontend/lib/api.ts
grep -Fq 'stream_first_event_timeout' frontend/lib/api.ts
grep -Fq 'reader.cancel("stream_first_event_timeout")' frontend/lib/api.ts
grep -Fq 'const completedSnapshot = event === "snapshot" && String(parsed.state ?? "") === "completed";' frontend/lib/api.ts
grep -Fq 'verifyPendingStream' frontend/lib/api.ts
grep -Fq '/messages/status/?idempotency_key=' frontend/lib/api.ts
grep -Fq 'reason instanceof ApiError && reason.status === 403' frontend/lib/api.ts
grep -Fq 'research_progress' frontend/lib/api.ts
grep -Fq 'WorkspaceExperience' frontend/app/app/page.tsx
grep -Fq 'ActivityTrace' frontend/app/workspace/WorkspaceExperience.tsx
grep -Fq 'event==="activity"' frontend/app/workspace/WorkspaceV2.tsx
grep -Fq '<ActivityTrace steps={visibleActivity}' frontend/app/workspace/WorkspaceV2.tsx
grep -Fq 'Promise.allSettled' frontend/app/workspace/WorkspaceV2.tsx
grep -Fq 'Чат доступен. Часть вспомогательных данных временно не загрузилась' frontend/app/workspace/WorkspaceV2.tsx
grep -Fq 'const PRE_SEND_GATE_MS=15000;' frontend/app/workspace/Composer.tsx
grep -Fq 'const noModelsAvailable=explicitModelControl&&' frontend/app/workspace/Composer.tsx
grep -Fq 'система автоматически использует рабочую резервную модель' frontend/app/workspace/Composer.tsx
if grep -Fq 'sending||selectedModelUnavailable||noModelsAvailable' frontend/app/workspace/Composer.tsx \
  || grep -Fq 'tooLong||selectedModelUnavailable||noModelsAvailable' frontend/app/workspace/Composer.tsx; then
  echo '[FAIL] unavailable preferred manual model still blocks send instead of backend continuity fallback'
  exit 1
fi
grep -Fq '/messages/status/' frontend/app/components/WorkspaceRuntimeGuard.tsx
grep -Fq 'zero routing UI/state responsibilities' frontend/app/components/WorkspaceRuntimeGuard.tsx
if grep -Fq 'dispatchRouting(source' frontend/app/components/WorkspaceRuntimeGuard.tsx; then
  echo '[FAIL] legacy WorkspaceRuntimeGuard still overrides the visible model/routing choice'
  exit 1
fi

echo '[chat-check] build isolated backend test image'
COMPOSE_BAKE=false docker compose --ansi never --progress plain -f "$TEST_COMPOSE" build backend-test

echo '[chat-check] start isolated PostgreSQL/pgvector'
docker compose --ansi never -f "$TEST_COMPOSE" up -d postgres

echo '[chat-check] run production chat reliability regressions'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test \
  pytest -q \
  apps/ai_registry/test_chat_reliability.py \
  apps/ai_registry/test_unified_readiness.py \
  apps/ai_registry/test_client_readiness_wiring.py \
  apps/ai_registry/test_provider_key_selection.py \
  apps/ai_registry/test_dispatch_runtime.py \
  apps/ai_registry/test_special_provider_dispatch.py \
  apps/ai_registry/test_provider_recovery_probe.py \
  apps/ai_registry/test_http_error_classification.py \
  apps/ai_registry/test_model_quarantine.py \
  apps/ai_registry/test_router.py \
  apps/ai_registry/test_router_v2.py \
  apps/ai_registry/test_router_intelligence_v3.py \
  apps/ai_registry/test_routing_pools.py \
  apps/ai_registry/test_manual_continuity.py \
  apps/ai_registry/test_auto_continuity.py \
  apps/ai_registry/test_search_intelligence_v3.py \
  apps/ai_registry/test_web_fetch_security.py \
  apps/ai_registry/test_client_catalog_readonly.py \
  apps/billing/test_pricing_bridge.py \
  apps/billing/test_confirmed_usage_guard.py \
  apps/billing/test_model_loss_isolation.py \
  apps/procurement/test_chat_reservation_cleanup.py \
  apps/procurement/test_special_provider_procurement.py \
  apps/chat/test_default_auto_routing.py \
  apps/chat/test_activity_stream.py \
  apps/chat/test_runtime_safety_wiring.py \
  apps/chat/test_runtime_entrypoint_wiring.py \
  apps/chat/test_customer_capacity.py \
  apps/chat/test_context_safety.py \
  apps/chat/test_context_trust_boundary.py \
  apps/chat/test_conversation_snapshot.py \
  apps/chat/test_execution_readiness_after_provider_reserve.py \
  apps/chat/test_execution_fence.py \
  apps/chat/test_procurement_execution_reservation.py \
  apps/chat/test_generation_status.py \
  apps/chat/test_asgi_capacity.py \
  apps/chat/test_cost_preview_runtime_parity.py \
  apps/chat/test_cost_preview_history_bound.py \
  apps/chat/test_cost_preview_no_double_history.py \
  apps/chat/test_cost_confirmation_resume.py \
  apps/chat/test_cost_view_failure_contract.py \
  apps/chat/test_manual_selection_race.py \
  apps/chat/test_quarantine_race_failover.py \
  apps/chat/test_terminal_overrun_recovery.py \
  apps/chat/test_empty_response_failover.py \
  apps/chat/test_cooperative_cancel.py \
  apps/chat/test_durable_cancellation.py \
  apps/chat/test_reconnect_claim_race.py \
  apps/chat/test_single_flight_cache_outage.py \
  apps/chat/test_error_contract.py \
  apps/chat/test_web_search_reliability.py \
  apps/chat/test_public_error_codes.py \
  apps/chat/test_public_history_error_contract.py \
  apps/chat/test_reconnect_fast_path.py \
  apps/chat/test_managed_stream_continuity.py \
  apps/chat/test_provider_exhaustion_failover.py \
  apps/chat/test_partial_cancel_billing.py \
  apps/chat/test_preflight_reservation_cleanup.py \
  apps/chat/test_money_safety_django.py \
  apps/admin_ops/test_provider_client_activation.py \
  apps/admin_ops/test_stale_recovery_economics.py \
  apps/admin_ops/test_stale_recovery_active_attempt.py \
  apps/admin_ops/test_model_quarantine_diagnostics.py \
  apps/admin_ops/test_diagnostics_share.py \
  apps/admin_ops/test_system_health.py

echo '[chat-check] django configuration and migration drift'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py check
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py makemigrations --check --dry-run

echo 'CHAT RELIABILITY CHECK: PASS'
