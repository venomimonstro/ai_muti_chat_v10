#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_INSTALLER="${PROJECT_DIR}/install.sh"
RUNTIME_INSTALLER="${PROJECT_DIR}/.install-ip-runtime.sh"
ENV_FILE="${PROJECT_DIR}/.env.production"

fail(){ printf 'Ошибка IP-установки: %s\n' "$1" >&2; exit 1; }
[[ "${EUID}" -eq 0 ]] || fail "запустите через sudo"
[[ -f "${BASE_INSTALLER}" ]] || fail "install.sh не найден"
[[ -f "${PROJECT_DIR}/deploy/Caddyfile.ip" ]] || fail "deploy/Caddyfile.ip не найден"

SERVER_IP="${AIWS_SERVER_IP:-}"
if [[ -z "${SERVER_IP}" ]]; then
  SERVER_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')"
fi
if [[ -z "${SERVER_IP}" ]]; then
  SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi
[[ "${SERVER_IP}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || fail "не удалось определить IPv4 сервера; задайте AIWS_SERVER_IP вручную"
IFS=. read -r a b c d <<<"${SERVER_IP}"
for octet in "$a" "$b" "$c" "$d"; do (( octet >= 0 && octet <= 255 )) || fail "некорректный IPv4: ${SERVER_IP}"; done

printf 'IP-режим: приложение будет доступно по http://%s без домена и TLS.\n' "${SERVER_IP}"
printf 'Это режим первичной настройки/тестирования. Перед коммерческим запуском подключите домен и HTTPS.\n\n'

cp "${BASE_INSTALLER}" "${RUNTIME_INSTALLER}"
chmod 700 "${RUNTIME_INSTALLER}"

python3 - "${RUNTIME_INSTALLER}" "${SERVER_IP}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
ip = sys.argv[2]
s = path.read_text()

old_domain = '''else
  printf 'До запуска направьте A/AAAA-запись домена на этот сервер.\\n\\n'
  APP_DOMAIN="$(required_or_prompt "${AIWS_DOMAIN:-}" "Домен без https:// (например ai.example.ru)")"
  valid_domain "${APP_DOMAIN}" || fail "некорректный домен"
  ACME_EMAIL="$(required_or_prompt "${AIWS_ACME_EMAIL:-}" "Email для HTTPS-сертификата")"
  valid_email "${ACME_EMAIL}" || fail "некорректный email"
fi'''
new_domain = f'''else
  APP_DOMAIN="{ip}"
  ACME_EMAIL=""
  printf 'IP-режим: домен пока не используется. Адрес: http://%s\\n\\n' "${{APP_DOMAIN}}"
fi'''
if old_domain not in s:
    raise SystemExit('Не найден блок настройки домена в install.sh')
s = s.replace(old_domain, new_domain, 1)

# Small VPS / compact disk profile for temporary IP deployments.
s = s.replace(
    '''  if (( disk_kb < 10000000 )); then\n    fail "нужно минимум около 10 ГБ свободного диска"\n  fi''',
    '''  if (( disk_kb < 3200000 )); then\n    fail "для IP/SMALL-VPS режима нужно минимум около 3.2 ГБ свободного диска"\n  elif (( disk_kb < 6000000 )); then\n    LOW_MEMORY_MODE=true\n    UVICORN_WORKERS=1\n    CELERY_CONCURRENCY=1\n    export COMPOSE_PARALLEL_LIMIT=1\n    printf 'IP/LOW-DISK режим: последовательная сборка и 1 worker.\\n' >&2\n  fi''',
    1,
)

# HTTP/IP mode must not force HTTPS cookies or redirects.
s = s.replace('DJANGO_SECURE_SSL_REDIRECT=true', 'DJANGO_SECURE_SSL_REDIRECT=false')
s = s.replace('DJANGO_SESSION_COOKIE_SECURE=true', 'DJANGO_SESSION_COOKIE_SECURE=false')
s = s.replace('DJANGO_CSRF_COOKIE_SECURE=true', 'DJANGO_CSRF_COOKIE_SECURE=false')
s = s.replace('DJANGO_SECURE_HSTS_SECONDS=31536000', 'DJANGO_SECURE_HSTS_SECONDS=0')

# All generated public URLs are HTTP until a domain is connected.
s = s.replace('https://%s', 'http://%s')
s = s.replace('https://${APP_DOMAIN}', 'http://${APP_DOMAIN}')

# Tell compose to mount the HTTP-only Caddy config.
needle = "printf 'APP_DOMAIN=%s\\nACME_EMAIL=%s\\n' \"${APP_DOMAIN}\" \"${ACME_EMAIL}\""
replacement = "printf 'APP_DOMAIN=%s\\nACME_EMAIL=%s\\nCADDY_CONFIG_FILE=Caddyfile.ip\\n' \"${APP_DOMAIN}\" \"${ACME_EMAIL}\""
if needle not in s:
    raise SystemExit('Не найден блок APP_DOMAIN/ACME_EMAIL в install.sh')
s = s.replace(needle, replacement, 1)

path.write_text(s)
PY

# Если предыдущая попытка уже создала env, переводим незавершённую установку
# в IP-режим без удаления секретов/БД.
if [[ -f "${ENV_FILE}" && ! -f "${PROJECT_DIR}/.installed" ]]; then
  upsert(){
    local key="$1" value="$2"
    if grep -q "^${key}=" "${ENV_FILE}"; then
      sed -i "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
    else
      printf '%s=%s\n' "${key}" "${value}" >>"${ENV_FILE}"
    fi
  }
  upsert APP_DOMAIN "${SERVER_IP}"
  upsert ACME_EMAIL ""
  upsert CADDY_CONFIG_FILE "Caddyfile.ip"
  upsert DJANGO_ALLOWED_HOSTS "${SERVER_IP}"
  upsert DJANGO_SECURE_SSL_REDIRECT "false"
  upsert DJANGO_SESSION_COOKIE_SECURE "false"
  upsert DJANGO_CSRF_COOKIE_SECURE "false"
  upsert DJANGO_SECURE_HSTS_SECONDS "0"
  upsert CORS_ALLOWED_ORIGINS "http://${SERVER_IP}"
  upsert PUBLIC_API_URL "http://${SERVER_IP}/api/v1"
  upsert NEXT_PUBLIC_API_URL "http://${SERVER_IP}/api/v1"
  upsert NEXT_PUBLIC_SITE_URL "http://${SERVER_IP}"
  upsert FRONTEND_PUBLIC_URL "http://${SERVER_IP}"
  upsert PAYMENT_RETURN_URL "http://${SERVER_IP}/app/wallet/return"
fi

mem_kb="$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
if (( mem_kb > 0 && mem_kb < 3000000 )); then
  export UVICORN_WORKERS=1
  export CELERY_CONCURRENCY=1
  export COMPOSE_PARALLEL_LIMIT=1
fi

bash -n "${RUNTIME_INSTALLER}" || fail "runtime installer содержит синтаксическую ошибку"
exec bash "${RUNTIME_INSTALLER}"
