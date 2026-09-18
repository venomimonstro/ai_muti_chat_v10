#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

[[ "${1:-}" == "--confirm-chaos" ]] || {
  echo "Refusing to restart production services without --confirm-chaos" >&2
  exit 2
}
[[ -f "$ENV_FILE" ]] || { echo ".env.production not found" >&2; exit 3; }

compose() {
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

wait_ready() {
  local attempts=0
  until compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/', timeout=3)" >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    [[ $attempts -lt 40 ]] || { echo "Readiness did not recover" >&2; return 1; }
    sleep 2
  done
}

wait_worker() {
  local attempts=0
  until compose exec -T worker celery -A config inspect ping --timeout=3 2>/dev/null | grep -q 'pong'; do
    attempts=$((attempts + 1))
    [[ $attempts -lt 40 ]] || { echo "Celery worker did not recover" >&2; return 1; }
    sleep 2
  done
}

wait_redis() {
  local attempts=0
  until compose exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" ping' 2>/dev/null | grep -q PONG; do
    attempts=$((attempts + 1))
    [[ $attempts -lt 40 ]] || { echo "Redis did not recover" >&2; return 1; }
    sleep 2
  done
}

cd "$PROJECT_DIR"
echo '[chaos] worker restart'
compose restart worker
wait_worker
wait_ready

echo '[chaos] redis restart'
compose restart redis
wait_redis
wait_worker
wait_ready

echo '[chaos] backend restart'
compose restart backend
wait_ready

echo '[chaos] stale-operation recovery'
compose exec -T backend python manage.py recover_stale_operations

echo '[chaos] financial invariants'
compose exec -T backend python manage.py verify_financial_invariants

echo 'CHAOS SMOKE: PASS'
