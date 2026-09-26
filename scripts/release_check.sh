#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_COMPOSE="${PROJECT_DIR}/docker-compose.test.yml"
PROD_COMPOSE="${PROJECT_DIR}/docker-compose.prod.yml"
FRONTEND_SMOKE_CONTAINER="ai-workspace-frontend-smoke-$$"
FRONTEND_SMOKE_PORT="${FRONTEND_SMOKE_PORT:-39001}"
cd "$PROJECT_DIR"

cleanup() {
  docker rm -f "$FRONTEND_SMOKE_CONTAINER" >/dev/null 2>&1 || true
  docker compose -f "$TEST_COMPOSE" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

printf '[1/17] Secret scan\n'
bash ./scripts/security_scan.sh

printf '[2/17] Shell and Python syntax\n'
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

printf '[3/17] Compose syntax\n'
docker compose -f "$TEST_COMPOSE" config >/dev/null
APP_DOMAIN=release-check.example.test \
ACME_EMAIL=ops@example.test \
POSTGRES_DB=release_check \
POSTGRES_USER=release_check \
POSTGRES_PASSWORD=release-check-password \
REDIS_PASSWORD=release-check-redis-password \
PUBLIC_API_URL=https://release-check.example.test/api/v1 \
docker compose --env-file .env.example -f "$PROD_COMPOSE" config >/dev/null

printf '[4/17] Build isolated test stack\n'
docker compose -f "$TEST_COMPOSE" build backend-test
docker compose -f "$TEST_COMPOSE" up -d postgres

printf '[5/17] Backend blocking lint\n'
# Block only defects that can make Python invalid or reference undefined names.
# Full-repository style/import debt is reported below but must not make an
# otherwise safe production rollback loop impossible to escape.
docker compose -f "$TEST_COMPOSE" run --rm backend-test \
  ruff check . --select E9,F63,F7,F82

printf '[6/17] Backend lint debt report (non-blocking)\n'
if ! docker compose -f "$TEST_COMPOSE" run --rm backend-test ruff check .; then
  printf '[WARN] Full Ruff debt remains. Blocking correctness rules already passed; clean style debt incrementally.\n' >&2
fi

printf '[7/17] Backend tests on PostgreSQL/pgvector\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test pytest -q

printf '[8/17] Sprint 68 tenant/security regressions\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test \
  pytest -q apps/agents/test_tenant_security.py apps/agents/test_webhook_hardening.py apps/agents/test_webhooks.py

printf '[9/17] Django checks and migration drift\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py check
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py makemigrations --check --dry-run

printf '[10/17] Economic safety invariants\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py economic_safety_check

printf '[11/17] Billing ledger integrity\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py billing_integrity_check

printf '[12/17] Agent Studio integrity and tenant isolation\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py agent_system_audit
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py agent_webhook_audit
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py agent_security_audit

printf '[13/17] Dev Studio readiness\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py dev_studio_audit

printf '[14/17] Agent Runtime billing integrity\n'
docker compose -f "$TEST_COMPOSE" run --rm backend-test python manage.py agent_billing_audit

printf '[15/17] Frontend production build\n'
docker build \
  --target builder \
  --build-arg NEXT_PUBLIC_SITE_URL=http://127.0.0.1:${FRONTEND_SMOKE_PORT} \
  -t ai-workspace-frontend-builder frontend
docker build \
  --build-arg NEXT_PUBLIC_SITE_URL=http://127.0.0.1:${FRONTEND_SMOKE_PORT} \
  -t ai-workspace-frontend-test frontend

printf '[16/17] Frontend lint\n'
docker run --rm ai-workspace-frontend-builder sh -c 'npm run lint'

printf '[17/17] Frontend runtime route smoke\n'
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
for route in / /pricing /faq /login /register /api /use-cases/marketing /app /app/account /app/wallet /app/usage /app/settings /app/projects /app/projects/00000000-0000-0000-0000-000000000000/github /app/help /app/notifications /app/images /app/compare /app/agents /app/teams /app/schedules /app/events /app/dev /app/runs /app/runs/00000000-0000-0000-0000-000000000000 /admin-console /admin-console/system /admin-console/providers /admin-console/finance /admin-console/security /admin-console/operations /admin-console/drills /admin-console/compliance /sitemap.xml /robots.txt; do
  curl -fsS --max-time 5 "http://127.0.0.1:${FRONTEND_SMOKE_PORT}${route}" >/dev/null || {
    echo "Frontend route failed: ${route}" >&2
    exit 1
  }
done

printf 'RELEASE CHECK: PASS\n'
