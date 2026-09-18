#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
EVIDENCE_DIR="${PROJECT_DIR}/launch-evidence"
BACKUP_DIR="${PROJECT_DIR}/backups"

[[ "${1:-}" == "--confirm-battle-test" ]] || {
  echo "Refusing battle test without --confirm-battle-test" >&2
  echo "Usage: sudo bash scripts/battle_test.sh --confirm-battle-test" >&2
  exit 2
}
[[ -f "$ENV_FILE" ]] || { echo ".env.production not found" >&2; exit 3; }
mkdir -p "$EVIDENCE_DIR" "$BACKUP_DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
MASTER_LOG="${EVIDENCE_DIR}/battle-test-${STAMP}.log"
DB_BACKUP="${BACKUP_DIR}/battle-test-${STAMP}.dump"
MEDIA_BACKUP="${BACKUP_DIR}/battle-test-media-${STAMP}.tar.gz"

exec > >(tee -a "$MASTER_LOG") 2>&1

printf '=== AI Workspace battle test %s ===\n' "$STAMP"
printf '[1/6] Release/build/test gate\n'
bash "$PROJECT_DIR/scripts/release_check.sh"

printf '[2/6] Production load/chaos/paid E2E drills\n'
bash "$PROJECT_DIR/scripts/production_drills.sh" --confirm-production-drills

printf '[3/6] Fresh database/media backups\n'
bash "$PROJECT_DIR/scripts/backup_postgres.sh" "$DB_BACKUP"
bash "$PROJECT_DIR/scripts/backup_media.sh" "$MEDIA_BACKUP"

printf '[4/6] Database restore drill from fresh verified backup\n'
bash "$PROJECT_DIR/scripts/restore_drill.sh" "$DB_BACKUP"

printf '[5/6] Application rollback drill\n'
bash "$PROJECT_DIR/scripts/rollback_drill.sh" --confirm-rollback-drill

printf '[6/6] Final commercial launch gate\n'
bash "$PROJECT_DIR/scripts/commercial_launch_check.sh"

sha256sum "$MASTER_LOG" >"${MASTER_LOG}.sha256"
printf '\nBATTLE TEST: PASS\nEvidence: %s\n' "$MASTER_LOG"
printf 'Database backup: %s\nMedia backup: %s\n' "$DB_BACKUP" "$MEDIA_BACKUP"
printf 'Before public traffic, ensure provider_outage, duplicate_webhook, payment_failure and refund drills are also recorded in the operational evidence registry.\n'
