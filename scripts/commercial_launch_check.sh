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
RELEASE_LOG="${EVIDENCE_DIR}/release-check-${STAMP}.log"
E2E_LOG="${EVIDENCE_DIR}/commercial-e2e-${STAMP}.log"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
compose(){ docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }

printf 'Running immutable release gate...\n'
set +e
bash "$PROJECT_DIR/scripts/release_check.sh" >"${RELEASE_LOG}.tmp" 2>&1
RELEASE_STATUS=$?
set -e
mv "${RELEASE_LOG}.tmp" "$RELEASE_LOG"
sha256sum "$RELEASE_LOG" >"${RELEASE_LOG}.sha256"
if [[ $RELEASE_STATUS -ne 0 ]]; then
  echo "COMMERCIAL LAUNCH: BLOCKED BY RELEASE CHECK"; echo "Evidence: $RELEASE_LOG"; exit "$RELEASE_STATUS"
fi

printf 'Running live commercial E2E...\n'
set +e
E2E_BASE_URL="https://${APP_DOMAIN}" bash "$PROJECT_DIR/scripts/commercial_e2e_smoke.sh" >"${E2E_LOG}.tmp" 2>&1
E2E_STATUS=$?
set -e
mv "${E2E_LOG}.tmp" "$E2E_LOG"
E2E_SHA="$(sha256sum "$E2E_LOG" | awk '{print $1}')"
printf '%s  %s\n' "$E2E_SHA" "$E2E_LOG" >"${E2E_LOG}.sha256"
if [[ $E2E_STATUS -ne 0 ]]; then
  echo "COMMERCIAL LAUNCH: BLOCKED BY E2E"; echo "Evidence: $E2E_LOG"; exit "$E2E_STATUS"
fi
compose exec -T backend python manage.py record_commercial_drill commercial_e2e --evidence "$E2E_LOG" --checksum "$E2E_SHA" >/dev/null

compose exec -T backend python manage.py bootstrap_compliance >/dev/null
set +e
compose exec -T backend python manage.py commercial_launch_audit --json >"${REPORT}.tmp"
STATUS=$?
set -e
mv "${REPORT}.tmp" "$REPORT"
sha256sum "$REPORT" >"${REPORT}.sha256"

if [[ $STATUS -ne 0 ]]; then
  echo "COMMERCIAL LAUNCH: BLOCKED"
  echo "Release evidence: $RELEASE_LOG"
  echo "E2E evidence: $E2E_LOG"
  echo "Audit evidence: $REPORT"
  exit "$STATUS"
fi

echo "COMMERCIAL LAUNCH: PASS"
echo "Release evidence: $RELEASE_LOG"
echo "E2E evidence: $E2E_LOG"
echo "Audit evidence: $REPORT"
