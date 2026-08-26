#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
ENV_FILE="${PROJECT_DIR}/.env.production"
INSTALL_MARKER="${PROJECT_DIR}/.installed"

fail() {
  printf 'Ошибка установки: %s\n' "$1" >&2
  exit 1
}

on_error() {
  printf 'Установка остановлена на строке %s. Данные не удалялись.\n' "$1" >&2
}
trap 'on_error "$LINENO"' ERR

if [[ "${EUID}" -ne 0 ]]; then
  fail "запустите команду через sudo: sudo ./install.sh"
fi
if [[ ! -f "${COMPOSE_FILE}" ]]; then
  fail "docker-compose.prod.yml не найден"
fi
RESUME=false
if [[ -e "${ENV_FILE}" ]]; then
  if [[ -e "${INSTALL_MARKER}" ]]; then
    fail "система уже установлена. Для обновления используйте sudo ./scripts/update.sh"
  fi
  RESUME=true
fi

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  command -v apt-get >/dev/null 2>&1 || fail "автоустановка поддерживает Ubuntu/Debian с apt"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y ca-certificates curl openssl docker.io
  if ! docker compose version >/dev/null 2>&1; then
    apt-get install -y docker-compose-v2 || apt-get install -y docker-compose-plugin
  fi
  systemctl enable --now docker
  docker compose version >/dev/null 2>&1 || fail "не удалось установить Docker Compose v2"
}

prompt_required() {
  local label="$1"
  local value=""
  while [[ -z "${value}" ]]; do
    read -r -p "${label}: " value
  done
  printf '%s' "${value}"
}

