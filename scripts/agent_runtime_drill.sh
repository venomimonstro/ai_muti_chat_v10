#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
cd "$PROJECT_DIR"

[[ "${EUID}" -eq 0 ]] || { echo 'Run with sudo/root' >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo '.env.production not found' >&2; exit 1; }

compose() {
  docker compose --ansi never --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
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

wait_healthy_if_defined() {
  local service="$1" container_id health
  container_id="$(compose ps -q "$service")"
  [[ -n "$container_id" ]] || { echo "Container missing: $service" >&2; return 1; }
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
  if [[ "$health" == "none" ]]; then
    return 0
  fi
  for _attempt in $(seq 1 30); do
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
    [[ "$health" == "healthy" ]] && return 0
    [[ "$health" == "unhealthy" ]] && { echo "Service unhealthy: $service" >&2; return 1; }
    sleep 2
  done
  echo "Service health timeout: $service ($health)" >&2
  return 1
}

assert_celery_worker_responds() {
  local output
  output="$(compose exec -T worker celery -A config inspect ping --timeout=10 2>&1)" || {
    printf '%s\n' "$output" >&2
    echo 'Celery worker ping command failed' >&2
    return 1
  }
  if ! grep -qi 'pong' <<<"$output"; then
    printf '%s\n' "$output" >&2
    echo 'Celery worker did not return pong' >&2
    return 1
  fi
  echo '[PASS] Celery worker responds through broker.'
}

assert_beat_process() {
  local container_id
  container_id="$(compose ps -q beat)"
  [[ -n "$container_id" ]] || { echo 'Celery beat container is missing' >&2; return 1; }
  if ! docker top "$container_id" -eo args 2>/dev/null | grep -Eq '[c]elery .* beat|[c]elery -A config beat'; then
    echo 'Celery beat process is not running' >&2
    return 1
  fi
  echo '[PASS] Celery beat process is running.'
}

assert_web_search() {
  compose exec -T backend python -c "import json,urllib.parse,urllib.request; u='http://searxng:8080/search?'+urllib.parse.urlencode({'q':'runtime drill','format':'json','safesearch':1}); d=json.load(urllib.request.urlopen(u,timeout=8)); assert isinstance(d.get('results'),list)" >/dev/null
  echo '[PASS] SearXNG is reachable from backend.'
}

assert_no_release_test_stack() {
  local leftovers
  leftovers="$(docker ps -a --filter 'name=ai-workspace-release-test' --format '{{.Names}}' | sed '/^$/d')"
  if [[ -n "$leftovers" ]]; then
    printf 'Stale release-test containers detected:\n%s\n' "$leftovers" >&2
    return 1
  fi
  echo '[PASS] No stale release-test containers.'
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

printf '[1/5] Baseline production health and audits\n'
assert_no_release_test_stack
for service in postgres redis searxng backend worker beat frontend caddy sandbox; do
  wait_running "$service"
  wait_healthy_if_defined "$service"
done
assert_celery_worker_responds
assert_beat_process
assert_web_search
run_agent_audits

printf '[2/5] Worker restart drill\n'
compose restart worker
wait_running worker
assert_celery_worker_responds
run_agent_audits

printf '[3/5] Beat restart drill\n'
compose restart beat
wait_running beat
assert_beat_process
run_agent_audits

printf '[4/5] Recovery/readiness checks\n'
compose exec -T backend python manage.py agent_recovery_audit
compose exec -T backend python -c "import os,urllib.request; req=urllib.request.Request('http://127.0.0.1:8000/api/v1/readiness/',headers={'Host':os.environ.get('APP_DOMAIN','localhost'),'X-Forwarded-Proto':'https'}); urllib.request.urlopen(req,timeout=5).read()"
compose exec -T sandbox python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=5).read()"
assert_web_search

printf '[5/5] Final service and process verification\n'
for service in postgres redis searxng backend worker beat frontend caddy sandbox; do
  wait_running "$service"
  wait_healthy_if_defined "$service"
done
assert_celery_worker_responds
assert_beat_process
assert_no_release_test_stack

printf 'AGENT RUNTIME DRILL: PASS\n'
