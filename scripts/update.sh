#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
TEST_COMPOSE_FILE="${PROJECT_DIR}/docker-compose.test.yml"
BACKUP_DIR="${PROJECT_DIR}/backups"
LOG_DIR="${PROJECT_DIR}/logs"
LOCK_FILE="${PROJECT_DIR}/.update.lock"
DEPLOY_STATE_FILE="${LOG_DIR}/.last-deployed-sha"
PREVIOUS_SHA=""
CURRENT_SHA=""
DEPLOYED_SHA=""
FALLBACK_BASE_SHA=""
CHANGED_FILES=""
CODE_UPDATED=false
DEPLOY_STARTED=false
CURRENT_PHASE="initialization"
STARTED_AT="$(date +%s)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
UPDATE_LOG="${LOG_DIR}/update-${STAMP}.log"
REQUESTED_MODE="auto"
UPDATE_MODE="full"
BACKUP_FILE=""
MEDIA_BACKUP=""

usage() {
  cat <<'EOF'
Usage: sudo bash scripts/update.sh [--auto|--fast|--full]

  --auto  Default. Detect changed files and use FAST for frontend/docs/test-only changes,
          FULL for backend/runtime/security/billing/migrations/infrastructure changes.
  --fast  Force fast deploy, but only when changed files are safe for fast deployment.
  --full  Always run the complete 20-gate release check and production audits.
EOF
}

for arg in "$@"; do
  case "$arg" in
    --auto) REQUESTED_MODE="auto" ;;
    --fast) REQUESTED_MODE="fast" ;;
    --full) REQUESTED_MODE="full" ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$arg" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || { printf 'Запустите: sudo bash scripts/update.sh\n' >&2; exit 1; }
