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
SYSTEM_LOG="${EVIDENCE_DIR}/system-health-${STAMP}.json"

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

printf 'Running production system health gate...\n'
set +e
compose exec -T backend python manage.py system_health_check --strict --json >"${SYSTEM_LOG}.tmp"
SYSTEM_STATUS=$?
set -e
mv "${SYSTEM_LOG}.tmp" "$SYSTEM_LOG"
sha256sum "$SYSTEM_LOG" >"${SYSTEM_LOG}.sha256"
if [[ $SYSTEM_STATUS -ne 0 ]]; then
  echo "COMMERCIAL LAUNCH: BLOCKED BY SYSTEM HEALTH"
  echo "Evidence: $SYSTEM_LOG"
  exit "$SYSTEM_STATUS"
fi

printf 'Running commercial client-cabinet E2E...\n'
set +e
E2E_BASE_URL="https://${APP_DOMAIN}" bash "$PROJECT_DIR/scripts/commercial_e2e_smoke.sh" >"${E2E_LOG}.tmp" 2>&1
CABINET_E2E_STATUS=$?
set -e
if [[ $CABINET_E2E_STATUS -ne 0 ]]; then
  mv "${E2E_LOG}.tmp" "$E2E_LOG"
  echo "COMMERCIAL LAUNCH: BLOCKED BY CLIENT E2E"; echo "Evidence: $E2E_LOG"; exit "$CABINET_E2E_STATUS"
fi

if [[ -z "${E2E_USERNAME:-}" || -z "${E2E_PASSWORD:-}" ]]; then
  {
    echo
    echo 'LIVE E2E: BLOCKED'
    echo 'Set E2E_USERNAME and E2E_PASSWORD for a dedicated verified non-admin account with a small positive balance.'
  } >>"${E2E_LOG}.tmp"
  mv "${E2E_LOG}.tmp" "$E2E_LOG"
  sha256sum "$E2E_LOG" >"${E2E_LOG}.sha256"
  echo "COMMERCIAL LAUNCH: BLOCKED BY LIVE E2E CREDENTIALS"
  echo "Evidence: $E2E_LOG"
  exit 2
fi

printf 'Running live paid workspace AI provider/billing E2E...\n'
set +e
compose run --rm -T \
  -v "${PROJECT_DIR}/scripts:/opt/aiws-scripts:ro" \
  -e "E2E_BASE_URL=https://${APP_DOMAIN}" \
  -e "E2E_USERNAME=${E2E_USERNAME}" \
  -e "E2E_PASSWORD=${E2E_PASSWORD}" \
  backend python /opt/aiws-scripts/commercial_http_smoke.py >>"${E2E_LOG}.tmp" 2>&1
LIVE_AI_STATUS=$?
set -e
if [[ $LIVE_AI_STATUS -ne 0 ]]; then
  mv "${E2E_LOG}.tmp" "$E2E_LOG"
  sha256sum "$E2E_LOG" >"${E2E_LOG}.sha256"
  echo "COMMERCIAL LAUNCH: BLOCKED BY LIVE WORKSPACE AI E2E"; echo "Evidence: $E2E_LOG"; exit "$LIVE_AI_STATUS"
fi

printf 'Running live OpenAI-compatible B2B API/billing E2E...\n'
set +e
compose run --rm -T \
  -v "${PROJECT_DIR}/scripts:/opt/aiws-scripts:ro" \
  -e "E2E_BASE_URL=https://${APP_DOMAIN}" \
  -e "E2E_USERNAME=${E2E_USERNAME}" \
  -e "E2E_PASSWORD=${E2E_PASSWORD}" \
  backend python /opt/aiws-scripts/b2b_http_smoke.py >>"${E2E_LOG}.tmp" 2>&1
B2B_STATUS=$?
set -e
mv "${E2E_LOG}.tmp" "$E2E_LOG"
E2E_SHA="$(sha256sum "$E2E_LOG" | awk '{print $1}')"
printf '%s  %s\n' "$E2E_SHA" "$E2E_LOG" >"${E2E_LOG}.sha256"
if [[ $B2B_STATUS -ne 0 ]]; then
  echo "COMMERCIAL LAUNCH: BLOCKED BY LIVE B2B API E2E"; echo "Evidence: $E2E_LOG"; exit "$B2B_STATUS"
fi
compose exec -T backend python manage.py record_commercial_drill commercial_e2e \
  --evidence "$E2E_LOG" --checksum "$E2E_SHA" \
  --notes "Client cabinet, paid workspace AI and paid OpenAI-compatible B2B API paths passed" >/dev/null

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
  echo "System health evidence: $SYSTEM_LOG"
  echo "E2E evidence: $E2E_LOG"
  echo "Audit evidence: $REPORT"
  exit "$STATUS"
fi

echo "COMMERCIAL LAUNCH: PASS"
echo "Release evidence: $RELEASE_LOG"
echo "System health evidence: $SYSTEM_LOG"
echo "E2E evidence: $E2E_LOG"
echo "Audit evidence: $REPORT"
