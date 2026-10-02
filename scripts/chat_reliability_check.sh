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
(cd frontend && npm run test:chat)
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
grep -Fq '<ModelPicker value={selectorValue} models={models}' frontend/app/workspace/WorkspaceV2.tsx
grep -Fq 'const showActions=!editing&&(Boolean(message.content)||(message.role==="assistant"&&message.status!=="streaming"));' frontend/app/workspace/MessageCard.tsx
grep -Fq 'aria-label="Повторить запрос"' frontend/app/workspace/MessageCard.tsx

grep -Fq 'api<AIModel[]>("/models/")' frontend/app/workspace/ModelPicker.tsx
grep -Fq 'window.setInterval(()=>void refresh(),30000)' frontend/app/workspace/ModelPicker.tsx
grep -Fq 'catalogAvailableRef=useRef(initialCatalog.length>0)' frontend/app/workspace/ModelPicker.tsx
grep -Fq 'broadcastCatalogState("error",catalogAvailableRef.current)' frontend/app/workspace/ModelPicker.tsx
grep -Fq 'label:"Авто"' frontend/app/workspace/ModelPicker.tsx
grep -Fq 'label:"Простой"' frontend/app/workspace/ModelPicker.tsx
grep -Fq 'label:"Средний"' frontend/app/workspace/ModelPicker.tsx
grep -Fq 'label:"Сложный"' frontend/app/workspace/ModelPicker.tsx
grep -Fq 'yandexgpt:{label:"YandexGPT"' frontend/app/workspace/ModelPicker.tsx
if grep -Fq 'broadcastCatalogState("error",catalog.length>0)' frontend/app/workspace/ModelPicker.tsx; then
  echo '[FAIL] ModelPicker uses stale catalog closure on refresh errors'
  exit 1
fi
if grep -Fq 'api<AIModel[]>("/models/")' frontend/app/workspace/Composer.tsx; then
  echo '[FAIL] Composer performs a competing /models/ poll'
  exit 1
fi
if grep -Fq 'window.setInterval(()=>void refresh(),30000)' frontend/app/workspace/Composer.tsx; then
  echo '[FAIL] Composer still owns an independent catalog refresh timer'
  exit 1
fi
grep -Fq 'MODEL_CATALOG_EVENT="aiws:model-catalog"' frontend/app/workspace/Composer.tsx
grep -Fq 'workspaceModelsAvailable===false' frontend/app/workspace/Composer.tsx
grep -Fq 'routing_tiers_configured' frontend/app/workspace/ModelPicker.tsx
grep -Fq 'onSend:()=>void|Promise<void>;' frontend/app/workspace/Composer.tsx
grep -Fq 'await Promise.resolve(onSend())' frontend/app/workspace/Composer.tsx
grep -Fq 'streamMessage owns durable cancellation' frontend/app/workspace/Composer.tsx
if grep -Fq '/messages/cancel/' frontend/app/workspace/Composer.tsx; then
  echo '[FAIL] Composer sends a duplicate cancellation request; transport must own cancel'
  exit 1
fi
if grep -Fq 'PRE_SEND_GATE_MS' frontend/app/workspace/Composer.tsx; then
  echo '[FAIL] composer still uses a timer-based submit latch instead of the real send lifecycle'
  exit 1
