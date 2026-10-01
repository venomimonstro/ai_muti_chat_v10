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
echo '[1/4] FULL production update + release gates'
echo '============================================================'
bash scripts/update.sh --full

echo '============================================================'
echo '[2/4] Production readiness'
echo '============================================================'
compose exec -T backend python - <<'PY'
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/', timeout=8) as response:
    if response.status != 200:
        raise SystemExit(f'readiness HTTP {response.status}')
print('CHAT_PRODUCTION_READINESS_OK')
PY

echo '============================================================'
echo '[3/4] Live inference for every customer-visible model'
echo '============================================================'
compose exec -T backend python manage.py chat_runtime_check --live

echo '============================================================'
echo '[4/4] Full customer HTTP journey when E2E account is configured'
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
  echo '[WARN] E2E_USERNAME/E2E_PASSWORD не настроены: полный клиентский HTTP smoke пропущен.'
  echo '[WARN] Для максимального release assurance создайте отдельный verified E2E-аккаунт с небольшим положительным балансом.'
fi

echo '============================================================'
echo 'CHAT PRODUCTION GATE: PASS'
echo '============================================================'
