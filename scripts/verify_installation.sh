#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

fail(){ printf 'Проверка установки: ОШИБКА: %s\n' "$1" >&2; exit 1; }
[[ "${EUID}" -eq 0 ]] || fail "запустите через sudo"
[[ -f "${ENV_FILE}" ]] || fail ".env.production не найден"
[[ -f "${COMPOSE_FILE}" ]] || fail "docker-compose.prod.yml не найден"

compose(){ docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"; }
env_value(){ sed -n "s/^$1=//p" "${ENV_FILE}" | head -n 1; }

APP_DOMAIN="$(env_value APP_DOMAIN)"
BASE_URL="$(env_value NEXT_PUBLIC_SITE_URL)"
[[ -n "${APP_DOMAIN}" ]] || fail "APP_DOMAIN пуст"
case "${BASE_URL}" in
  http://*|https://*) ;;
  *) fail "NEXT_PUBLIC_SITE_URL должен начинаться с http:// или https://" ;;
esac
SCHEME="${BASE_URL%%://*}"

printf 'Проверяем Docker Compose...\n'
compose config --quiet

printf 'Проверяем контейнеры...\n'
for service in postgres redis backend worker beat frontend caddy; do
  container_id="$(compose ps -q "${service}")"
  [[ -n "${container_id}" ]] || fail "контейнер ${service} не создан"
  running="$(docker inspect -f '{{.State.Running}}' "${container_id}" 2>/dev/null || echo false)"
  [[ "${running}" == "true" ]] || fail "контейнер ${service} не работает"
done

for service in postgres redis backend frontend; do
  container_id="$(compose ps -q "${service}")"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container_id}" 2>/dev/null || echo unknown)"
  [[ "${health}" == "healthy" ]] || fail "healthcheck ${service}: ${health}"
done

printf 'Проверяем PostgreSQL и Redis...\n'
compose exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null
redis_ping="$(compose exec -T redis sh -c 'redis-cli --no-auth-warning -a "$REDIS_PASSWORD" ping' | tr -d '\r')"
[[ "${redis_ping}" == "PONG" ]] || fail "Redis не отвечает PONG"

printf 'Проверяем Django и миграции...\n'
compose exec -T backend python manage.py check --fail-level ERROR >/dev/null
compose exec -T backend python manage.py migrate --check >/dev/null
compose exec -T backend python manage.py makemigrations --check --dry-run >/dev/null

printf 'Проверяем backend readiness внутри контейнера...\n'
compose exec -T -e AIWS_VERIFY_SCHEME="${SCHEME}" backend python -c "import os,urllib.request; req=urllib.request.Request('http://127.0.0.1:8000/api/v1/readiness/',headers={'Host':os.environ['APP_DOMAIN'],'X-Forwarded-Proto':os.environ.get('AIWS_VERIFY_SCHEME','http')}); r=urllib.request.urlopen(req,timeout=8); assert r.status==200, r.status" >/dev/null

printf 'Проверяем frontend внутри контейнера...\n'
compose exec -T frontend node -e "fetch('http://127.0.0.1:3000/status').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

printf 'Проверяем Caddy...\n'
compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile >/dev/null

printf 'Проверяем публичные маршруты %s...\n' "${BASE_URL}"
for endpoint in /api/v1/health/ /api/v1/readiness/ /status; do
  curl -fsS --max-time 12 "${BASE_URL}${endpoint}" >/dev/null || fail "не отвечает ${BASE_URL}${endpoint}"
done

printf 'Проверка установки пройдена: Docker, PostgreSQL, Redis, Django, миграции, backend, frontend и Caddy работают.\n'
