#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
destination="${1:?Usage: backup_postgres.sh /absolute/private/path/backup.dump}"

[[ -f "$ENV_FILE" ]] || { echo ".env.production not found" >&2; exit 2; }
if [[ "$destination" != /* || "$destination" == "/" ]]; then
  echo "Backup destination must be an explicit absolute file path." >&2
  exit 2
fi
if [[ -e "$destination" || -e "${destination}.sha256" ]]; then
  echo "Refusing to overwrite an existing backup." >&2
  exit 3
fi

mkdir -p "$(dirname "$destination")"
umask 077
temporary="${destination}.tmp.$$"
trap 'rm -f -- "$temporary"' EXIT
compose(){ docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }

compose exec -T postgres sh -ceu 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' >"$temporary"
test -s "$temporary" || { echo "Database backup is empty" >&2; exit 4; }
compose exec -T postgres pg_restore --list <"$temporary" >/dev/null
mv -- "$temporary" "$destination"
trap - EXIT
sha256sum "$destination" | tee "${destination}.sha256"
