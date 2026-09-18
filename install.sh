#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
ENV_FILE="${PROJECT_DIR}/.env.production"
INSTALL_MARKER="${PROJECT_DIR}/.installed"
NONINTERACTIVE="${AIWS_NONINTERACTIVE:-false}"

fail() { printf 'Ошибка установки: %s\n' "$1" >&2; exit 1; }
on_error() { printf '\nУстановка остановлена на строке %s. Данные и volumes не удалялись.\n' "$1" >&2; }
trap 'on_error "$LINENO"' ERR

[[ "${EUID}" -eq 0 ]] || fail "запустите через sudo"
[[ -f "${COMPOSE_FILE}" ]] || fail "docker-compose.prod.yml не найден"

RESUME=false
if [[ -e "${ENV_FILE}" ]]; then
  if [[ -e "${INSTALL_MARKER}" ]]; then
    fail "система уже установлена. Для обновления используйте sudo bash scripts/update.sh"
  fi
  RESUME=true
fi

install_packages() {
  command -v apt-get >/dev/null 2>&1 || fail "автоустановка поддерживает Ubuntu/Debian с apt"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl openssl iproute2
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    systemctl enable --now docker >/dev/null 2>&1 || true
    docker info >/dev/null 2>&1 || fail "Docker установлен, но daemon недоступен"
    return
  fi
  apt-get install -y docker.io
  if ! docker compose version >/dev/null 2>&1; then
    apt-get install -y docker-compose-v2 || apt-get install -y docker-compose-plugin
  fi
  systemctl enable --now docker
  docker compose version >/dev/null 2>&1 || fail "не удалось установить Docker Compose v2"
  docker info >/dev/null 2>&1 || fail "Docker daemon не запустился"
}

check_server() {
  local mem_kb disk_kb
  mem_kb="$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  disk_kb="$(df -Pk "${PROJECT_DIR}" | awk 'NR==2{print $4}')"
  if (( mem_kb > 0 && mem_kb < 2000000 )); then
    fail "нужно минимум около 2 ГБ RAM; найдено $((mem_kb/1024)) МБ"
  fi
  if (( mem_kb > 0 && mem_kb < 4000000 )); then
    printf 'Предупреждение: RAM меньше рекомендуемых 4 ГБ. Для небольшой beta это допустимо, но следите за нагрузкой.\n' >&2
  fi
  if (( disk_kb < 10000000 )); then
    fail "нужно минимум около 10 ГБ свободного диска"
  fi
  if [[ "${RESUME}" != true ]]; then
    if ss -ltn '( sport = :80 or sport = :443 )' 2>/dev/null | tail -n +2 | grep -q .; then
      fail "порты 80/443 уже заняты. Освободите их перед установкой Caddy"
    fi
  fi
}

