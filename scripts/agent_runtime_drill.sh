#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
cd "$PROJECT_DIR"

[[ -f "$ENV_FILE" ]] || { echo '.env.production not found' >&2; exit 1; }

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

wait_running() {
  local service="$1"
  for _attempt in $(seq 1 30); do
    if compose ps --status running --services | grep -Fxq "$service"; then
      return 0
    fi
    sleep 2
  done
  echo "Service did not recover: $service" >&2
  return 1
}

run_agent_audits() {
  compose exec -T backend python manage.py agent_system_audit
  compose exec -T backend python manage.py agent_webhook_audit
  compose exec -T backend python manage.py agent_security_audit
  compose exec -T backend python manage.py agent_commercial_limits_audit
  compose exec -T backend python manage.py agent_recovery_audit
  compose exec -T backend python manage.py dev_studio_audit
  compose exec -T backend python manage.py agent_billing_audit
  compose exec -T backend python manage.py connection_health_audit
}

printf '[1/4] Baseline audits\n'
run_agent_audits

printf '[2/4] Worker restart drill\n'
compose restart worker
wait_running worker
run_agent_audits

printf '[3/4] Beat restart drill\n'
compose restart beat
wait_running beat
run_agent_audits

printf '[4/4] Final service/readiness check\n'
for service in postgres redis backend worker beat frontend caddy sandbox; do
  compose ps --status running --services | grep -Fxq "$service" || {
    echo "Required service is not running: $service" >&2
    exit 1
  }
done
compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/', timeout=5)"

printf 'AGENT RUNTIME DRILL: PASS\n'
