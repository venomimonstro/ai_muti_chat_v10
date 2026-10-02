#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_COMPOSE="${PROJECT_DIR}/docker-compose.test.yml"
PROD_COMPOSE="${PROJECT_DIR}/docker-compose.prod.yml"
FRONTEND_SMOKE_CONTAINER="ai-workspace-frontend-smoke-$$"
FRONTEND_SMOKE_PORT="${FRONTEND_SMOKE_PORT:-39001}"
LOG_DIR="${PROJECT_DIR}/logs"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${LOG_DIR}/release-check-${RUN_ID}.log"
CURRENT_STEP="initialization"
STARTED_AT="$(date +%s)"

mkdir -p "$LOG_DIR"
touch "$LOG_FILE"
cd "$PROJECT_DIR"

# FULL release builds backend/sandbox/frontend images side-by-side with the running
# production stack. Fail early (and reclaim only disposable build cache) instead of
# dying halfway through a Playwright/containerd layer with ENOSPC.
RELEASE_MIN_FREE_KB="${RELEASE_MIN_FREE_KB:-8388608}" # 8 GiB
DOCKER_STORAGE_PATH="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
if [[ -z "$DOCKER_STORAGE_PATH" || ! -e "$DOCKER_STORAGE_PATH" ]]; then
  DOCKER_STORAGE_PATH="/var/lib/containerd"
fi
[[ -e "$DOCKER_STORAGE_PATH" ]] || DOCKER_STORAGE_PATH="$PROJECT_DIR"

available_kb() {
  df -Pk "$DOCKER_STORAGE_PATH" | awk 'NR==2 {print $4}'
}

ensure_release_disk() {
  local free_kb
  free_kb="$(available_kb)"
  if (( free_kb >= RELEASE_MIN_FREE_KB )); then
    printf '[DISK] Free space before release build: %s MiB\n' "$((free_kb / 1024))"
    return 0
  fi

  printf '[DISK] Low free space: %s MiB. Reclaiming disposable Docker build cache...\n' "$((free_kb / 1024))" >&2
  docker builder prune -af >/dev/null 2>&1 || true
  docker image prune -f >/dev/null 2>&1 || true
  free_kb="$(available_kb)"
  if (( free_kb < RELEASE_MIN_FREE_KB )); then
    printf '[FAIL] Недостаточно свободного места для FULL release: %s MiB; требуется минимум %s MiB.\n'       "$((free_kb / 1024))" "$((RELEASE_MIN_FREE_KB / 1024))" >&2
    printf '[INFO] Production volumes не удалялись. Освободите диск и повторите update.\n' >&2
    exit 1
  fi
  printf '[DISK] Free space after safe cache cleanup: %s MiB\n' "$((free_kb / 1024))"
}

ensure_release_disk

exec > >(tee -a "$LOG_FILE") 2>&1

cleanup() {
  docker rm -f "$FRONTEND_SMOKE_CONTAINER" >/dev/null 2>&1 || true
  docker compose --ansi never -f "$TEST_COMPOSE" down -v --remove-orphans >/dev/null 2>&1 || true
}

abort_release() {
  local signal="${1:-INTERRUPTED}"
  printf '\n[ABORT] Release check interrupted by %s during: %s\n' "$signal" "$CURRENT_STEP" >&2
  cleanup
  printf '[INFO] Full log: %s\n' "$LOG_FILE" >&2
  exit 130
}

failure_excerpt() {
  local excerpt
  excerpt="$(grep -nE 'FAILED|ERROR|Traceback|AssertionError|\[FAIL\]|E +[A-Za-z_]+|F +[A-Za-z_]+|error:' "$LOG_FILE" 2>/dev/null | tail -n 80 || true)"
  if [[ -n "$excerpt" ]]; then
    printf '[DIAG] Последние диагностические строки:\n%s\n' "$excerpt" >&2
  else
    printf '[DIAG] Явный marker ошибки не найден; последние 60 строк этапа:\n' >&2
    tail -n 60 "$LOG_FILE" >&2 || true
  fi
}

on_error() {
  local code=$?
  trap - ERR
  printf '\n[FAIL] %s (exit code %s)\n' "$CURRENT_STEP" "$code" >&2
  failure_excerpt
  printf '[INFO] Full log: %s\n' "$LOG_FILE" >&2
  return "$code"
}

