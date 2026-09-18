#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
EVIDENCE_DIR="${PROJECT_DIR}/launch-evidence"

[[ "${1:-}" == "--confirm-production-drills" ]] || {
  echo "Refusing production drills without --confirm-production-drills" >&2
  exit 2
}
[[ -f "$ENV_FILE" ]] || { echo ".env.production not found" >&2; exit 3; }
mkdir -p "$EVIDENCE_DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

compose(){ docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
run_log(){
  local name="$1"; shift
  local log="${EVIDENCE_DIR}/${name}-${STAMP}.log"
  "$@" >"$log.tmp" 2>&1
  mv "$log.tmp" "$log"
  sha256sum "$log" >"${log}.sha256"
  printf '%s' "$log"
}

printf '[1/4] Bounded readiness load smoke\n'
LOAD_LOG="$(run_log load-smoke python3 "$PROJECT_DIR/scripts/load_smoke.py" --base-url "https://${APP_DOMAIN}" --requests "${DRILL_LOAD_REQUESTS:-200}" --concurrency "${DRILL_LOAD_CONCURRENCY:-20}" --max-error-rate "${DRILL_MAX_ERROR_RATE:-0.01}" --max-p95-ms "${DRILL_MAX_P95_MS:-1200}")"

printf '[2/4] Service restart chaos smoke\n'
CHAOS_LOG="$(run_log chaos-smoke bash "$PROJECT_DIR/scripts/chaos_smoke.sh" --confirm-chaos)"

printf '[3/4] Commercial HTTP smoke\n'
E2E_LOG="$(run_log commercial-e2e env E2E_BASE_URL="https://${APP_DOMAIN}" bash "$PROJECT_DIR/scripts/commercial_e2e_smoke.sh")"

printf '[4/4] Financial invariants\n'
FIN_LOG="$(run_log financial-invariants docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T backend python manage.py verify_financial_invariants)"

cat >"${EVIDENCE_DIR}/drill-summary-${STAMP}.json" <<JSON
{
  "timestamp_utc": "${STAMP}",
  "load_smoke": "${LOAD_LOG}",
  "chaos_smoke": "${CHAOS_LOG}",
  "commercial_e2e": "${E2E_LOG}",
  "financial_invariants": "${FIN_LOG}",
  "status": "pass"
}
JSON
sha256sum "${EVIDENCE_DIR}/drill-summary-${STAMP}.json" >"${EVIDENCE_DIR}/drill-summary-${STAMP}.json.sha256"

printf 'PRODUCTION DRILLS: PASS\n'
printf 'Evidence: %s\n' "${EVIDENCE_DIR}/drill-summary-${STAMP}.json"