[[ -f "${ENV_FILE}" ]] || { printf '.env.production не найден. Сначала запустите install.sh\n' >&2; exit 1; }
command -v flock >/dev/null 2>&1 || { printf 'Команда flock не найдена\n' >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { printf 'openssl не найден\n' >&2; exit 1; }

bootstrap_free_kb() {
  df -Pk "${PROJECT_DIR}" | awk 'NR==2 {print $4}'
}

ensure_update_bootstrap_disk() {
  local minimum_kb="${UPDATE_MIN_BOOTSTRAP_FREE_KB:-524288}" # 512 MiB
  local free_kb
  free_kb="$(bootstrap_free_kb)"
  if (( free_kb >= minimum_kb )); then
    return 0
  fi

  printf '[DISK] Свободно только %s MiB. Очищаю только disposable Docker cache...\n' "$((free_kb / 1024))" >&2
  if command -v docker >/dev/null 2>&1; then
    docker builder prune -af >/dev/null 2>&1 || true
    docker image prune -f >/dev/null 2>&1 || true
  fi
  free_kb="$(bootstrap_free_kb)"
  if (( free_kb < minimum_kb )); then
    printf '[FAIL] Для безопасного запуска updater требуется минимум %s MiB, доступно %s MiB.\n'       "$((minimum_kb / 1024))" "$((free_kb / 1024))" >&2
    printf '[INFO] Docker volumes, PostgreSQL и пользовательские данные не удалялись.\n' >&2
    exit 1
  fi
}

if [[ "${AI_WORKSPACE_UPDATE_LOCK_HELD:-0}" != "1" ]]; then
  LOCK_CONFLICT_EXIT=75
  if flock --nonblock --close --conflict-exit-code "${LOCK_CONFLICT_EXIT}" "${LOCK_FILE}" \
      env AI_WORKSPACE_UPDATE_LOCK_HELD=1 bash "${BASH_SOURCE[0]}" "$@"; then
    exit 0
  else
    status=$?
    if [[ "$status" -eq "$LOCK_CONFLICT_EXIT" ]]; then
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
    fi
    exit "$status"
  fi
fi

ensure_update_bootstrap_disk

docker_free_kb() {
  local root
  root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
  [[ -n "${root}" && -e "${root}" ]] || root="${PROJECT_DIR}"
  df -Pk "${root}" | awk 'NR==2 {print $4}'
}

reclaim_disposable_docker_space() {
  command -v docker >/dev/null 2>&1 || return 0
  printf '[DISK] Очищаю только неиспользуемые Docker containers/images/build cache. Volumes не трогаются.\n'
  docker container prune -f >/dev/null 2>&1 || true
  docker builder prune -af >/dev/null 2>&1 || true
  docker buildx prune -af >/dev/null 2>&1 || true
  docker image prune -af >/dev/null 2>&1 || true
}

ensure_full_build_disk_before_backup() {
  local minimum_kb="${UPDATE_FULL_MIN_FREE_KB:-8388608}" # 8 GiB
  local free_kb
  free_kb="$(docker_free_kb)"
  if (( free_kb < minimum_kb )); then
    printf '[DISK] До backup/build свободно только %s MiB; запускаю безопасную очистку.\n' "$((free_kb / 1024))"
    reclaim_disposable_docker_space
    free_kb="$(docker_free_kb)"
  fi
  if (( free_kb < minimum_kb )); then
    printf '[FAIL] Недостаточно места ДО создания нового backup: %s MiB; требуется минимум %s MiB.\n' \
      "$((free_kb / 1024))" "$((minimum_kb / 1024))" >&2
    printf '[INFO] Новый backup не создавался; production volumes и PostgreSQL не удалялись.\n' >&2
    exit 1
  fi
  printf '[DISK] Свободно перед FULL backup/build: %s MiB.\n' "$((free_kb / 1024))"
}

mkdir -p "${BACKUP_DIR}" "${LOG_DIR}"
touch "${UPDATE_LOG}"
exec > >(tee -a "${UPDATE_LOG}") 2>&1
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

restore_previous_release() {
  local rollback_ok=true
  local rollback_ready=false

  printf '[ROLLBACK] Возвращаем код на %s\n' "$PREVIOUS_SHA" >&2
  git -c safe.directory="${PROJECT_DIR}" reset --hard "$PREVIOUS_SHA" || rollback_ok=false
  compose build || rollback_ok=false
  compose up -d --remove-orphans || rollback_ok=false

  if [[ "$rollback_ok" == true ]]; then
    for _rollback_attempt in $(seq 1 24); do
      if compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/', timeout=5).read()" >/dev/null 2>&1; then
        rollback_ready=true
        break
      fi
      sleep 5
    done
  fi

  if [[ "$rollback_ok" == true && "$rollback_ready" == true ]]; then
    printf '[ROLLBACK] PASS: предыдущий backend снова прошёл readiness.\n' >&2
    return 0
  fi

  printf '[CRITICAL] ROLLBACK FAILED: предыдущий commit не подтверждён readiness. Требуется ручное восстановление.\n' >&2
  printf '[CRITICAL] Проверка: docker compose --env-file %s -f %s ps\n' "$ENV_FILE" "$COMPOSE_FILE" >&2
  return 1
}

abort_update() {
  local signal="${1:-INTERRUPTED}"
  trap - ERR INT TERM HUP TSTP
  printf '\n[ABORT] Обновление прервано сигналом %s на этапе: %s\n' "$signal" "$CURRENT_PHASE" >&2
  terminate_children
  cleanup_test_stack
  if [[ "$DEPLOY_STARTED" == true && "$CODE_UPDATED" == true && -n "$PREVIOUS_SHA" && "$UPDATE_MODE" == "full" ]]; then
    restore_previous_release || true
  else
    printf '[INFO] Production deploy ещё не заменял backend; rollback не требуется.\n' >&2
  fi
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
  upsert_env_if_missing CHAT_GENERATION_STALE_TIMEOUT_SECONDS 360
  upsert_env_if_missing CHAT_TERMINAL_REFUND_RECOVERY_SECONDS 60

  local procurement_fail_closed
  procurement_fail_closed="$(sed -n 's/^PROCUREMENT_RUNTIME_FAIL_CLOSED=//p' "${ENV_FILE}" | head -n 1 | tr '[:upper:]' '[:lower:]')"
  if [[ -z "${procurement_fail_closed}" ]]; then
    printf 'PROCUREMENT_RUNTIME_FAIL_CLOSED=1\n' >>"${ENV_FILE}"
    procurement_fail_closed="1"
    printf '[ENV] Добавлен обязательный production fail-closed для закупочного контура.\n'
  fi
  case "${procurement_fail_closed}" in
    1|true|yes|on) ;;
    *)
      printf '[FAIL] PROCUREMENT_RUNTIME_FAIL_CLOSED=%s несовместим с production readiness.\n' "${procurement_fail_closed}" >&2
      printf '[INFO] Установите PROCUREMENT_RUNTIME_FAIL_CLOSED=1 в %s и повторите обновление.\n' "${ENV_FILE}" >&2
      exit 1
      ;;
  esac

  chmod 600 "${ENV_FILE}"
}

