#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_DIR="${PROJECT_DIR}/launch-evidence"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
E2E_LOG="${EVIDENCE_DIR}/dev-studio-rc-e2e-${STAMP}.log"
FINAL_LOG="${EVIDENCE_DIR}/dev-studio-rc-final-${STAMP}.log"
MANIFEST="${EVIDENCE_DIR}/dev-studio-rc-${STAMP}.manifest"

cd "$PROJECT_DIR"
mkdir -p "$EVIDENCE_DIR"
umask 077
[[ "${EUID}" -eq 0 ]] || { echo 'Run with sudo/root' >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo '.env.production not found' >&2; exit 2; }
[[ -d .git ]] || { echo '.git repository not found' >&2; exit 2; }
[[ -n "${DEV_E2E_RUN_ID:-}" ]] || { echo 'Set DEV_E2E_RUN_ID to a completed real Dev Studio journey.' >&2; exit 2; }
if [[ -n "$(git -c safe.directory="$PROJECT_DIR" status --porcelain)" ]]; then
  echo 'DEV STUDIO RC: BLOCKED BY DIRTY WORKTREE' >&2
  git -c safe.directory="$PROJECT_DIR" status --short >&2
  exit 1
fi
RC_COMMIT="$(git -c safe.directory="$PROJECT_DIR" rev-parse HEAD)"
compose(){ docker compose --ansi never --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
checksum(){ sha256sum "$1" >"$1.sha256"; }

printf '[1/3] Commercial Dev Studio E2E\n'
set +e
bash scripts/dev_studio_commercial_e2e.sh >"${E2E_LOG}.tmp" 2>&1
STATUS=$?
set -e
mv "${E2E_LOG}.tmp" "$E2E_LOG"
checksum "$E2E_LOG"
[[ $STATUS -eq 0 ]] || { echo "DEV STUDIO RC: BLOCKED BY E2E — $E2E_LOG" >&2; exit "$STATUS"; }
grep -Fq 'DEV STUDIO COMMERCIAL E2E: PASS' "$E2E_LOG" || { echo 'Dev E2E missing PASS marker' >&2; exit 1; }

printf '[2/3] Final Dev security/reliability/accounting audits\n'
set +e
{
  compose exec -T backend python manage.py dev_studio_audit &&
  compose exec -T backend python manage.py agent_security_audit &&
  compose exec -T backend python manage.py agent_billing_audit &&
  compose exec -T backend python manage.py economic_safety_check &&
  compose exec -T backend python manage.py billing_integrity_check &&
  compose exec -T backend python manage.py dev_studio_recover
} >"${FINAL_LOG}.tmp" 2>&1
STATUS=$?
set -e
mv "${FINAL_LOG}.tmp" "$FINAL_LOG"
checksum "$FINAL_LOG"
[[ $STATUS -eq 0 ]] || { echo "DEV STUDIO RC: BLOCKED BY FINAL AUDITS — $FINAL_LOG" >&2; exit "$STATUS"; }

printf '[3/3] Immutable evidence manifest\n'
E2E_SHA="$(sha256sum "$E2E_LOG" | awk '{print $1}')"
FINAL_SHA="$(sha256sum "$FINAL_LOG" | awk '{print $1}')"
{
  printf 'release_candidate=dev-studio-v2\n'
  printf 'created_at_utc=%s\n' "$STAMP"
  printf 'git_commit=%s\n' "$RC_COMMIT"
  printf 'dev_e2e_run_id=%s\n' "$DEV_E2E_RUN_ID"
  printf 'e2e_evidence=%s\n' "$E2E_LOG"
  printf 'e2e_sha256=%s\n' "$E2E_SHA"
  printf 'final_audit_evidence=%s\n' "$FINAL_LOG"
  printf 'final_audit_sha256=%s\n' "$FINAL_SHA"
  printf 'targeted_gate_sha256=%s\n' "$(sha256sum scripts/dev_studio_check.sh | awk '{print $1}')"
  printf 'commercial_e2e_gate_sha256=%s\n' "$(sha256sum scripts/dev_studio_commercial_e2e.sh | awk '{print $1}')"
  printf 'rc_gate_sha256=%s\n' "$(sha256sum scripts/dev_studio_rc_check.sh | awk '{print $1}')"
} >"${MANIFEST}.tmp"
mv "${MANIFEST}.tmp" "$MANIFEST"
checksum "$MANIFEST"

sha256sum -c "${E2E_LOG}.sha256" >/dev/null
sha256sum -c "${FINAL_LOG}.sha256" >/dev/null
sha256sum -c "${MANIFEST}.sha256" >/dev/null

printf 'DEV STUDIO RC v2: PASS\n'
printf 'Commit: %s\n' "$RC_COMMIT"
printf 'Run: %s\n' "$DEV_E2E_RUN_ID"
printf 'E2E evidence: %s\n' "$E2E_LOG"
printf 'Final audit evidence: %s\n' "$FINAL_LOG"
printf 'Manifest: %s\n' "$MANIFEST"
