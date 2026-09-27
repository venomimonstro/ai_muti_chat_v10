#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_DIR="${PROJECT_DIR}/launch-evidence"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
mkdir -p "$EVIDENCE_DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DRILL_LOG="${EVIDENCE_DIR}/agent-runtime-drill-${STAMP}.log"
LAUNCH_LOG="${EVIDENCE_DIR}/agent-commercial-launch-${STAMP}.log"
FINAL_AUDIT_LOG="${EVIDENCE_DIR}/agent-final-audits-${STAMP}.log"
MANIFEST="${EVIDENCE_DIR}/agent-rc-v1-${STAMP}.manifest"

cd "$PROJECT_DIR"
[[ "${EUID}" -eq 0 ]] || { echo 'Run with sudo/root' >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo '.env.production not found' >&2; exit 1; }
[[ -d .git ]] || { echo '.git repository not found' >&2; exit 1; }
if [[ -n "$(git -c safe.directory="$PROJECT_DIR" status --porcelain)" ]]; then
  echo 'AGENT RELEASE CANDIDATE: BLOCKED BY DIRTY WORKTREE' >&2
  git -c safe.directory="$PROJECT_DIR" status --short >&2
  exit 1
fi
RC_COMMIT="$(git -c safe.directory="$PROJECT_DIR" rev-parse HEAD)"

compose(){ docker compose --ansi never --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
checksum(){ sha256sum "$1" >"$1.sha256"; }

printf '[1/4] Agent/Dev runtime restart and recovery drill\n'
set +e
bash scripts/agent_runtime_drill.sh >"${DRILL_LOG}.tmp" 2>&1
DRILL_STATUS=$?
set -e
mv "${DRILL_LOG}.tmp" "$DRILL_LOG"
checksum "$DRILL_LOG"
if [[ $DRILL_STATUS -ne 0 ]]; then
  echo "AGENT RELEASE CANDIDATE: BLOCKED BY RUNTIME DRILL"
  echo "Evidence: $DRILL_LOG"
  exit "$DRILL_STATUS"
fi
if ! grep -Fq 'AGENT RUNTIME DRILL: PASS' "$DRILL_LOG"; then
  echo 'AGENT RELEASE CANDIDATE: RUNTIME DRILL MISSING PASS MARKER' >&2
  exit 1
fi

printf '[2/4] Commercial launch gate with live E2E and billing evidence\n'
set +e
bash scripts/commercial_launch_check.sh >"${LAUNCH_LOG}.tmp" 2>&1
LAUNCH_STATUS=$?
set -e
mv "${LAUNCH_LOG}.tmp" "$LAUNCH_LOG"
checksum "$LAUNCH_LOG"
if [[ $LAUNCH_STATUS -ne 0 ]]; then
  echo "AGENT RELEASE CANDIDATE: BLOCKED BY COMMERCIAL LAUNCH GATE"
  echo "Runtime evidence: $DRILL_LOG"
  echo "Launch evidence: $LAUNCH_LOG"
  exit "$LAUNCH_STATUS"
fi
if ! grep -Fq 'COMMERCIAL LAUNCH: PASS' "$LAUNCH_LOG"; then
  echo 'AGENT RELEASE CANDIDATE: COMMERCIAL GATE MISSING PASS MARKER' >&2
  exit 1
fi

printf '[3/4] Final production Agent/Dev audits\n'
set +e
{
  compose exec -T backend python manage.py agent_system_audit &&
  compose exec -T backend python manage.py agent_webhook_audit &&
  compose exec -T backend python manage.py agent_security_audit &&
  compose exec -T backend python manage.py agent_commercial_limits_audit &&
  compose exec -T backend python manage.py agent_recovery_audit &&
  compose exec -T backend python manage.py connection_health_audit &&
  compose exec -T backend python manage.py dev_studio_audit &&
  compose exec -T backend python manage.py agent_billing_audit
} >"${FINAL_AUDIT_LOG}.tmp" 2>&1
FINAL_AUDIT_STATUS=$?
set -e
mv "${FINAL_AUDIT_LOG}.tmp" "$FINAL_AUDIT_LOG"
checksum "$FINAL_AUDIT_LOG"
if [[ $FINAL_AUDIT_STATUS -ne 0 ]]; then
  echo 'AGENT RELEASE CANDIDATE: BLOCKED BY FINAL AGENT/DEV AUDITS'
  echo "Final audit evidence: $FINAL_AUDIT_LOG"
  exit "$FINAL_AUDIT_STATUS"
fi

printf '[4/4] Immutable RC evidence manifest\n'
DRILL_SHA="$(sha256sum "$DRILL_LOG" | awk '{print $1}')"
LAUNCH_SHA="$(sha256sum "$LAUNCH_LOG" | awk '{print $1}')"
AUDIT_SHA="$(sha256sum "$FINAL_AUDIT_LOG" | awk '{print $1}')"
{
  printf 'release_candidate=agent-v1.0\n'
  printf 'created_at_utc=%s\n' "$STAMP"
  printf 'git_commit=%s\n' "$RC_COMMIT"
  printf 'runtime_evidence=%s\n' "$DRILL_LOG"
  printf 'runtime_sha256=%s\n' "$DRILL_SHA"
  printf 'commercial_evidence=%s\n' "$LAUNCH_LOG"
  printf 'commercial_sha256=%s\n' "$LAUNCH_SHA"
  printf 'final_audit_evidence=%s\n' "$FINAL_AUDIT_LOG"
  printf 'final_audit_sha256=%s\n' "$AUDIT_SHA"
  printf 'runtime_drill_script_sha256=%s\n' "$(sha256sum scripts/agent_runtime_drill.sh | awk '{print $1}')"
  printf 'commercial_gate_script_sha256=%s\n' "$(sha256sum scripts/commercial_launch_check.sh | awk '{print $1}')"
  printf 'rc_script_sha256=%s\n' "$(sha256sum scripts/agent_release_candidate_check.sh | awk '{print $1}')"
} >"${MANIFEST}.tmp"
mv "${MANIFEST}.tmp" "$MANIFEST"
checksum "$MANIFEST"

# Verify the evidence bundle once more before printing the only RC PASS marker.
sha256sum -c "${DRILL_LOG}.sha256" >/dev/null
sha256sum -c "${LAUNCH_LOG}.sha256" >/dev/null
sha256sum -c "${FINAL_AUDIT_LOG}.sha256" >/dev/null
sha256sum -c "${MANIFEST}.sha256" >/dev/null

printf 'AGENT RELEASE CANDIDATE v1.0: PASS\n'
printf 'Commit: %s\n' "$RC_COMMIT"
printf 'Runtime evidence: %s\n' "$DRILL_LOG"
printf 'Launch evidence: %s\n' "$LAUNCH_LOG"
printf 'Final audit evidence: %s\n' "$FINAL_AUDIT_LOG"
printf 'Manifest: %s\n' "$MANIFEST"
