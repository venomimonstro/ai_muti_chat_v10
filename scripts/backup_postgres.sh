#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
destination="${1:?Usage: backup_postgres.sh /absolute/private/path/backup.dump}"

if [[ "$destination" != /* || "$destination" == "/" ]]; then
  echo "Backup destination must be an explicit absolute file path." >&2
  exit 2
fi
if [[ -e "$destination" || -e "${destination}.sha256" ]]; then
  echo "Refusing to overwrite an existing backup." >&2
  exit 3
fi

umask 077
temporary="$(mktemp "${destination}.tmp.XXXXXX")"
trap 'rm -f -- "$temporary"' EXIT
pg_dump --format=custom --no-owner --no-acl "$DATABASE_URL" > "$temporary"
pg_restore --list "$temporary" >/dev/null
mv -- "$temporary" "$destination"
trap - EXIT
sha256sum "$destination" | tee "${destination}.sha256"