is_fast_safe_path() {
  local path="$1"
  case "$path" in
    frontend/*|docs/*|README*|LICENSE|scripts/update.sh) return 0 ;;
    backend/*/test_*.py|backend/*/tests.py|backend/*/*/test_*.py|backend/*/*/tests.py) return 0 ;;
    *) return 1 ;;
  esac
}

classify_update_mode() {
  local unsafe=false path

  if [[ "$REQUESTED_MODE" == "full" ]]; then
    UPDATE_MODE="full"
    return
  fi

  if [[ -z "$CHANGED_FILES" ]]; then
    UPDATE_MODE="fast"
    return
  fi

  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    if ! is_fast_safe_path "$path"; then
      unsafe=true
      break
    fi
  done <<<"$CHANGED_FILES"

  if [[ "$REQUESTED_MODE" == "fast" && "$unsafe" == true ]]; then
    printf '[FAIL] --fast запрещён: обнаружены runtime/backend/infrastructure изменения.\n' >&2
    printf '[INFO] Изменённые файлы:\n%s\n' "$CHANGED_FILES" >&2
    printf '[INFO] Используйте --full или обычный --auto.\n' >&2
    exit 1
  fi

  if [[ "$unsafe" == true ]]; then
    UPDATE_MODE="full"
  else
    UPDATE_MODE="fast"
  fi
}

