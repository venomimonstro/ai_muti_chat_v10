#!/usr/bin/env bash
set -Eeuo pipefail

source_file="${1:?Usage: offsite_backup.sh /absolute/path/backup remote:path}"
remote="${2:?Usage: offsite_backup.sh /absolute/path/backup remote:path}"

[[ -f "$source_file" ]] || { echo "Backup not found" >&2; exit 2; }
[[ -f "${source_file}.sha256" ]] || { echo "Checksum not found" >&2; exit 3; }
command -v rclone >/dev/null 2>&1 || { echo "rclone is required" >&2; exit 4; }

(cd "$(dirname "$source_file")" && sha256sum -c "$(basename "${source_file}.sha256")")
name="$(basename "$source_file")"
remote_file="${remote%/}/$name"
remote_sidecar="${remote_file}.sha256"

rclone copyto "$source_file" "$remote_file" --immutable
rclone copyto "${source_file}.sha256" "$remote_sidecar" --immutable

local_sum="$(sha256sum "$source_file" | awk '{print $1}')"
sidecar_sum="$(rclone cat "$remote_sidecar" | awk 'NR==1 {print $1}')"
[[ "$sidecar_sum" == "$local_sum" ]] || {
  echo "Remote checksum sidecar mismatch" >&2
  exit 5
}

# Verify the actual remote bytes. This is intentionally more expensive than trusting
# provider metadata because launch/backup evidence must detect a corrupted object.
remote_sum="$(rclone cat "$remote_file" | sha256sum | awk '{print $1}')"
[[ "$remote_sum" == "$local_sum" ]] || {
  echo "Remote backup object checksum mismatch" >&2
  exit 6
}

echo "Offsite backup verified byte-for-byte: $remote_file"
