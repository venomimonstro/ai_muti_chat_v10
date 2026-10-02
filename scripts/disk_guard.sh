#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/ai-workspace}"
MIN_FREE_GB="${MIN_FREE_GB:-6}"
TARGET_FREE_GB="${TARGET_FREE_GB:-8}"
KEEP_BACKUPS="${KEEP_BACKUPS:-1}"

free_kb() {
  df -Pk / | awk 'NR==2 {print $4}'
}

free_gb_int() {
  awk -v k="$(free_kb)" 'BEGIN { printf "%d", k/1024/1024 }'
}

cleanup_safe() {
  printf '[DISK-GUARD] Safe cleanup started. Volumes/database are protected.\n'

  docker container prune -f >/dev/null 2>&1 || true
  docker builder prune -af >/dev/null 2>&1 || true
  docker buildx prune -af >/dev/null 2>&1 || true
  docker image prune -af >/dev/null 2>&1 || true
  docker network prune -f >/dev/null 2>&1 || true

  # Rebuildable host/project caches only.
  rm -rf \
    "${PROJECT_DIR}/frontend/.next" \
    "${PROJECT_DIR}/frontend/coverage" \
    "${PROJECT_DIR}/backend/.pytest_cache" \
    "${PROJECT_DIR}/.pytest_cache" \
    "${PROJECT_DIR}/.ruff_cache" \
    "${PROJECT_DIR}/test-results" \
    "${PROJECT_DIR}/playwright-report" \
    /root/.cache/pip \
    /root/.npm/_cacache 2>/dev/null || true

  find "${PROJECT_DIR}/backend" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find "${PROJECT_DIR}/backend" -type f -name '*.pyc' -delete 2>/dev/null || true

  # Rotate project release logs/backups conservatively.
  mkdir -p "${PROJECT_DIR}/backups" "${PROJECT_DIR}/logs"
  find "${PROJECT_DIR}/logs" -maxdepth 1 -type f \
    \( -name 'update-*.log' -o -name 'release-check-*.log' \) \
    -mtime +7 -delete 2>/dev/null || true

  if [[ "${KEEP_BACKUPS}" =~ ^[0-9]+$ ]] && (( KEEP_BACKUPS >= 1 )); then
    ls -1t "${PROJECT_DIR}"/backups/pre-update-*.dump 2>/dev/null \
      | tail -n "+$((KEEP_BACKUPS + 1))" \
      | xargs -r rm -f -- 2>/dev/null || true
    ls -1t "${PROJECT_DIR}"/backups/pre-update-media-*.tar.gz 2>/dev/null \
      | tail -n "+$((KEEP_BACKUPS + 1))" \
      | xargs -r rm -f -- 2>/dev/null || true
  fi

  apt-get clean >/dev/null 2>&1 || true
  journalctl --vacuum-size=200M >/dev/null 2>&1 || true

  # Docker json logs are already rotated by compose; trim accidental legacy logs.
  find /var/lib/docker/containers -type f -name '*-json.log' -size +100M \
    -exec sh -c ': > "$1"' _ {} \; 2>/dev/null || true

  printf '[DISK-GUARD] Safe cleanup finished. Free: %s GiB\n' "$(free_gb_int)"
}

before="$(free_gb_int)"
printf '[DISK-GUARD] Free before: %s GiB\n' "$before"

if (( before < MIN_FREE_GB )); then
  cleanup_safe
fi

after="$(free_gb_int)"
if (( after < MIN_FREE_GB )); then
  printf '[DISK-GUARD] WARNING: only %s GiB free after cleanup; target is %s GiB.\n' "$after" "$TARGET_FREE_GB" >&2
fi

df -h /
docker system df 2>/dev/null || true