rollback_app() {
  local exit_code=$?
  trap - ERR
  printf '\n[FAIL] Обновление остановлено на этапе: %s\n' "$CURRENT_PHASE" >&2
  cleanup_test_stack
  if [[ "$DEPLOY_STARTED" == true && "$CODE_UPDATED" == true && -n "$PREVIOUS_SHA" && "$UPDATE_MODE" == "full" ]]; then
    restore_previous_release || true
  else
    printf '[INFO] Production rollback не требуется или fast frontend deploy ещё не заменил backend.\n' >&2
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
  if [[ -f "$DEPLOY_STATE_FILE" ]]; then
    DEPLOYED_SHA="$(tr -d '[:space:]' <"$DEPLOY_STATE_FILE")"
    if ! git -c safe.directory="${PROJECT_DIR}" cat-file -e "${DEPLOYED_SHA}^{commit}" 2>/dev/null; then
      DEPLOYED_SHA=""
    fi
  fi
  FALLBACK_BASE_SHA="$(git -c safe.directory="${PROJECT_DIR}" rev-parse 'HEAD@{1}' 2>/dev/null || true)"
  printf '[INFO] Текущий commit: %s\n' "$PREVIOUS_SHA"
  [[ -z "$DEPLOYED_SHA" ]] || printf '[INFO] Последний успешно deployed commit: %s\n' "$DEPLOYED_SHA"
fi

phase 2 'Подготовка обновления'
printf '[INFO] Режим запрошен: %s\n' "${REQUESTED_MODE^^}"
printf '[INFO] Резервные копии будут созданы только если AUTO выберет FULL или указан --full.\n'

phase 3 'Получение нового кода и оценка риска'
if [[ -d .git ]]; then
  git -c safe.directory="${PROJECT_DIR}" fetch origin main
  git -c safe.directory="${PROJECT_DIR}" merge --ff-only origin/main
  CURRENT_SHA="$(git -c safe.directory="${PROJECT_DIR}" rev-parse HEAD)"
  [[ "$CURRENT_SHA" == "$PREVIOUS_SHA" ]] || CODE_UPDATED=true

  BASE_SHA="$DEPLOYED_SHA"
  if [[ -z "$BASE_SHA" ]]; then
    if [[ "$CURRENT_SHA" != "$PREVIOUS_SHA" ]]; then
      BASE_SHA="$PREVIOUS_SHA"
    elif [[ -n "$FALLBACK_BASE_SHA" ]] && git -c safe.directory="${PROJECT_DIR}" merge-base --is-ancestor "$FALLBACK_BASE_SHA" "$CURRENT_SHA" 2>/dev/null; then
      BASE_SHA="$FALLBACK_BASE_SHA"
    else
      BASE_SHA="$PREVIOUS_SHA"
    fi
  fi

  if [[ -n "$BASE_SHA" && "$BASE_SHA" != "$CURRENT_SHA" ]]; then
    CHANGED_FILES="$(git -c safe.directory="${PROJECT_DIR}" diff --name-only "$BASE_SHA" "$CURRENT_SHA")"
  else
    CHANGED_FILES=""
  fi

  classify_update_mode
  printf '[PASS] Код: %s -> %s\n' "${BASE_SHA:-unknown}" "$CURRENT_SHA"
  printf '[MODE] %s\n' "${UPDATE_MODE^^}"
  if [[ -n "$CHANGED_FILES" ]]; then
    printf '[INFO] Изменённые файлы:\n%s\n' "$CHANGED_FILES"
  else
    printf '[INFO] Изменений относительно известного deployed commit не найдено.\n'
  fi
fi

phase 4 'Проверка production-конфигурации'
ensure_runtime_env
printf '[PASS] Runtime env и secrets готовы.\n'

if [[ "$UPDATE_MODE" == "full" ]]; then
  phase 5 'FULL gate: место + backup + тесты + безопасность + frontend'
  ensure_full_build_disk_before_backup
  BACKUP_FILE="${BACKUP_DIR}/pre-update-${STAMP}.dump"
  MEDIA_BACKUP="${BACKUP_DIR}/pre-update-media-${STAMP}.tar.gz"
  printf '[BACKUP] PostgreSQL -> %s\n' "$BACKUP_FILE"
  compose exec -T postgres sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' >"${BACKUP_FILE}"
  test -s "${BACKUP_FILE}" || { printf '[FAIL] Резервная копия БД пуста\n' >&2; exit 1; }
  compose exec -T postgres pg_restore --list <"${BACKUP_FILE}" >/dev/null
  sha256sum "${BACKUP_FILE}" >"${BACKUP_FILE}.sha256"
  printf '[PASS] Резерв БД проверен.\n'
  printf '[BACKUP] Media -> %s\n' "$MEDIA_BACKUP"
  bash "${PROJECT_DIR}/scripts/backup_media.sh" "$MEDIA_BACKUP"
  printf '[PASS] Резерв media создан.\n'
  bash "${PROJECT_DIR}/scripts/release_check.sh"
  printf '[PASS] RELEASE CHECK завершён успешно.\n'
else
  phase 5 'FAST gate: только frontend build'
  printf '[INFO] Backend pytest/release gate пропущен: runtime/backend файлы не менялись.\n'
  compose build frontend
  printf '[PASS] Next.js production build завершён.\n'
fi

if [[ "$UPDATE_MODE" == "full" ]]; then
  phase 6 'FULL deploy: сборка, миграции и production'
  # release_check leaves no running test stack, but its build images/cache may still
  # occupy several GiB. Reclaim disposable layers before the production image export.
  reclaim_disposable_docker_space
  ensure_full_build_disk_before_backup
  compose build --pull
  compose pull searxng
  compose up -d postgres redis searxng
  compose run --rm backend python manage.py migration_safety_check
  DEPLOY_STARTED=true
  compose run --rm backend python manage.py migrate --noinput
  compose run --rm backend python manage.py collectstatic --noinput
  compose up -d --remove-orphans
  printf '[PASS] Production stack обновлён и запущен.\n'
else
  phase 6 'FAST deploy: frontend + reverse proxy'
  DEPLOY_STARTED=true
  compose up -d --no-deps frontend
  compose up -d --no-deps caddy
  printf '[PASS] Frontend и caddy обновлены. Backend/DB/worker не перезапускались.\n'
fi

phase 7 'Health-check production'
if [[ "$UPDATE_MODE" == "full" ]]; then
  compose exec -T backend python manage.py check --deploy
  compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/', timeout=5)"
  compose exec -T backend python manage.py customer_ai_readiness_check
  compose exec -T sandbox python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=5)"

  printf '[CHECK] Celery beat + worker heartbeat...\n'
  compose exec -T backend python -c "from django.core.cache import cache; cache.delete('system:celery-worker-heartbeat')"
  WORKER_HEARTBEAT_OK=false
  for _attempt in $(seq 1 18); do
    if compose exec -T backend python -c "from django.core.cache import cache; assert cache.get('system:celery-worker-heartbeat')" >/dev/null 2>&1; then
      WORKER_HEARTBEAT_OK=true
      break
    fi
    sleep 5
  done
  [[ "${WORKER_HEARTBEAT_OK}" == true ]] || { printf '[FAIL] Celery beat/worker не подтвердили свежий heartbeat\n' >&2; exit 1; }
  printf '[PASS] Celery beat и worker выполняют фоновые задачи.\n'

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

  RUNNING_SERVICES="$(compose ps --status running --services)"
  for service in postgres redis searxng backend worker beat frontend caddy sandbox; do
    printf '%s\n' "$RUNNING_SERVICES" | grep -Fxq "$service" || { printf '[SERVICE] %-12s FAIL\n' "$service" >&2; exit 1; }
    printf '[SERVICE] %-12s PASS\n' "$service"
  done