fi
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
  apps/ai_registry/test_stream_completion_integrity.py \
  apps/ai_registry/test_credential_save_continuity.py \
  apps/chat/test_preflight_reconnect_authorization.py \
  apps/chat/test_draft_concurrency.py \
  apps/files/test_rag_outage_fallback.py \
  apps/ai_registry/test_chat_reliability.py \
  apps/ai_registry/test_unified_readiness.py \
  apps/ai_registry/test_client_readiness_wiring.py \
  apps/ai_registry/test_provider_key_selection.py \
  apps/ai_registry/test_dispatch_runtime.py \
  apps/ai_registry/test_special_provider_dispatch.py \
  apps/ai_registry/test_yandexgpt_runtime.py \
  apps/ai_registry/test_provider_recovery_probe.py \
  apps/ai_registry/test_provider_recovery_lock.py \
  apps/ai_registry/test_http_error_classification.py \
  apps/ai_registry/test_model_quarantine.py \
  apps/ai_registry/test_model_quarantine_recovery_lock.py \
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
  apps/billing/test_loss_watchdog.py \
  apps/procurement/test_chat_reservation_cleanup.py \
  apps/procurement/test_special_provider_procurement.py \
  apps/procurement/test_runtime_account_failover.py \
  apps/procurement/test_request_cost_usage_guard.py \
  apps/chat/test_default_auto_routing.py \
  apps/chat/test_streaming.py \
  apps/chat/tests.py \
  apps/chat/test_client_journey.py \
  apps/chat/test_client_cabinet_journey.py \
  apps/chat/test_chat_cost_confirmation.py \
  apps/chat/test_manual_llm_system_selection.py \
  apps/chat/test_public_system_identity.py \
  apps/chat/test_cost_preview_public_identity.py \
  apps/chat/test_mixed_attachments.py \
  apps/memory_store/tests.py \
  apps/chat/test_activity_stream.py \
  apps/chat/test_reasoning_trace.py \
  apps/chat/test_search_trigger_policy.py \
  apps/chat/test_search_runtime_wiring.py \
  apps/chat/test_paid_search_customer_billing.py \
  apps/chat/test_paid_search_key_readiness.py \
  apps/chat/test_runtime_safety_wiring.py \
  apps/chat/test_runtime_entrypoint_wiring.py \
  apps/chat/test_runtime_installer_contract.py \
  apps/chat/test_customer_capacity.py \
  apps/chat/test_context_safety.py \
  apps/chat/test_context_trust_boundary.py \
  apps/chat/test_conversation_snapshot.py \
  apps/chat/test_execution_readiness_after_provider_reserve.py \
  apps/chat/test_execution_fence.py \
  apps/chat/test_procurement_execution_reservation.py \
  apps/chat/test_procurement_account_binding.py \
  apps/chat/test_provider_delivery_checkpoint.py \
  apps/chat/test_generation_status.py \
  apps/chat/test_asgi_capacity.py \
  apps/chat/test_asgi_backpressure.py \
  apps/chat/test_chat_health_watch.py \
  apps/chat/test_cost_preview_runtime_parity.py \
  apps/chat/test_cost_preview_history_bound.py \
  apps/chat/test_cost_preview_no_double_history.py \
  apps/chat/test_cost_confirmation_resume.py \
  apps/chat/test_cost_view_failure_contract.py \
  apps/chat/test_durable_preflight_stream.py \
  apps/chat/test_attachment_durability.py \
  apps/chat/test_manual_selection_race.py \
  apps/chat/test_quarantine_race_failover.py \
  apps/chat/test_terminal_overrun_recovery.py \
  apps/chat/test_terminal_attempt_cleanup.py \
  apps/chat/test_empty_response_failover.py \
  apps/chat/test_cooperative_cancel.py \
  apps/chat/test_durable_cancellation.py \
  apps/chat/test_reconnect_claim_race.py \
  apps/chat/test_running_reconnect_follow.py \
  apps/chat/test_single_flight_cache_outage.py \
  apps/chat/test_error_contract.py \
  apps/chat/test_web_search_reliability.py \
  apps/chat/test_search_policy_hardening.py \
  apps/ai_registry/test_web_search_failover.py \
  apps/ai_registry/test_web_tools_yandex.py \
  apps/chat/test_public_error_codes.py \
  apps/chat/test_public_history_error_contract.py \
  apps/chat/test_reconnect_fast_path.py \
  apps/chat/test_managed_stream_continuity.py \
  apps/chat/test_provider_exhaustion_failover.py \
  apps/chat/test_partial_cancel_billing.py \
  apps/chat/test_preflight_reservation_cleanup.py \
  apps/chat/test_money_safety_django.py \
  apps/admin_ops/test_provider_client_activation.py \
  apps/admin_ops/test_provider_key_inference_gate.py \
  apps/admin_ops/test_stale_recovery_economics.py \
  apps/admin_ops/test_stale_recovery_active_attempt.py \
  apps/admin_ops/test_model_quarantine_diagnostics.py \
  apps/admin_ops/test_diagnostics_share.py \
  apps/admin_ops/test_system_health.py

echo '[chat-check] django configuration and migration drift'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py check
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py makemigrations --check --dry-run

echo 'CHAT RELIABILITY CHECK: PASS'
