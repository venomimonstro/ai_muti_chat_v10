#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_DIR="${PROJECT_DIR}/launch-evidence"
mkdir -p "$EVIDENCE_DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DRILL_LOG="${EVIDENCE_DIR}/agent-runtime-drill-${STAMP}.log"
LAUNCH_LOG="${EVIDENCE_DIR}/agent-release-candidate-${STAMP}.log"

cd "$PROJECT_DIR"

printf '[1/3] Agent/Dev runtime restart and recovery drill\n'
set +e
bash scripts/agent_runtime_drill.sh >"${DRILL_LOG}.tmp" 2>&1
DRILL_STATUS=$?
set -e
mv "${DRILL_LOG}.tmp" "$DRILL_LOG"
sha256sum "$DRILL_LOG" >"${DRILL_LOG}.sha256"
if [[ $DRILL_STATUS -ne 0 ]]; then
  echo "AGENT RELEASE CANDIDATE: BLOCKED BY RUNTIME DRILL"
  echo "Evidence: $DRILL_LOG"
  exit "$DRILL_STATUS"
fi

printf '[2/3] Commercial launch gate with live E2E and billing evidence\n'
set +e
bash scripts/commercial_launch_check.sh >"${LAUNCH_LOG}.tmp" 2>&1
LAUNCH_STATUS=$?
set -e
mv "${LAUNCH_LOG}.tmp" "$LAUNCH_LOG"
sha256sum "$LAUNCH_LOG" >"${LAUNCH_LOG}.sha256"
if [[ $LAUNCH_STATUS -ne 0 ]]; then
  echo "AGENT RELEASE CANDIDATE: BLOCKED BY COMMERCIAL LAUNCH GATE"
  echo "Runtime evidence: $DRILL_LOG"
  echo "Launch evidence: $LAUNCH_LOG"
  exit "$LAUNCH_STATUS"
fi

printf '[3/3] Final production Agent/Dev audits\n'
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
compose(){ docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
compose exec -T backend python manage.py agent_system_audit
compose exec -T backend python manage.py agent_webhook_audit
compose exec -T backend python manage.py agent_security_audit
compose exec -T backend python manage.py agent_commercial_limits_audit
compose exec -T backend python manage.py agent_recovery_audit
compose exec -T backend python manage.py connection_health_audit
compose exec -T backend python manage.py dev_studio_audit
compose exec -T backend python manage.py agent_billing_audit

echo "AGENT RELEASE CANDIDATE v1.0: PASS"
echo "Runtime evidence: $DRILL_LOG"
echo "Launch evidence: $LAUNCH_LOG"
