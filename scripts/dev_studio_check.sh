#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_COMPOSE="${PROJECT_DIR}/docker-compose.test.yml"
PROD_COMPOSE="${PROJECT_DIR}/docker-compose.prod.yml"
cd "$PROJECT_DIR"

printf '\n=== DEV STUDIO V2 CHECK ===\n'

python3 -m compileall -q \
  backend/apps/agents/dev_context.py \
  backend/apps/agents/dev_changes.py \
  backend/apps/agents/dev_execution.py \
  backend/apps/agents/dev_execution_v2.py \
  backend/apps/agents/dev_model_execution.py \
  backend/apps/agents/dev_model_fallback.py \
  backend/apps/agents/dev_plan.py \
  backend/apps/agents/dev_prompt.py \
  backend/apps/agents/dev_provider_retry.py \
  backend/apps/agents/dev_recovery.py \
  backend/apps/agents/dev_security.py \
  backend/apps/agents/sandbox_client.py \
  backend/apps/agents/sandbox_server.py \
  backend/apps/agents/team_readiness.py \
  backend/apps/agents/team_runtime.py \
  backend/apps/agents/team_runtime_v2.py \
  backend/apps/agents/management/commands/dev_studio_recover.py \
  backend/apps/agents/management/commands/dev_studio_runtime_audit.py \
  backend/apps/agents/management/commands/dev_studio_e2e_audit.py
printf '[PASS] Python syntax\n'

APP_DOMAIN=dev-check.example.test \
ACME_EMAIL=ops@example.test \
POSTGRES_DB=dev_check \
POSTGRES_USER=dev_check \
POSTGRES_PASSWORD=dev-check-password \
REDIS_PASSWORD=dev-check-redis-password \
SEARXNG_SECRET=dev-check-searxng-secret \
PUBLIC_API_URL=https://dev-check.example.test/api/v1 \
NEXT_PUBLIC_SITE_URL=https://dev-check.example.test \
docker compose --ansi never --env-file .env.example -f "$PROD_COMPOSE" config >/dev/null
printf '[PASS] Production compose syntax\n'

COMPOSE_BAKE=false docker compose --ansi never --progress plain -f "$TEST_COMPOSE" build backend-test sandbox

docker compose --ansi never -f "$TEST_COMPOSE" up -d postgres sandbox
cleanup(){ docker compose --ansi never -f "$TEST_COMPOSE" down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test \
  pytest -q --tb=short \
    apps/agents/test_dev_context_discovery.py \
    apps/agents/test_dev_plan.py \
    apps/agents/test_dev_changes_v2.py \
    apps/agents/test_dev_preapproval_validation.py \
    apps/agents/test_dev_execution_v2.py \
    apps/agents/test_dev_provider_retry.py \
    apps/agents/test_dev_model_fallback.py \
    apps/agents/test_dev_model_execution.py \
    apps/agents/test_team_runtime_v2_activation.py \
    apps/agents/test_dev_recovery.py \
    apps/agents/test_dev_security.py \
    apps/agents/test_dev_prompt_boundary.py \
    apps/agents/test_sandbox.py \
    apps/agents/test_sandbox_client_health.py \
    apps/agents/test_dev_execution_cancel.py \
    apps/agents/test_dev_cancellation.py \
    apps/agents/test_dev_member_budgets.py \
    apps/agents/test_dev_approval_revalidation.py \
    apps/agents/test_dev_safety.py \
    apps/agents/test_dev_team_guards.py \
    apps/agents/test_team_director_order.py
printf '[PASS] Dev Studio backend regressions\n'

docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py check
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py makemigrations --check --dry-run
printf '[PASS] Django checks and migration drift\n'

docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py dev_studio_audit
docker compose --ansi never -f "$TEST_COMPOSE" run --rm backend-test python manage.py dev_studio_runtime_audit
printf '[PASS] Dev Studio audits\n'

DOCKER_BUILDKIT=1 docker build --progress=plain \
  -f backend/Dockerfile.sandbox \
  -t ai-workspace-dev-sandbox-check backend
printf '[PASS] Dedicated sandbox image build\n'

DOCKER_BUILDKIT=1 docker build --progress=plain \
  --target builder \
  --build-arg NEXT_PUBLIC_SITE_URL=http://127.0.0.1:39001 \
  -t ai-workspace-dev-frontend-check frontend

docker run --rm ai-workspace-dev-frontend-check sh -c 'npm run lint'
printf '[PASS] Dev Studio frontend build/lint\n'

printf '\nDEV_STUDIO_V2_CHECK=PASS\n'