else
  FRONTEND_OK=false
  for _attempt in $(seq 1 12); do
    if compose exec -T frontend node -e "fetch('http://127.0.0.1:3000/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))" >/dev/null 2>&1; then
      FRONTEND_OK=true
      break
    fi
    sleep 2
  done
  [[ "$FRONTEND_OK" == true ]] || { printf '[FAIL] Frontend HTTP smoke не прошёл\n' >&2; exit 1; }
  RUNNING_SERVICES="$(compose ps --status running --services)"
  for service in frontend caddy backend; do
    printf '%s\n' "$RUNNING_SERVICES" | grep -Fxq "$service" || { printf '[SERVICE] %-12s FAIL\n' "$service" >&2; exit 1; }
    printf '[SERVICE] %-12s PASS\n' "$service"
  done
  printf '[PASS] FAST frontend smoke пройден.\n'
fi

phase 8 'Финальные проверки и завершение'
if [[ "$UPDATE_MODE" == "full" ]]; then
  compose exec -T backend python manage.py billing_integrity_check
  compose exec -T backend python manage.py agent_system_audit
  compose exec -T backend python manage.py agent_webhook_audit
  compose exec -T backend python manage.py agent_security_audit
  compose exec -T backend python manage.py agent_commercial_limits_audit
  compose exec -T backend python manage.py agent_recovery_audit
  compose exec -T backend python manage.py connection_health_audit
  compose exec -T backend python manage.py dev_studio_audit
  compose exec -T backend python manage.py agent_billing_audit
else
  printf '[SKIP] Backend/billing/agent audits не нужны для frontend-only deploy.\n'
fi

CURRENT_SHA="$(git -c safe.directory="${PROJECT_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
printf '%s\n' "$CURRENT_SHA" >"$DEPLOY_STATE_FILE"
chmod 600 "$DEPLOY_STATE_FILE"

# Keep the small production host from accumulating disposable BuildKit/container
# layers across successful releases. Never prune volumes or images referenced by a
# container. Cleanup is best-effort and cannot invalidate an otherwise healthy deploy.
if [[ "$UPDATE_MODE" == "full" ]]; then
  printf '[INFO] Reclaiming disposable Docker build cache after successful deploy...\n'
  docker container prune -f >/dev/null 2>&1 || true
  docker builder prune -af >/dev/null 2>&1 || true
  docker image prune -af >/dev/null 2>&1 || true
fi

trap - ERR INT TERM HUP TSTP
FINISHED_AT="$(date +%s)"
DURATION="$((FINISHED_AT - STARTED_AT))"
printf '\n################################################################\n'
printf 'UPDATE: PASS\n'
printf 'Mode: %s\n' "${UPDATE_MODE^^}"
printf 'Commit: %s\n' "$CURRENT_SHA"
printf 'Duration: %ss\n' "$DURATION"
[[ -z "$BACKUP_FILE" ]] || printf 'DB backup: %s\n' "$BACKUP_FILE"
[[ -z "$MEDIA_BACKUP" ]] || printf 'Media backup: %s\n' "$MEDIA_BACKUP"
printf 'Full log: %s\n' "$UPDATE_LOG"
printf '################################################################\n'
