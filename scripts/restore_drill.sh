#!/usr/bin/env bash
set -euo pipefail

: "${RESTORE_DRILL_DATABASE_URL:?RESTORE_DRILL_DATABASE_URL is required}"
backup_file="${1:?Usage: restore_drill.sh /absolute/path/backup.dump}"

if [[ "$backup_file" != /* || ! -f "$backup_file" ]]; then
  echo "Backup must be an existing absolute file path." >&2
  exit 2
fi
if [[ -f "${backup_file}.sha256" ]]; then
  sha256sum --check "${backup_file}.sha256"
fi
pg_restore --list "$backup_file" >/dev/null

database_name="${RESTORE_DRILL_DATABASE_URL%%\?*}"
database_name="${database_name##*/}"
if [[ "$database_name" != *_restore_drill ]]; then
  echo "Refusing restore: target database name must end with _restore_drill." >&2
  exit 3
fi

pg_restore --clean --if-exists --no-owner --no-acl \
  --dbname="$RESTORE_DRILL_DATABASE_URL" "$backup_file"
DATABASE_URL="$RESTORE_DRILL_DATABASE_URL" python backend/manage.py migrate --check
DATABASE_URL="$RESTORE_DRILL_DATABASE_URL" python backend/manage.py check
echo "Restore drill passed for isolated database suffix _restore_drill."
