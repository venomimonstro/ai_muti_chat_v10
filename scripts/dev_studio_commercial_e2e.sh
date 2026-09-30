#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
EVIDENCE_DIR="${PROJECT_DIR}/launch-evidence"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGETED_LOG="${EVIDENCE_DIR}/dev-studio-targeted-${STAMP}.log"
AUDIT_LOG="${EVIDENCE_DIR}/dev-studio-live-audit-${STAMP}.log"
RECOVERY_LOG="${EVIDENCE_DIR}/dev-studio-recovery-${STAMP}.log"
RUN_LOG="${EVIDENCE_DIR}/dev-studio-run-${STAMP}.log"

cd "$PROJECT_DIR"
mkdir -p "$EVIDENCE_DIR"
umask 077
[[ "${EUID}" -eq 0 ]] || { echo 'Run with sudo/root' >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo '.env.production not found' >&2; exit 2; }
[[ -d .git ]] || { echo '.git repository not found' >&2; exit 2; }
[[ -n "${DEV_E2E_RUN_ID:-}" ]] || {
  echo 'DEV STUDIO E2E: BLOCKED — set DEV_E2E_RUN_ID to a completed real Dev Studio run.' >&2
  exit 2
}

compose(){ docker compose --ansi never --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
checksum(){ sha256sum "$1" >"$1.sha256"; }

printf '[1/4] Targeted Dev Studio v2 gate\n'
set +e
bash scripts/dev_studio_check.sh >"${TARGETED_LOG}.tmp" 2>&1
STATUS=$?
set -e
mv "${TARGETED_LOG}.tmp" "$TARGETED_LOG"
checksum "$TARGETED_LOG"
[[ $STATUS -eq 0 ]] || { echo "DEV STUDIO E2E: BLOCKED BY TARGETED GATE — $TARGETED_LOG" >&2; exit "$STATUS"; }
grep -Fq 'DEV_STUDIO_V2_CHECK=PASS' "$TARGETED_LOG" || { echo 'Targeted gate missing PASS marker' >&2; exit 1; }

printf '[2/4] Production Dev readiness and billing audits\n'
set +e
{
  compose exec -T backend python manage.py dev_studio_audit &&
  compose exec -T backend python manage.py agent_billing_audit &&
  compose exec -T backend python manage.py economic_safety_check
} >"${AUDIT_LOG}.tmp" 2>&1
STATUS=$?
set -e
mv "${AUDIT_LOG}.tmp" "$AUDIT_LOG"
checksum "$AUDIT_LOG"
[[ $STATUS -eq 0 ]] || { echo "DEV STUDIO E2E: BLOCKED BY PRODUCTION AUDIT — $AUDIT_LOG" >&2; exit "$STATUS"; }

printf '[3/4] Stale-run recovery dry-run\n'
set +e
compose exec -T backend python manage.py dev_studio_recover >"${RECOVERY_LOG}.tmp" 2>&1
STATUS=$?
set -e
mv "${RECOVERY_LOG}.tmp" "$RECOVERY_LOG"
checksum "$RECOVERY_LOG"
[[ $STATUS -eq 0 ]] || {
  echo 'DEV STUDIO E2E: BLOCKED BY STALE RUNS. Inspect evidence; do not auto-replay side effects.' >&2
  echo "Evidence: $RECOVERY_LOG" >&2
  exit "$STATUS"
}

printf '[4/4] Completed real Dev journey evidence\n'
set +e
compose exec -T backend python manage.py dev_studio_e2e_audit --run-id "$DEV_E2E_RUN_ID" >"${RUN_LOG}.tmp" 2>&1
STATUS=$?
set -e
mv "${RUN_LOG}.tmp" "$RUN_LOG"
checksum "$RUN_LOG"
[[ $STATUS -eq 0 ]] || { echo "DEV STUDIO E2E: BLOCKED BY RUN EVIDENCE — $RUN_LOG" >&2; exit "$STATUS"; }
grep -Fq 'DEV_STUDIO_COMMERCIAL_E2E_OK' "$RUN_LOG" || { echo 'Run audit missing PASS marker' >&2; exit 1; }

printf 'DEV STUDIO COMMERCIAL E2E: PASS\n'
printf 'Targeted evidence: %s\n' "$TARGETED_LOG"
printf 'Audit evidence: %s\n' "$AUDIT_LOG"
printf 'Recovery evidence: %s\n' "$RECOVERY_LOG"
printf 'Run evidence: %s\n' "$RUN_LOG"
