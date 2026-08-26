#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
BACKUP_DIR="${PROJECT_DIR}/backups"

[[ "${EUID}" -eq 0 ]] || { printf 'Запустите: sudo ./scripts/update.sh\n' >&2; exit 1; }
[[ -f "${ENV_FILE}" ]] || { printf '.env.production не найден. Сначала запустите install.sh\n' >&2; exit 1; }
command -v flock >/dev/null 2>&1 || { printf 'Команда flock не найдена\n' >&2; exit 1; }

exec 9>"${PROJECT_DIR}/.update.lock"
flock -n 9 || { printf 'Другое обновление уже выполняется\n' >&2; exit 1; }
umask 077
mkdir -p "${BACKUP_DIR}"

compose() {
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

cd "${PROJECT_DIR}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_FILE="${BACKUP_DIR}/pre-update-${STAMP}.dump"
compose exec -T postgres sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' >"${BACKUP_FILE}"
test -s "${BACKUP_FILE}" || { printf 'Резервная копия пуста, обновление остановлено\n' >&2; exit 1; }
compose exec -T postgres pg_restore --list <"${BACKUP_FILE}" >/dev/null
sha256sum "${BACKUP_FILE}" >"${BACKUP_FILE}.sha256"

if [[ -d .git ]]; then
  git -c safe.directory="${PROJECT_DIR}" fetch origin main
  git -c safe.directory="${PROJECT_DIR}" merge --ff-only origin/main
fi

compose build --pull
compose run --rm backend python manage.py migrate --noinput
compose run --rm backend python manage.py collectstatic --noinput
compose up -d --remove-orphans
compose exec -T backend python manage.py check --deploy
compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/', timeout=5)"

printf 'Обновление завершено. Резервная копия: %s\n' "${BACKUP_FILE}"
