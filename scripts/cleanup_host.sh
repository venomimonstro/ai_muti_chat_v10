#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/ai-workspace}"
BACKUP_DIR="${PROJECT_DIR}/backups"
LOG_DIR="${PROJECT_DIR}/logs"
KEEP_BACKUPS="${KEEP_BACKUPS:-1}"

printf '[CLEANUP] Before:\n'
df -h / /var/lib/docker /var/lib/containerd 2>/dev/null || df -h /

printf '[CLEANUP] Removing stopped containers and disposable Docker cache/images...\n'
docker container prune -f >/dev/null 2>&1 || true
docker builder prune -af >/dev/null 2>&1 || true
docker buildx prune -af >/dev/null 2>&1 || true
docker image prune -af >/dev/null 2>&1 || true

printf '[CLEANUP] Removing rebuildable project artifacts...\n'
rm -rf -- \
  "${PROJECT_DIR}/frontend/.next" \
  "${PROJECT_DIR}/frontend/coverage" \
  "${PROJECT_DIR}/backend/.pytest_cache" \
  "${PROJECT_DIR}/.pytest_cache" \
  "${PROJECT_DIR}/.ruff_cache" \
  "${PROJECT_DIR}/test-results" \
  "${PROJECT_DIR}/playwright-report" 2>/dev/null || true
find "${PROJECT_DIR}/backend" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

mkdir -p "${BACKUP_DIR}" "${LOG_DIR}"
if [[ "${KEEP_BACKUPS}" =~ ^[0-9]+$ ]] && (( KEEP_BACKUPS >= 1 )); then
  while IFS= read -r file; do
    [[ -n "${file}" ]] || continue
    rm -f -- "${file}" "${file}.sha256"
    printf '[CLEANUP] Removed old DB backup: %s\n' "${file}"
  done < <(ls -1t "${BACKUP_DIR}"/pre-update-*.dump 2>/dev/null | tail -n "+$((KEEP_BACKUPS + 1))" || true)

  while IFS= read -r file; do
    [[ -n "${file}" ]] || continue
    rm -f -- "${file}" "${file}.sha256"
    printf '[CLEANUP] Removed old media backup: %s\n' "${file}"
  done < <(ls -1t "${BACKUP_DIR}"/pre-update-media-*.tar.gz 2>/dev/null | tail -n "+$((KEEP_BACKUPS + 1))" || true)
fi

find "${LOG_DIR}" -maxdepth 1 -type f \
  \( -name 'update-*.log' -o -name 'release-check-*.log' \) \
  -mtime +7 -delete 2>/dev/null || true

# Host package/download caches are rebuildable and can be surprisingly large on
# small VPS instances. Do not touch application data or Docker volumes.
apt-get clean >/dev/null 2>&1 || true
rm -rf /root/.cache/pip /root/.npm/_cacache 2>/dev/null || true

printf '[CLEANUP] After:\n'
df -h / /var/lib/docker /var/lib/containerd 2>/dev/null || df -h /
printf '[CLEANUP] Docker usage:\n'
docker system df 2>/dev/null || true
