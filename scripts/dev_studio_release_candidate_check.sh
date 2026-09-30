#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROD_COMPOSE="${PROJECT_DIR}/docker-compose.prod.yml"
ENV_FILE="${PROJECT_DIR}/.env.production"
LOG_DIR="${PROJECT_DIR}/logs"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_FILE="${LOG_DIR}/dev-studio-rc-${RUN_ID}.log"
CHECKSUM_FILE="${EVIDENCE_FILE}.sha256"
DEV_RUN_ID="${DEV_RUN_ID:-}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"
exec > >(tee -a "$EVIDENCE_FILE") 2>&1

printf '\n=== DEV STUDIO RELEASE CANDIDATE ===\n'
printf 'commit=%s\n' "$(git rev-parse HEAD)"
printf 'started_at=%s\n' "$(date -u +%FT%TZ)"

printf '\n[1/5] Targeted Dev Studio gate\n'
bash ./scripts/dev_studio_check.sh
printf '[PASS] Targeted Dev Studio gate\n'

if [[ ! -f "$ENV_FILE" ]]; then
  echo '[FAIL] .env.production not found; live RC evidence cannot be collected.' >&2
  exit 1
fi

compose=(sudo docker compose --env-file "$ENV_FILE" -f "$PROD_COMPOSE")

printf '\n[2/5] Production runtime audit\n'
"${compose[@]}" exec -T backend python manage.py dev_studio_runtime_audit
printf '[PASS] Production runtime audit\n'

printf '\n[3/5] Stale-run recovery audit\n'
"${compose[@]}" exec -T backend python manage.py dev_studio_recover --older-than-seconds 14400
printf '[PASS] No stale Dev execution\n'

printf '\n[4/5] Commercial Dev journey evidence\n'
if [[ -z "$DEV_RUN_ID" ]]; then
  echo '[FAIL] DEV_RUN_ID is required for Sprint 89/90 commercial E2E evidence.' >&2
  echo 'Run one real Dev Studio task through approval, sandbox, isolated branch, QA and Final Review, then export DEV_RUN_ID=<uuid>.' >&2
  exit 1
fi
"${compose[@]}" exec -T backend python manage.py dev_studio_e2e_audit --run-id "$DEV_RUN_ID"
printf '[PASS] Commercial Dev journey evidence\n'

printf '\n[5/5] Evidence integrity\n'
printf 'finished_at=%s\n' "$(date -u +%FT%TZ)"
printf 'DEV_STUDIO_RELEASE_CANDIDATE=PASS\n'
sha256sum "$EVIDENCE_FILE" > "$CHECKSUM_FILE"
printf 'evidence=%s\nchecksum=%s\n' "$EVIDENCE_FILE" "$CHECKSUM_FILE"
printf '\nDEV STUDIO RELEASE CANDIDATE: PASS\n'
