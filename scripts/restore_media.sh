#!/usr/bin/env bash
set -Eeuo pipefail

archive="${1:?Usage: restore_media.sh /absolute/private/path/media.tar.gz}"
volume="${2:-ai-workspace_media_restore_drill}"

[[ -f "$archive" ]] || { echo "Backup not found: $archive" >&2; exit 2; }
[[ -f "${archive}.sha256" ]] || { echo "Checksum file not found" >&2; exit 3; }
(cd "$(dirname "$archive")" && sha256sum -c "$(basename "${archive}.sha256")")
tar -tzf "$archive" >/dev/null

if docker volume inspect "$volume" >/dev/null 2>&1; then
  echo "Refusing to restore into existing volume: $volume" >&2
  exit 4
fi
docker volume create "$volume" >/dev/null
cleanup() { docker volume rm -f "$volume" >/dev/null 2>&1 || true; }
trap cleanup ERR

docker run --rm \
  -v "${volume}:/restore" \
  -v "$(dirname "$archive"):/backup:ro" \
  alpine:3.21 \
  sh -ceu 'cd /restore && tar -xzf "/backup/'"$(basename "$archive")"'"'

docker run --rm -v "${volume}:/restore:ro" alpine:3.21 sh -ceu 'find /restore -type f -print >/tmp/files; test -s /tmp/files || true'
echo "Media restore drill succeeded into volume: $volume"
echo "Remove drill volume after inspection: docker volume rm $volume"
trap - ERR
