#!/usr/bin/env bash
set -Eeuo pipefail

destination="${1:?Usage: backup_media.sh /absolute/private/path/media.tar.gz}"
volume="${MEDIA_DOCKER_VOLUME:-ai-workspace_media_data}"

if [[ "$destination" != /* || "$destination" == "/" ]]; then
  echo "Backup destination must be an explicit absolute file path." >&2
  exit 2
fi
if [[ -e "$destination" || -e "${destination}.sha256" ]]; then
  echo "Refusing to overwrite an existing backup." >&2
  exit 3
fi
docker volume inspect "$volume" >/dev/null
mkdir -p "$(dirname "$destination")"
umask 077
temporary="${destination}.tmp.$$"
trap 'rm -f -- "$temporary"' EXIT

docker run --rm \
  -v "${volume}:/source:ro" \
  -v "$(dirname "$destination"):/backup" \
  alpine:3.21 \
  sh -ceu 'cd /source && tar -czf "/backup/'"$(basename "$temporary")"'" .'

tar -tzf "$temporary" >/dev/null
mv -- "$temporary" "$destination"
trap - EXIT
sha256sum "$destination" | tee "${destination}.sha256"