valid_domain() {
  [[ "$1" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]
}

valid_email() {
  [[ "$1" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]
}

install_docker
command -v openssl >/dev/null 2>&1 || fail "openssl не установлен"

printf '\nAI Workspace - автоматическая установка\n'
if [[ "${RESUME}" == true ]]; then
  APP_DOMAIN="$(sed -n 's/^APP_DOMAIN=//p' "${ENV_FILE}" | head -n 1)"
  [[ -n "${APP_DOMAIN}" ]] || fail "в .env.production отсутствует APP_DOMAIN"
  printf 'Продолжаем прерванную установку для %s.\n\n' "${APP_DOMAIN}"
else
  printf 'Перед продолжением направьте A/AAAA-запись домена на этот сервер.\n\n'
  APP_DOMAIN="$(prompt_required "Домен без https:// (например ai.example.ru)")"
  valid_domain "${APP_DOMAIN}" || fail "некорректный домен"
  ACME_EMAIL="$(prompt_required "Email для сертификата HTTPS")"
  valid_email "${ACME_EMAIL}" || fail "некорректный email"
fi
ADMIN_USERNAME="$(prompt_required "Логин администратора")"
[[ "${ADMIN_USERNAME}" =~ ^[A-Za-z0-9_.@+-]{3,150}$ ]] || fail "некорректный логин"
ADMIN_EMAIL="$(prompt_required "Email администратора")"
valid_email "${ADMIN_EMAIL}" || fail "некорректный email администратора"

read -r -s -p "Пароль администратора (минимум 12 символов; Enter - сгенерировать): " ADMIN_PASSWORD
printf '\n'
GENERATED_ADMIN_PASSWORD=false
if [[ -z "${ADMIN_PASSWORD}" ]]; then
  ADMIN_PASSWORD="$(openssl rand -hex 16)"
  GENERATED_ADMIN_PASSWORD=true
fi
[[ "${#ADMIN_PASSWORD}" -ge 12 ]] || fail "пароль администратора короче 12 символов"

if [[ "${RESUME}" != true ]]; then
  POSTGRES_PASSWORD="$(openssl rand -hex 32)"
  REDIS_PASSWORD="$(openssl rand -hex 32)"
  DJANGO_SECRET_KEY="$(openssl rand -base64 64 | tr -d '\n')"
  B2B_API_KEY_PEPPER="$(openssl rand -base64 64 | tr -d '\n')"

  umask 077
  {
  printf 'APP_DOMAIN=%s\n' "${APP_DOMAIN}"
  printf 'ACME_EMAIL=%s\n' "${ACME_EMAIL}"
  printf 'POSTGRES_DB=aiworkspace\n'
  printf 'POSTGRES_USER=aiworkspace\n'
  printf 'POSTGRES_PASSWORD=%s\n' "${POSTGRES_PASSWORD}"
  printf 'REDIS_PASSWORD=%s\n' "${REDIS_PASSWORD}"
  printf 'DATABASE_URL=postgresql://aiworkspace:%s@postgres:5432/aiworkspace\n' "${POSTGRES_PASSWORD}"
  printf 'REDIS_URL=redis://:%s@redis:6379/0\n' "${REDIS_PASSWORD}"
  printf 'CACHE_URL=redis://:%s@redis:6379/1\n' "${REDIS_PASSWORD}"
  printf 'DJANGO_SECRET_KEY=%s\n' "${DJANGO_SECRET_KEY}"
  printf 'B2B_API_KEY_PEPPER=%s\n' "${B2B_API_KEY_PEPPER}"
  printf 'DJANGO_DEBUG=false\n'
  printf 'DJANGO_ALLOWED_HOSTS=%s\n' "${APP_DOMAIN}"
  printf 'DJANGO_SECURE_SSL_REDIRECT=true\n'
  printf 'DJANGO_SESSION_COOKIE_SECURE=true\n'
  printf 'DJANGO_CSRF_COOKIE_SECURE=true\n'
  printf 'DJANGO_SECURE_HSTS_SECONDS=31536000\n'
  printf 'DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=false\n'
  printf 'DJANGO_SECURE_HSTS_PRELOAD=false\n'
  printf 'DJANGO_TRUST_PROXY_SSL_HEADER=true\n'
  printf 'CORS_ALLOWED_ORIGINS=https://%s\n' "${APP_DOMAIN}"
  printf 'PUBLIC_API_URL=https://%s/api/v1\n' "${APP_DOMAIN}"
  printf 'PAYMENT_RETURN_URL=https://%s/settings/billing/return\n' "${APP_DOMAIN}"
  printf 'B2B_TRUST_PROXY_IP_HEADER=true\n'
  printf 'ADMIN_MFA_ENFORCED=false\n'
  printf 'PAYMENTS_ENABLED=false\n'
  printf 'PAYMENTS_LIVE_ENABLED=false\n'
  printf 'PAYMENTS_FISCALIZATION_MODE=disabled\n'
  printf 'OPENAI_API_KEY=\nANTHROPIC_API_KEY=\nDEEPSEEK_API_KEY=\nGEMINI_API_KEY=\nXAI_API_KEY=\n'
  } >"${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
fi

compose() {
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

cd "${PROJECT_DIR}"
compose build --pull
compose up -d postgres redis
compose run --rm backend python manage.py migrate --noinput
compose run --rm backend python manage.py collectstatic --noinput
compose run --rm \
  -e "AIWORKSPACE_ADMIN_PASSWORD=${ADMIN_PASSWORD}" \
  backend python manage.py bootstrap_admin \
  --username "${ADMIN_USERNAME}" --email "${ADMIN_EMAIL}" --reset-password
compose up -d --remove-orphans

READY=false
for _attempt in $(seq 1 60); do
  if curl -fsS --max-time 5 "https://${APP_DOMAIN}/api/v1/health/" >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 2
done

printf 'Логин администратора: %s\n' "${ADMIN_USERNAME}"
if [[ "${GENERATED_ADMIN_PASSWORD}" == true ]]; then
  printf 'Сгенерированный пароль: %s\n' "${ADMIN_PASSWORD}"
  printf 'Сохраните его сейчас: повторно он не выводится.\n'
fi
if [[ "${READY}" == true ]]; then
  touch "${INSTALL_MARKER}"
  chmod 600 "${INSTALL_MARKER}"
  printf '\nУстановка завершена.\n'
  printf 'Сайт: https://%s\n' "${APP_DOMAIN}"
  printf 'Админка: https://%s/admin/\n' "${APP_DOMAIN}"
else
  printf '\nКонтейнеры запущены, но HTTPS пока не готов. Установка не помечена завершённой.\n' >&2
  printf 'Проверьте DNS и журнал: sudo docker compose --env-file .env.production -f docker-compose.prod.yml logs caddy\n' >&2
  printf 'После исправления повторите: sudo ./install.sh\n' >&2
  exit 1
fi
printf 'Платежи и внешние AI-провайдеры выключены до заполнения ключей и ручной проверки.\n'
