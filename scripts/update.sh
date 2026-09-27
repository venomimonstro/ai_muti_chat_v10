#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
TEST_COMPOSE_FILE="${PROJECT_DIR}/docker-compose.test.yml"
BACKUP_DIR="${PROJECT_DIR}/backups"
LOG_DIR="${PROJECT_DIR}/logs"
PREVIOUS_SHA=""
CODE_UPDATED=false
DEPLOY_STARTED=false
CURRENT_PHASE="initialization"
STARTED_AT="$(date +%s)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
UPDATE_LOG="${LOG_DIR}/update-${STAMP}.log"

[[ "${EUID}" -eq 0 ]] || { printf 'Запустите: sudo bash scripts/update.sh\n' >&2; exit 1; }
[[ -f "${ENV_FILE}" ]] || { printf '.env.production не найден. Сначала запустите install.sh\n' >&2; exit 1; }
command -v flock >/dev/null 2>&1 || { printf 'Команда flock не найдена\n' >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { printf 'openssl не найден\n' >&2; exit 1; }

mkdir -p "${BACKUP_DIR}" "${LOG_DIR}"
touch "${UPDATE_LOG}"
exec > >(tee -a "${UPDATE_LOG}") 2>&1

LOCK_FILE="${PROJECT_DIR}/.update.lock"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  printf '\n[BLOCKED] Другое обновление уже выполняется.\n' >&2
  if command -v fuser >/dev/null 2>&1; then
    LOCK_PIDS="$(fuser "${LOCK_FILE}" 2>/dev/null || true)"
    if [[ -n "${LOCK_PIDS// }" ]]; then
      printf '[INFO] Lock держит PID:%s\n' "${LOCK_PIDS}" >&2
      ps -o pid,ppid,etime,stat,cmd -p ${LOCK_PIDS} >&2 2>/dev/null || true
    fi
  elif command -v lsof >/dev/null 2>&1; then
    lsof "${LOCK_FILE}" >&2 2>/dev/null || true
  fi
  printf '[INFO] Не удаляйте .update.lock вручную, пока процесс-владелец существует.\n' >&2
  printf '[INFO] Log: %s\n' "${UPDATE_LOG}" >&2
  exit 1
fi

umask 077

phase() {
  local number="$1" title="$2"
  CURRENT_PHASE="${number}/8 ${title}"
  printf '\n################################################################\n'
  printf '[UPDATE %s/8] %s\n' "$number" "$title"
  printf '################################################################\n'
}

compose() {
  docker compose --ansi never --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

cleanup_test_stack() {
  docker compose --ansi never -f "${TEST_COMPOSE_FILE}" down -v --remove-orphans >/dev/null 2>&1 || true
}

terminate_children() {
  local children
  children="$(pgrep -P $$ 2>/dev/null || true)"
  if [[ -n "${children}" ]]; then
    kill -TERM ${children} >/dev/null 2>&1 || true
    sleep 1
    children="$(pgrep -P $$ 2>/dev/null || true)"
    [[ -z "${children}" ]] || kill -KILL ${children} >/dev/null 2>&1 || true
  fi
}

abort_update() {
  local signal="${1:-INTERRUPTED}"
  trap - INT TERM HUP TSTP
  printf '\n[ABORT] Обновление прервано сигналом %s на этапе: %s\n' "$signal" "$CURRENT_PHASE" >&2
  terminate_children
  cleanup_test_stack
  printf '[INFO] Production-контейнеры не останавливались намеренно.\n' >&2
  printf '[INFO] Полный лог: %s\n' "$UPDATE_LOG" >&2
  exit 130
}

upsert_env_if_missing() {
  local key="$1" value="$2"
  if ! grep -q "^${key}=" "${ENV_FILE}"; then
    printf '%s=%s\n' "${key}" "${value}" >>"${ENV_FILE}"
  fi
}

ensure_env_default() {
  local key="$1" value="$2" current
  current="$(sed -n "s/^${key}=//p" "${ENV_FILE}" | head -n 1)"
  if [[ -z "${current}" ]]; then
    if grep -q "^${key}=" "${ENV_FILE}"; then
      sed -i "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
    else
      printf '%s=%s\n' "${key}" "${value}" >>"${ENV_FILE}"
    fi
  fi
}

ensure_runtime_env() {
  local sandbox_secret searxng_secret
  sandbox_secret="$(sed -n 's/^SANDBOX_SHARED_SECRET=//p' "${ENV_FILE}" | head -n 1)"
  if [[ -z "${sandbox_secret}" ]]; then
    sandbox_secret="$(openssl rand -hex 32)"
    if grep -q '^SANDBOX_SHARED_SECRET=' "${ENV_FILE}"; then
      sed -i "s|^SANDBOX_SHARED_SECRET=.*|SANDBOX_SHARED_SECRET=${sandbox_secret}|" "${ENV_FILE}"
    else
      printf 'SANDBOX_SHARED_SECRET=%s\n' "${sandbox_secret}" >>"${ENV_FILE}"
    fi
  fi

  searxng_secret="$(sed -n 's/^SEARXNG_SECRET=//p' "${ENV_FILE}" | head -n 1)"
  if [[ -z "${searxng_secret}" ]]; then
    searxng_secret="$(openssl rand -hex 32)"
    if grep -q '^SEARXNG_SECRET=' "${ENV_FILE}"; then
      sed -i "s|^SEARXNG_SECRET=.*|SEARXNG_SECRET=${searxng_secret}|" "${ENV_FILE}"
    else
      printf 'SEARXNG_SECRET=%s\n' "${searxng_secret}" >>"${ENV_FILE}"
    fi
  fi

  upsert_env_if_missing SANDBOX_TIMEOUT_SECONDS 90
  upsert_env_if_missing SANDBOX_MAX_BODY_BYTES 2097152
  upsert_env_if_missing SANDBOX_MAX_FILES 300
  upsert_env_if_missing SANDBOX_MAX_FILE_BYTES 524288
  upsert_env_if_missing SANDBOX_CLIENT_TIMEOUT_SECONDS 100
  upsert_env_if_missing AGENT_MAX_ACTIVE_RUNS_PER_USER 3
  ensure_env_default WEB_SEARCH_BASE_URL http://searxng:8080
  ensure_env_default WEB_SEARCH_TRUSTED_HOSTS searxng
  upsert_env_if_missing WEB_SEARCH_MAX_RESULTS 8
  upsert_env_if_missing WEB_CONTEXT_MAX_TOKENS 2200
  chmod 600 "${ENV_FILE}"
}

rollback_app() {
  local exit_code=$?
  trap - ERR
  printf '\n[FAIL] Обновление остановлено на этапе: %s\n' "$CURRENT_PHASE" >&2
  cleanup_test_stack
  if [[ "$DEPLOY_STARTED" == true && "$CODE_UPDATED" == true && -n "$PREVIOUS_SHA" ]]; then
    printf '[ROLLBACK] Deployment уже начался. Возвращаем код на %s\n' "$PREVIOUS_SHA" >&2
    git -c safe.directory="${PROJECT_DIR}" reset --hard "$PREVIOUS_SHA" || true
    compose build || true
    compose up -d --remove-orphans || true
  else
    printf '[INFO] Production deployment ещё не начинался; рабочий код не откатываем.\n' >&2
  fi
  printf '[INFO] Полный лог: %s\n' "$UPDATE_LOG" >&2
  exit "$exit_code"
}

trap rollback_app ERR
trap 'abort_update SIGINT' INT
trap 'abort_update SIGTERM' TERM
trap 'abort_update SIGHUP' HUP
trap 'abort_update SIGTSTP' TSTP

cd "${PROJECT_DIR}"

phase 1 'Проверка блокировки и текущего состояния'
printf '[PASS] Эксклюзивный update-lock получен. PID=%s\n' "$$"
if [[ -d .git ]]; then
  git -c safe.directory="${PROJECT_DIR}" diff --quiet || {
    printf '[FAIL] Есть незакоммиченные изменения. Автообновление остановлено.\n' >&2
    exit 1
  }
  PREVIOUS_SHA="$(git -c safe.directory="${PROJECT_DIR}" rev-parse HEAD)"
  printf '[INFO] Текущий commit: %s\n' "$PREVIOUS_SHA"
fi

BACKUP_FILE="${BACKUP_DIR}/pre-update-${STAMP}.dump"
MEDIA_BACKUP="${BACKUP_DIR}/pre-update-media-${STAMP}.tar.gz"

phase 2 'Резервное копирование'
printf '[BACKUP] PostgreSQL -> %s\n' "$BACKUP_FILE"
compose exec -T postgres sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' >"${BACKUP_FILE}"
test -s "${BACKUP_FILE}" || { printf '[FAIL] Резервная копия БД пуста\n' >&2; exit 1; }
compose exec -T postgres pg_restore --list <"${BACKUP_FILE}" >/dev/null
sha256sum "${BACKUP_FILE}" >"${BACKUP_FILE}.sha256"
printf '[PASS] Резерв БД проверен.\n'
printf '[BACKUP] Media -> %s\n' "$MEDIA_BACKUP"
bash "${PROJECT_DIR}/scripts/backup_media.sh" "$MEDIA_BACKUP"
printf '[PASS] Резерв media создан.\n'

phase 3 'Получение нового кода'
if [[ -d .git ]]; then
  git -c safe.directory="${PROJECT_DIR}" fetch origin main
  git -c safe.directory="${PROJECT_DIR}" merge --ff-only origin/main
  CURRENT_SHA="$(git -c safe.directory="${PROJECT_DIR}" rev-parse HEAD)"
  if [[ "$CURRENT_SHA" != "$PREVIOUS_SHA" ]]; then
    CODE_UPDATED=true
    printf '[PASS] Код обновлён: %s -> %s\n' "$PREVIOUS_SHA" "$CURRENT_SHA"
  else
    printf '[PASS] Уже используется последний commit: %s\n' "$CURRENT_SHA"
  fi
fi

phase 4 'Проверка production-конфигурации'
ensure_runtime_env
printf '[PASS] Runtime env и secrets готовы.\n'

phase 5 'Release gate: тесты, безопасность, frontend'
printf '[INFO] Тесты показывают имена: PASSED / FAILED. Точки и одиночные F отключены.\n'
printf '[INFO] При ошибке смотрите конкретный test_name и traceback.\n'
bash "${PROJECT_DIR}/scripts/release_check.sh"
printf '[PASS] RELEASE CHECK завершён успешно.\n'

phase 6 'Сборка, миграции и запуск production'
compose build --pull
compose pull searxng
compose up -d postgres redis searxng
compose run --rm backend python manage.py migration_safety_check
DEPLOY_STARTED=true
compose run --rm backend python manage.py migrate --noinput
compose run --rm backend python manage.py collectstatic --noinput
compose up -d --remove-orphans
printf '[PASS] Production stack обновлён и запущен.\n'

phase 7 'Health-check production'
compose exec -T backend python manage.py check --deploy
compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/', timeout=5)"
compose exec -T sandbox python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=5)"

printf '[CHECK] Бесплатный web-search...\n'
SEARCH_OK=false
for _attempt in $(seq 1 12); do
  if compose exec -T backend python -c "import json,urllib.parse,urllib.request; u='http://searxng:8080/search?'+urllib.parse.urlencode({'q':'AI','format':'json','safesearch':1}); d=json.load(urllib.request.urlopen(u,timeout=8)); assert isinstance(d.get('results'),list)" >/dev/null 2>&1; then
    SEARCH_OK=true
    break
  fi
  sleep 5
done
[[ "${SEARCH_OK}" == true ]] || { printf '[FAIL] SearXNG web-search не прошёл health-check\n' >&2; exit 1; }
printf '[PASS] Web-search отвечает.\n'

printf '[CHECK] Обязательные production services...\n'
RUNNING_SERVICES="$(compose ps --status running --services)"
for service in postgres redis searxng backend worker beat frontend caddy sandbox; do
  if printf '%s\n' "$RUNNING_SERVICES" | grep -Fxq "$service"; then
    printf '[SERVICE] %-12s PASS\n' "$service"
  else
    printf '[SERVICE] %-12s FAIL\n' "$service" >&2
    exit 1
  fi
done

phase 8 'Production-аудиты и завершение'
compose exec -T backend python manage.py billing_integrity_check
compose exec -T backend python manage.py agent_system_audit
compose exec -T backend python manage.py agent_webhook_audit
compose exec -T backend python manage.py agent_security_audit
compose exec -T backend python manage.py agent_commercial_limits_audit
compose exec -T backend python manage.py agent_recovery_audit
compose exec -T backend python manage.py connection_health_audit
compose exec -T backend python manage.py dev_studio_audit
compose exec -T backend python manage.py agent_billing_audit

trap - ERR INT TERM HUP TSTP
FINISHED_AT="$(date +%s)"
DURATION="$((FINISHED_AT - STARTED_AT))"
CURRENT_SHA="$(git -c safe.directory="${PROJECT_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
printf '\n################################################################\n'
printf 'UPDATE: PASS\n'
printf 'Commit: %s -> %s\n' "${PREVIOUS_SHA:-unknown}" "$CURRENT_SHA"
printf 'Duration: %ss\n' "$DURATION"
printf 'DB backup: %s\n' "$BACKUP_FILE"
printf 'Media backup: %s\n' "$MEDIA_BACKUP"
printf 'Full log: %s\n' "$UPDATE_LOG"
printf '################################################################\n'
