#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
backup_file="${1:?Usage: restore_drill.sh /absolute/path/backup.dump}"

[[ -f "$ENV_FILE" ]] || { echo ".env.production not found" >&2; exit 2; }
if [[ "$backup_file" != /* || ! -f "$backup_file" ]]; then
  echo "Backup must be an existing absolute file path." >&2
  exit 2
fi
if [[ -f "${backup_file}.sha256" ]]; then sha256sum --check "${backup_file}.sha256"; fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
compose(){ docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
DRILL_DB="${POSTGRES_DB}_restore_drill"
DRILL_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${DRILL_DB}"

cleanup(){
  compose exec -T postgres sh -ceu 'dropdb -U "$POSTGRES_USER" --if-exists "$1"' sh "$DRILL_DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT

compose exec -T postgres pg_restore --list <"$backup_file" >/dev/null
cleanup
compose exec -T postgres sh -ceu 'createdb -U "$POSTGRES_USER" "$1"' sh "$DRILL_DB"
compose exec -T postgres sh -ceu 'pg_restore --exit-on-error --no-owner --no-acl -U "$POSTGRES_USER" -d "$1"' sh "$DRILL_DB" <"$backup_file"
compose run --rm -e DATABASE_URL="$DRILL_URL" backend python manage.py migrate --check
compose run --rm -e DATABASE_URL="$DRILL_URL" backend python manage.py check
compose run --rm -e DATABASE_URL="$DRILL_URL" backend python manage.py verify_financial_invariants

checksum="$(sha256sum "$backup_file" | awk '{print $1}')"
compose exec -T backend python manage.py record_operational_drill restore \
  --evidence "$backup_file" --checksum "$checksum" --size-bytes "$(stat -c %s "$backup_file")"

echo "RESTORE DRILL: PASS"
