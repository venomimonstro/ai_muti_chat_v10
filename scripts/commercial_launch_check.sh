#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
EVIDENCE_DIR="${PROJECT_DIR}/launch-evidence"

[[ -f "${ENV_FILE}" ]] || { echo ".env.production not found" >&2; exit 2; }
mkdir -p "${EVIDENCE_DIR}"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${EVIDENCE_DIR}/commercial-launch-${STAMP}.json"

compose(){ docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"; }

compose exec -T backend python manage.py bootstrap_compliance >/dev/null
set +e
compose exec -T backend python manage.py commercial_launch_audit --json >"${REPORT}.tmp"
STATUS=$?
set -e
mv "${REPORT}.tmp" "${REPORT}"
sha256sum "${REPORT}" >"${REPORT}.sha256"

if [[ ${STATUS} -ne 0 ]]; then
  echo "COMMERCIAL LAUNCH: BLOCKED"
  echo "Evidence: ${REPORT}"
  exit ${STATUS}
fi

echo "COMMERCIAL LAUNCH: PASS"
echo "Evidence: ${REPORT}"
