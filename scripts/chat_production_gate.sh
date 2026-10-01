#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

cd "$PROJECT_DIR"
[[ "${EUID}" -eq 0 ]] || { echo 'Запустите через sudo: sudo bash scripts/chat_production_gate.sh' >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo '.env.production не найден' >&2; exit 1; }

compose() {
  docker compose --ansi never --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$ENV_FILE" | head -n 1
}

echo '============================================================'
echo '[1/5] FULL production update + release gates'
echo '============================================================'
bash scripts/update.sh --full

echo '============================================================'
echo '[2/5] Production configuration + runtime dependencies'
echo '============================================================'
# Run Django system checks against the actual production environment. This catches
# unsafe local cache/procurement settings that an isolated test stack cannot prove.
compose exec -T backend python manage.py check
compose exec -T backend python - <<'PY'
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone as dt_timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from django.core.cache import cache
from apps.admin_ops.tasks import WORKER_HEARTBEAT_KEY

with urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/', timeout=8) as response:
    if response.status != 200:
        raise SystemExit(f'readiness HTTP {response.status}')
print('CHAT_PRODUCTION_READINESS_OK')

# Provider/model recovery, stale-generation cleanup and billing safety depend on
# Celery beat + worker. Wait for a fresh heartbeat after the deployment rather than
# letting a dead background plane pass the chat release gate.
deadline = time.monotonic() + 75
heartbeat = None
while time.monotonic() < deadline:
    heartbeat = cache.get(WORKER_HEARTBEAT_KEY)
    if heartbeat:
        try:
            parsed = datetime.fromisoformat(str(heartbeat))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt_timezone.utc)
            age = (datetime.now(dt_timezone.utc) - parsed.astimezone(dt_timezone.utc)).total_seconds()
            if 0 <= age <= 180:
                print(f'CHAT_WORKER_HEARTBEAT_OK age_seconds={age:.1f}')
                break
        except (TypeError, ValueError):
            pass
    time.sleep(5)
else:
    raise SystemExit(f'CHAT_WORKER_HEARTBEAT_STALE value={heartbeat!r}')

# SearXNG is the required fresh-information path for the customer chat. A process
# being up is not enough: prove that a real JSON search currently returns results.
base = os.getenv('WEB_SEARCH_BASE_URL', 'http://searxng:8080').rstrip('/')
query = urllib.parse.urlencode({'q': 'OpenAI', 'format': 'json'})
with urllib.request.urlopen(f'{base}/search?{query}', timeout=10) as response:
    if response.status != 200:
        raise SystemExit(f'CHAT_SEARCH_HTTP_{response.status}')
    payload = json.loads(response.read().decode('utf-8'))
results = payload.get('results') if isinstance(payload, dict) else None
if not isinstance(results, list) or not results:
    raise SystemExit('CHAT_SEARCH_NO_RESULTS')
print(f'CHAT_SEARCH_OK results={len(results)}')
PY

echo '============================================================'
echo '[3/5] Real production AUTO customer preflight'
echo '============================================================'
# This creates an ephemeral synthetic user/wallet/conversation inside a rollback-only
# transaction, then executes the same preview -> AUTO router -> pricing -> procurement
# reserve -> customer reserve -> Generation path as the real chat. No provider call is
# made and no test data or wallet mutation survives the command.
compose exec -T backend python manage.py chat_preflight_smoke --mode auto

echo '============================================================'
echo '[4/5] Live inference for every customer-visible model'
echo '============================================================'
# Direct live inference is complementary to the synthetic customer preflight above:
# preflight proves the commercial/chat path, while this proves every model currently
# advertised to users can actually answer through its production credential.
compose exec -T backend python manage.py chat_runtime_check --live

echo '============================================================'
echo '[5/5] Full customer HTTP journey when E2E account is configured'
echo '============================================================'
E2E_USERNAME="$(env_value E2E_USERNAME)"
E2E_PASSWORD="$(env_value E2E_PASSWORD)"
APP_DOMAIN="$(env_value APP_DOMAIN)"
if [[ -n "$E2E_USERNAME" && -n "$E2E_PASSWORD" && -n "$APP_DOMAIN" ]]; then
  compose run --rm -T \
    -e "E2E_BASE_URL=https://${APP_DOMAIN}" \
    -e "E2E_USERNAME=${E2E_USERNAME}" \
    -e "E2E_PASSWORD=${E2E_PASSWORD}" \
    -v "${PROJECT_DIR}/scripts:/smoke:ro" \
    backend python /smoke/commercial_http_smoke.py
  echo 'CHAT_CUSTOMER_E2E_OK'
else
  echo '[WARN] E2E_USERNAME/E2E_PASSWORD не настроены: внешний browser/HTTP smoke пропущен.'
  echo '[INFO] Production config, worker recovery, fresh search, AUTO customer preflight и live inference уже обязательны и пройдены.'
fi

echo '============================================================'
echo 'CHAT PRODUCTION GATE: PASS'
echo '============================================================'
