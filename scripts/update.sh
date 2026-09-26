#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
BACKUP_DIR="${PROJECT_DIR}/backups"
PREVIOUS_SHA=""
CODE_UPDATED=false
DEPLOY_STARTED=false

[[ "${EUID}" -eq 0 ]] || { printf 'Запустите: sudo ./scripts/update.sh\n' >&2; exit 1; }
[[ -f "${ENV_FILE}" ]] || { printf '.env.production не найден. Сначала запустите install.sh\n' >&2; exit 1; }
command -v flock >/dev/null 2>&1 || { printf 'Команда flock не найдена\n' >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { printf 'openssl не найден\n' >&2; exit 1; }

exec 9>"${PROJECT_DIR}/.update.lock"
flock -n 9 || { printf 'Другое обновление уже выполняется\n' >&2; exit 1; }
umask 077
mkdir -p "${BACKUP_DIR}"

compose() {
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

upsert_env_if_missing() {
  local key="$1" value="$2"
  if ! grep -q "^${key}=" "${ENV_FILE}"; then
    printf '%s=%s\n' "${key}" "${value}" >>"${ENV_FILE}"
  fi
}

ensure_runtime_env() {
  local sandbox_secret
  sandbox_secret="$(sed -n 's/^SANDBOX_SHARED_SECRET=//p' "${ENV_FILE}" | head -n 1)"
  if [[ -z "${sandbox_secret}" ]]; then
    sandbox_secret="$(openssl rand -hex 32)"
    if grep -q '^SANDBOX_SHARED_SECRET=' "${ENV_FILE}"; then
      sed -i "s|^SANDBOX_SHARED_SECRET=.*|SANDBOX_SHARED_SECRET=${sandbox_secret}|" "${ENV_FILE}"
    else
      printf 'SANDBOX_SHARED_SECRET=%s\n' "${sandbox_secret}" >>"${ENV_FILE}"
    fi
  fi
  upsert_env_if_missing SANDBOX_TIMEOUT_SECONDS 90
  upsert_env_if_missing SANDBOX_MAX_BODY_BYTES 2097152
  upsert_env_if_missing SANDBOX_MAX_FILES 300
  upsert_env_if_missing SANDBOX_MAX_FILE_BYTES 524288
  upsert_env_if_missing SANDBOX_CLIENT_TIMEOUT_SECONDS 100
  chmod 600 "${ENV_FILE}"
}

rollback_app() {
  local exit_code=$?
  if [[ "$CODE_UPDATED" == true && -n "$PREVIOUS_SHA" ]]; then
    printf 'Релиз не прошёл проверку. Возвращаем приложение на %s\n' "$PREVIOUS_SHA" >&2
    git -c safe.directory="${PROJECT_DIR}" reset --hard "$PREVIOUS_SHA" || true
    compose build || true
    compose up -d --remove-orphans || true
  fi
  exit "$exit_code"
}
trap rollback_app ERR

cd "${PROJECT_DIR}"
if [[ -d .git ]]; then
  git -c safe.directory="${PROJECT_DIR}" diff --quiet || {
    printf 'Есть незакоммиченные изменения. Автообновление остановлено.\n' >&2
    exit 1
  }
  PREVIOUS_SHA="$(git -c safe.directory="${PROJECT_DIR}" rev-parse HEAD)"
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="${BACKUP_DIR}/pre-update-${STAMP}.dump"
MEDIA_BACKUP="${BACKUP_DIR}/pre-update-media-${STAMP}.tar.gz"

printf 'Создаём резерв БД...\n'
compose exec -T postgres sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' >"${BACKUP_FILE}"
test -s "${BACKUP_FILE}" || { printf 'Резервная копия БД пуста\n' >&2; exit 1; }
compose exec -T postgres pg_restore --list <"${BACKUP_FILE}" >/dev/null
sha256sum "${BACKUP_FILE}" >"${BACKUP_FILE}.sha256"

printf 'Создаём резерв media...\n'
bash "${PROJECT_DIR}/scripts/backup_media.sh" "$MEDIA_BACKUP"

if [[ -d .git ]]; then
  git -c safe.directory="${PROJECT_DIR}" fetch origin main
  git -c safe.directory="${PROJECT_DIR}" merge --ff-only origin/main
  CURRENT_SHA="$(git -c safe.directory="${PROJECT_DIR}" rev-parse HEAD)"
  if [[ "$CURRENT_SHA" != "$PREVIOUS_SHA" ]]; then
    CODE_UPDATED=true
  fi
fi

ensure_runtime_env

printf 'Запускаем обязательный release gate...\n'
bash "${PROJECT_DIR}/scripts/release_check.sh"

compose build --pull
compose up -d postgres redis
compose run --rm backend python manage.py migration_safety_check
DEPLOY_STARTED=true
compose run --rm backend python manage.py migrate --noinput
compose run --rm backend python manage.py collectstatic --noinput
compose up -d --remove-orphans
compose exec -T backend python manage.py check --deploy
compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/', timeout=5)"
compose exec -T sandbox python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=5)"

printf 'Проверяем обязательные production services...\n'
RUNNING_SERVICES="$(compose ps --status running --services)"
for service in postgres redis backend worker beat frontend caddy sandbox; do
  printf '%s\n' "$RUNNING_SERVICES" | grep -Fxq "$service" || {
    printf 'Production service не запущен: %s\n' "$service" >&2
    exit 1
  }
done

printf 'Проверяем production billing, security и Agent Runtime...\n'
compose exec -T backend python manage.py billing_integrity_check
compose exec -T backend python manage.py agent_system_audit
compose exec -T backend python manage.py agent_webhook_audit
compose exec -T backend python manage.py agent_security_audit
compose exec -T backend python manage.py dev_studio_audit
compose exec -T backend python manage.py agent_billing_audit

trap - ERR
printf 'Обновление завершено.\n'
printf 'Commit: %s -> %s\n' "${PREVIOUS_SHA:-unknown}" "$(git -c safe.directory="${PROJECT_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
printf 'DB backup: %s\nMedia backup: %s\n' "$BACKUP_FILE" "$MEDIA_BACKUP"