trap cleanup EXIT
trap on_error ERR
trap 'abort_release SIGINT' INT
trap 'abort_release SIGTERM' TERM
trap 'abort_release SIGHUP' HUP
trap 'abort_release SIGTSTP' TSTP

step() {
  local number="$1" title="$2"
  CURRENT_STEP="${number}/20 ${title}"
  printf '\n============================================================\n'
  printf '[%s/20] START  %s\n' "$number" "$title"
  printf '============================================================\n'
}

pass() {
  printf '[%s/20] PASS   %s\n' "$1" "$2"
}

run_pytest() {
  local label="$1"
  shift
  printf '[TEST] %s\n' "$label"
  docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test \
    pytest -vv --tb=short -ra --disable-warnings "$@"
}

step 1 'Secret scan'
bash ./scripts/security_scan.sh
pass 1 'Secret scan'

step 2 'Shell and Python syntax'
bash -n install.sh
for script in scripts/*.sh; do
  bash -n "$script"
done
PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo 'Python 3 interpreter is required for release checks (python3/python not found)' >&2
  exit 1
fi
"$PYTHON_BIN" -m py_compile scripts/commercial_http_smoke.py scripts/b2b_http_smoke.py
"$PYTHON_BIN" -m compileall -q backend
pass 2 'Shell and Python syntax'

step 3 'Compose syntax'
docker compose --ansi never -f "$TEST_COMPOSE" config >/dev/null
APP_DOMAIN=release-check.example.test \
ACME_EMAIL=ops@example.test \
POSTGRES_DB=release_check \
POSTGRES_USER=release_check \
POSTGRES_PASSWORD=release-check-password \
REDIS_PASSWORD=release-check-redis-password \
SEARXNG_SECRET=release-check-searxng-secret \
WEB_SEARCH_BASE_URL=http://searxng:8080 \
WEB_SEARCH_TRUSTED_HOSTS=searxng \
PUBLIC_API_URL=https://release-check.example.test/api/v1 \
docker compose --ansi never --env-file .env.example -f "$PROD_COMPOSE" config >/dev/null
pass 3 'Compose syntax'

step 4 'Build isolated test stack'
COMPOSE_BAKE=false docker compose --ansi never --progress plain -f "$TEST_COMPOSE" build backend-test sandbox
docker compose --ansi never -f "$TEST_COMPOSE" up -d postgres sandbox
pass 4 'Build isolated test stack'

step 5 'Backend blocking lint'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test ruff check . --select E9,F63,F7,F82
pass 5 'Backend blocking lint'

step 6 'Backend lint debt report (non-blocking)'
LINT_DEBT_LOG="$(mktemp)"
if ! docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test ruff check . >"$LINT_DEBT_LOG" 2>&1; then
  printf '[WARN] Style debt remains; correctness lint already passed.\n'
  tail -n 3 "$LINT_DEBT_LOG" || true
else
  printf 'Full Ruff lint: PASS\n'
fi
rm -f "$LINT_DEBT_LOG"
pass 6 'Backend lint debt report'

step 7 'Backend tests on PostgreSQL/pgvector'
run_pytest 'Full backend test suite' .
pass 7 'Backend tests on PostgreSQL/pgvector'

step 8 'Sprint 68 tenant/security regressions'
run_pytest 'Tenant/security/webhook regressions' \
  apps/agents/test_tenant_security.py \
  apps/agents/test_webhook_hardening.py \
  apps/agents/test_webhooks.py
pass 8 'Sprint 68 tenant/security regressions'

step 9 'Sprint 69 commercial limits regressions'
run_pytest 'Commercial limits regressions' apps/agents/test_commercial_limits.py
pass 9 'Sprint 69 commercial limits regressions'

step 10 'Sprint 70-73 Agent/Dev self-service safety regressions'
run_pytest 'Agent/Dev self-service regressions' \
  apps/agents/test_agent_test_mode.py \
  apps/agents/test_dev_safety.py \
  apps/agents/test_diagnostics.py
pass 10 'Sprint 70-73 Agent/Dev self-service safety regressions'

step 11 'Django checks and migration drift'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py check
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py makemigrations --check --dry-run
pass 11 'Django checks and migration drift'

step 12 'Economic safety invariants'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py economic_safety_check
pass 12 'Economic safety invariants'

step 13 'Billing ledger integrity'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py billing_integrity_check
pass 13 'Billing ledger integrity'

step 14 'Agent Studio integrity, security and commercial limits'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py agent_system_audit
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py agent_webhook_audit
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py agent_security_audit
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py agent_commercial_limits_audit
pass 14 'Agent Studio integrity, security and commercial limits'

step 15 'Agent recovery, connections and SMM Studio integrity'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py agent_recovery_audit
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py connection_health_audit
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py smm_runtime_check
run_pytest 'SMM Studio safety regressions' \
  apps/connections/test_smm_studio.py \
  apps/connections/test_connection_validation.py \
  apps/connections/test_agent_connection_freeze.py
pass 15 'Agent recovery, connections and SMM Studio integrity'

step 16 'Dev Studio readiness'
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py dev_studio_audit
run_pytest 'Dev Studio V2 runtime regressions' \
  apps/agents/test_dev_context_discovery.py \
  apps/agents/test_dev_plan.py \
  apps/agents/test_dev_changes_v2.py \
  apps/agents/test_dev_preapproval_validation.py \
  apps/agents/test_dev_provider_retry.py \
  apps/agents/test_sandbox.py \
  apps/agents/test_sandbox_client_health.py
pass 16 'Dev Studio readiness'

step 17 'Agent Runtime billing integrity'
run_pytest 'Agent/provider settlement and credential-binding regressions' \
  apps/ai_registry/test_commercial_dispatch_contract.py \
  apps/agents/test_agent_recovery.py \
  apps/agents/test_ai_planner_billing.py \
  apps/agents/test_dev_model_execution.py \
  apps/agents/test_dev_settlement_integrity.py \
  apps/agents/test_generic_team_cancellation.py \
  apps/b2b_api/test_cost_guard.py \
  apps/chat/test_compare_cost_guard.py \
  apps/procurement/tests.py
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py agent_billing_audit
pass 17 'Agent Runtime billing integrity'

step 18 'Frontend production build'
DOCKER_BUILDKIT=1 docker build --progress=plain \
  --target builder \
  --build-arg NEXT_PUBLIC_SITE_URL=http://127.0.0.1:${FRONTEND_SMOKE_PORT} \
  -t ai-workspace-frontend-builder frontend
DOCKER_BUILDKIT=1 docker build --progress=plain \
  --build-arg NEXT_PUBLIC_SITE_URL=http://127.0.0.1:${FRONTEND_SMOKE_PORT} \
  -t ai-workspace-frontend-test frontend
pass 18 'Frontend production build'

step 19 'Frontend lint'
docker run --rm ai-workspace-frontend-builder sh -c 'npm run lint'
pass 19 'Frontend lint'

step 20 'Frontend runtime route smoke'
docker run -d --rm --name "$FRONTEND_SMOKE_CONTAINER" \
  -p "127.0.0.1:${FRONTEND_SMOKE_PORT}:3000" ai-workspace-frontend-test >/dev/null
READY=false
for _attempt in $(seq 1 30); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${FRONTEND_SMOKE_PORT}/" >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 1
done
[[ "$READY" == true ]] || { echo 'Frontend runtime did not become ready' >&2; exit 1; }
for route in / /pricing /faq /login /register /api /use-cases/marketing /app /app/account /app/wallet /app/usage /app/settings /app/projects /app/projects/00000000-0000-0000-0000-000000000000/github /app/help /app/notifications /app/images /app/compare /app/agents /app/teams /app/schedules /app/events /app/integrations /app/smm /app/dev /app/runs /app/runs/00000000-0000-0000-0000-000000000000 /admin-console /admin-console/system /admin-console/providers /admin-console/finance /admin-console/security /admin-console/operations /admin-console/drills /admin-console/compliance /sitemap.xml /robots.txt; do
  printf '[SMOKE] %-70s ' "$route"
  if curl -fsS --max-time 5 "http://127.0.0.1:${FRONTEND_SMOKE_PORT}${route}" >/dev/null; then
    printf 'PASS\n'
  else
    printf 'FAIL\n' >&2
    exit 1
  fi
done
pass 20 'Frontend runtime route smoke'

FINISHED_AT="$(date +%s)"
DURATION="$((FINISHED_AT - STARTED_AT))"
printf '\n============================================================\n'
printf 'RELEASE CHECK: PASS\n'
printf 'Duration: %ss\n' "$DURATION"
printf 'Full log: %s\n' "$LOG_FILE"
printf '============================================================\n'