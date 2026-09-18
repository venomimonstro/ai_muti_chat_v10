#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
cd "${PROJECT_DIR}"

failures=0
ok(){ printf '✓ %s\n' "$1"; }
bad(){ printf '✗ %s\n' "$1" >&2; failures=$((failures+1)); }
run_check(){ local label="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$label"; else bad "$label"; fi; }

[[ -f "${ENV_FILE}" ]] || { bad ".env.production найден"; exit 1; }
compose(){ docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"; }

printf 'AI Workspace — диагностика production\n\n'
run_check "Docker daemon доступен" docker info
run_check "Production compose валиден" compose config

printf '\nКонтейнеры:\n'
compose ps || bad "Не удалось получить состояние контейнеров"

run_check "PostgreSQL отвечает" compose exec -T postgres sh -lc 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
run_check "Redis отвечает" compose exec -T redis sh -lc 'redis-cli -a "$REDIS_PASSWORD" ping | grep -q PONG'
run_check "Django system check" compose exec -T backend python manage.py check --fail-level ERROR
run_check "Миграции применены" compose exec -T backend python manage.py migrate --check
run_check "Backend readiness" compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/', timeout=5)"
run_check "Celery worker отвечает" compose exec -T worker celery -A config inspect ping --timeout=5

APP_DOMAIN="$(sed -n 's/^APP_DOMAIN=//p' "${ENV_FILE}" | head -n1)"
if [[ -n "${APP_DOMAIN}" ]]; then
  run_check "Публичный HTTPS health" curl -fsS --max-time 8 "https://${APP_DOMAIN}/api/v1/health/"
  run_check "Публичный frontend" curl -fsS --max-time 8 "https://${APP_DOMAIN}/"
fi

printf '\nПоследние зарегистрированные системные ошибки:\n'
compose exec -T backend sh -lc 'if [ -s /app/logs/system_issues.jsonl ]; then tail -n 5 /app/logs/system_issues.jsonl; else echo "Журнал пока пуст."; fi' || true

printf '\nИспользование Docker-диска:\n'
docker system df || true

if (( failures > 0 )); then
  printf '\nДИАГНОСТИКА: ОБНАРУЖЕНО ПРОБЛЕМ: %s\n' "${failures}" >&2
  exit 1
fi
printf '\nДИАГНОСТИКА: ВСЕ БАЗОВЫЕ ПРОВЕРКИ ПРОЙДЕНЫ\n'