prompt_required() {
  local label="$1" value=""
  if [[ "${NONINTERACTIVE}" == "true" ]]; then return 1; fi
  while [[ -z "${value}" ]]; do read -r -p "${label}: " value; done
  printf '%s' "${value}"
}
valid_domain() { [[ "$1" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]; }
valid_email() { [[ "$1" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; }
required_or_prompt() {
  local env_value="$1" label="$2"
  if [[ -n "${env_value}" ]]; then printf '%s' "${env_value}"; return; fi
  prompt_required "${label}" || fail "${label}: задайте значение через AIWS_* переменную для non-interactive установки"
}

install_packages
install_docker
check_server
command -v openssl >/dev/null 2>&1 || fail "openssl не установлен"

printf '\nAI Workspace — автоматическая установка\n'
if [[ "${RESUME}" == true ]]; then
  APP_DOMAIN="$(sed -n 's/^APP_DOMAIN=//p' "${ENV_FILE}" | head -n 1)"
  [[ -n "${APP_DOMAIN}" ]] || fail "в .env.production отсутствует APP_DOMAIN"
  ACME_EMAIL="$(sed -n 's/^ACME_EMAIL=//p' "${ENV_FILE}" | head -n 1)"
  printf 'Продолжаем прерванную установку для %s.\n\n' "${APP_DOMAIN}"
else
  printf 'До запуска направьте A/AAAA-запись домена на этот сервер.\n\n'
  APP_DOMAIN="$(required_or_prompt "${AIWS_DOMAIN:-}" "Домен без https:// (например ai.example.ru)")"
  valid_domain "${APP_DOMAIN}" || fail "некорректный домен"
  ACME_EMAIL="$(required_or_prompt "${AIWS_ACME_EMAIL:-}" "Email для HTTPS-сертификата")"
  valid_email "${ACME_EMAIL}" || fail "некорректный email"
fi

ADMIN_USERNAME="$(required_or_prompt "${AIWS_ADMIN_USERNAME:-}" "Логин администратора")"
[[ "${ADMIN_USERNAME}" =~ ^[A-Za-z0-9_.@+-]{3,150}$ ]] || fail "некорректный логин администратора"
ADMIN_EMAIL="$(required_or_prompt "${AIWS_ADMIN_EMAIL:-}" "Email администратора")"
valid_email "${ADMIN_EMAIL}" || fail "некорректный email администратора"
ADMIN_PASSWORD="${AIWS_ADMIN_PASSWORD:-}"
GENERATED_ADMIN_PASSWORD=false
if [[ -z "${ADMIN_PASSWORD}" && "${NONINTERACTIVE}" != "true" ]]; then
  read -r -s -p "Пароль администратора (минимум 12 символов; Enter — сгенерировать): " ADMIN_PASSWORD
  printf '\n'
fi
if [[ -z "${ADMIN_PASSWORD}" ]]; then ADMIN_PASSWORD="$(openssl rand -hex 16)"; GENERATED_ADMIN_PASSWORD=true; fi
[[ "${#ADMIN_PASSWORD}" -ge 12 ]] || fail "пароль администратора короче 12 символов"

if [[ "${RESUME}" != true ]]; then
  POSTGRES_PASSWORD="$(openssl rand -hex 32)"
  REDIS_PASSWORD="$(openssl rand -hex 32)"
  DJANGO_SECRET_KEY="$(openssl rand -base64 64 | tr -d '\n')"
  B2B_API_KEY_PEPPER="$(openssl rand -base64 64 | tr -d '\n')"
  MFA_ENCRYPTION_KEY="$(openssl rand -base64 64 | tr -d '\n')"
  MFA_RECOVERY_PEPPER="$(openssl rand -base64 64 | tr -d '\n')"
  umask 077
  {
    printf 'APP_DOMAIN=%s\n' "${APP_DOMAIN}"
    printf 'ACME_EMAIL=%s\n' "${ACME_EMAIL}"
    printf 'POSTGRES_DB=aiworkspace\nPOSTGRES_USER=aiworkspace\n'
    printf 'POSTGRES_PASSWORD=%s\nREDIS_PASSWORD=%s\n' "${POSTGRES_PASSWORD}" "${REDIS_PASSWORD}"
    printf 'DATABASE_URL=postgresql://aiworkspace:%s@postgres:5432/aiworkspace\n' "${POSTGRES_PASSWORD}"
    printf 'REDIS_URL=redis://:%s@redis:6379/0\nCACHE_URL=redis://:%s@redis:6379/1\n' "${REDIS_PASSWORD}" "${REDIS_PASSWORD}"
    printf 'DJANGO_SECRET_KEY=%s\nB2B_API_KEY_PEPPER=%s\nMFA_ENCRYPTION_KEY=%s\nMFA_RECOVERY_PEPPER=%s\n' "${DJANGO_SECRET_KEY}" "${B2B_API_KEY_PEPPER}" "${MFA_ENCRYPTION_KEY}" "${MFA_RECOVERY_PEPPER}"
    printf 'SYSTEM_ISSUE_LOG_FILE=/app/logs/system_issues.jsonl\n'
    printf 'LEGAL_DOC_VERSION=2026-09-18\nLEGAL_NAME=\nLEGAL_TAX_ID=\nLEGAL_ADDRESS=\nLEGAL_CONTACT_EMAIL=\nLEGAL_EFFECTIVE_DATE=\n'
    printf 'DJANGO_DEBUG=false\nDJANGO_ALLOWED_HOSTS=%s\n' "${APP_DOMAIN}"
    printf 'DJANGO_SECURE_SSL_REDIRECT=true\nDJANGO_SESSION_COOKIE_SECURE=true\nDJANGO_CSRF_COOKIE_SECURE=true\nDJANGO_SECURE_HSTS_SECONDS=31536000\nDJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=false\nDJANGO_SECURE_HSTS_PRELOAD=false\nDJANGO_TRUST_PROXY_SSL_HEADER=true\n'
    printf 'CORS_ALLOWED_ORIGINS=https://%s\nPUBLIC_API_URL=https://%s/api/v1\nNEXT_PUBLIC_API_URL=https://%s/api/v1\nNEXT_PUBLIC_SITE_URL=https://%s\nFRONTEND_PUBLIC_URL=https://%s\n' "${APP_DOMAIN}" "${APP_DOMAIN}" "${APP_DOMAIN}" "${APP_DOMAIN}" "${APP_DOMAIN}"
    printf 'PAYMENT_RETURN_URL=https://%s/app/wallet/return\n' "${APP_DOMAIN}"
    printf 'EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend\nEMAIL_HOST=\nEMAIL_PORT=587\nEMAIL_HOST_USER=\nEMAIL_HOST_PASSWORD=\nEMAIL_USE_TLS=true\nEMAIL_USE_SSL=false\nDEFAULT_FROM_EMAIL=noreply@%s\n' "${APP_DOMAIN}"
    printf 'B2B_TRUST_PROXY_IP_HEADER=true\nADMIN_MFA_ENFORCED=false\nPAYMENTS_ENABLED=false\nPAYMENTS_LIVE_ENABLED=false\nPAYMENTS_FISCALIZATION_MODE=disabled\n'
    printf 'OPENAI_API_KEY=\nOPENAI_DEFAULT_MODEL=\nANTHROPIC_API_KEY=\nANTHROPIC_DEFAULT_MODEL=\nDEEPSEEK_API_KEY=\nDEEPSEEK_DEFAULT_MODEL=\nGEMINI_API_KEY=\nGEMINI_DEFAULT_MODEL=\nXAI_API_KEY=\nXAI_DEFAULT_MODEL=\n'
  } >"${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
fi

compose() { docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"; }
cd "${PROJECT_DIR}"
printf '\n[1/7] Сборка контейнеров...\n'
compose build --pull
printf '[2/7] Запуск PostgreSQL и Redis...\n'
compose up -d postgres redis
printf '[3/7] Миграции базы данных...\n'
compose run --rm backend python manage.py migrate --noinput
printf '[4/7] Проверка Django и статических файлов...\n'
compose run --rm backend python manage.py check --fail-level ERROR
compose run --rm backend python manage.py collectstatic --noinput
printf '[5/7] Начальная конфигурация каталога и администратора...\n'
compose run --rm backend python manage.py bootstrap_catalog
compose run --rm -e "AIWORKSPACE_ADMIN_PASSWORD=${ADMIN_PASSWORD}" backend python manage.py bootstrap_admin --username "${ADMIN_USERNAME}" --email "${ADMIN_EMAIL}" --reset-password
printf '[6/7] Запуск приложения...\n'
compose up -d --remove-orphans
printf '[7/7] Проверка HTTPS и readiness...\n'
READY=false
for _attempt in $(seq 1 90); do
  if curl -fsS --max-time 5 "https://${APP_DOMAIN}/api/v1/readiness/" >/dev/null 2>&1; then READY=true; break; fi
  sleep 2
done

if [[ "${READY}" == true ]]; then
  touch "${INSTALL_MARKER}"; chmod 600 "${INSTALL_MARKER}"
  printf '\nУстановка завершена успешно.\n'
  printf 'Сайт: https://%s\nЛичный кабинет: https://%s/app\nПанель администратора: https://%s/admin-console\nСостояние системы: https://%s/admin-console/system\nДвухфакторная защита: https://%s/security/mfa\n' "${APP_DOMAIN}" "${APP_DOMAIN}" "${APP_DOMAIN}" "${APP_DOMAIN}" "${APP_DOMAIN}"
  printf 'Логин администратора: %s\n' "${ADMIN_USERNAME}"
  if [[ "${GENERATED_ADMIN_PASSWORD}" == true ]]; then
    printf 'Сгенерированный пароль: %s\nСохраните его сейчас — повторно он не выводится.\n' "${ADMIN_PASSWORD}"
  fi
  printf '\nДиагностика: sudo bash scripts/system_diagnostics.sh\n'
  printf 'Проверка коммерческого запуска: sudo bash scripts/commercial_launch_check.sh\n'
else
  printf '\nКонтейнеры запущены, но HTTPS/readiness не прошли. Установка не помечена завершённой.\n' >&2
  printf 'Проверьте DNS и журнал: sudo docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=200 caddy backend frontend\n' >&2
  printf 'После исправления повторите: sudo bash install.sh\n' >&2
  exit 1
fi

printf '\nДо коммерческого запуска обязательно заполните SMTP, реквизиты продавца, AI API-ключи и цены, YooKassa, включите MFA и закройте юридические проверки/drills.\n'
