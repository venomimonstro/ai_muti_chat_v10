#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
EVIDENCE_DIR="${PROJECT_DIR}/launch-evidence"

[[ "${1:-}" == "--confirm-production-drills" ]] || { echo "Refusing production drills without --confirm-production-drills" >&2; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo ".env.production not found" >&2; exit 3; }
mkdir -p "$EVIDENCE_DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
[[ -n "${E2E_USERNAME:-}" && -n "${E2E_PASSWORD:-}" ]] || {
  echo "Production drills require E2E_USERNAME/E2E_PASSWORD for a verified test account with small positive balance" >&2
  exit 4
}
compose(){ docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
run_log(){ local name="$1"; shift; local log="${EVIDENCE_DIR}/${name}-${STAMP}.log"; "$@" >"$log.tmp" 2>&1; mv "$log.tmp" "$log"; sha256sum "$log" >"${log}.sha256"; printf '%s' "$log"; }
record(){ local kind="$1"; local file="$2"; local checksum; checksum="$(sha256sum "$file" | awk '{print $1}')"; compose exec -T backend python manage.py record_commercial_drill "$kind" --evidence "$file" --checksum "$checksum" >/dev/null; }

printf '[1/6] Bounded readiness load smoke\n'
LOAD_LOG="$(run_log load-smoke python3 "$PROJECT_DIR/scripts/load_smoke.py" --base-url "https://${APP_DOMAIN}" --requests "${DRILL_LOAD_REQUESTS:-200}" --concurrency "${DRILL_LOAD_CONCURRENCY:-20}" --max-error-rate "${DRILL_MAX_ERROR_RATE:-0.01}" --max-p95-ms "${DRILL_MAX_P95_MS:-1200}")"
record load "$LOAD_LOG"

printf '[2/6] Service restart chaos smoke\n'
CHAOS_LOG="$(run_log chaos-smoke bash "$PROJECT_DIR/scripts/chaos_smoke.sh" --confirm-chaos)"
record chaos "$CHAOS_LOG"
record stale_recovery "$CHAOS_LOG"

printf '[3/6] Client-cabinet E2E\n'
CABINET_LOG="$(run_log client-e2e env E2E_BASE_URL="https://${APP_DOMAIN}" bash "$PROJECT_DIR/scripts/commercial_e2e_smoke.sh")"

printf '[4/6] Real paid AI provider/billing E2E\n'
AI_LOG="${EVIDENCE_DIR}/paid-ai-e2e-${STAMP}.log"
compose run --rm -T \
  -v "${PROJECT_DIR}/scripts:/opt/aiws-scripts:ro" \
  -e "E2E_BASE_URL=https://${APP_DOMAIN}" \
  -e "E2E_USERNAME=${E2E_USERNAME}" \
  -e "E2E_PASSWORD=${E2E_PASSWORD}" \
  backend python /opt/aiws-scripts/commercial_http_smoke.py >"${AI_LOG}.tmp" 2>&1
mv "${AI_LOG}.tmp" "$AI_LOG"
sha256sum "$AI_LOG" >"${AI_LOG}.sha256"
record commercial_e2e "$AI_LOG"

printf '[5/6] Real paid B2B OpenAI-compatible E2E\n'
B2B_LOG="${EVIDENCE_DIR}/b2b-e2e-${STAMP}.log"
compose run --rm -T \
  -v "${PROJECT_DIR}/scripts:/opt/aiws-scripts:ro" \
  -e "E2E_BASE_URL=https://${APP_DOMAIN}" \
  -e "E2E_USERNAME=${E2E_USERNAME}" \
  -e "E2E_PASSWORD=${E2E_PASSWORD}" \
  backend python /opt/aiws-scripts/b2b_http_smoke.py >"${B2B_LOG}.tmp" 2>&1
mv "${B2B_LOG}.tmp" "$B2B_LOG"
sha256sum "$B2B_LOG" >"${B2B_LOG}.sha256"

printf '[6/6] Financial invariants\n'
FIN_LOG="$(run_log financial-invariants docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T backend python manage.py verify_financial_invariants)"

SUMMARY="${EVIDENCE_DIR}/drill-summary-${STAMP}.json"
cat >"$SUMMARY" <<JSON
{"timestamp_utc":"${STAMP}","load_smoke":"${LOAD_LOG}","chaos_smoke":"${CHAOS_LOG}","client_e2e":"${CABINET_LOG}","paid_ai_e2e":"${AI_LOG}","b2b_e2e":"${B2B_LOG}","financial_invariants":"${FIN_LOG}","status":"pass"}
JSON
sha256sum "$SUMMARY" >"${SUMMARY}.sha256"
printf 'PRODUCTION DRILLS: PASS\nEvidence: %s\n' "$SUMMARY"
printf 'Manual drills still required before launch: provider_outage, duplicate_webhook, payment_failure, refund, plus restore and rollback evidence.\n'
