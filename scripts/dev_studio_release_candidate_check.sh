#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROD_COMPOSE="${PROJECT_DIR}/docker-compose.prod.yml"
ENV_FILE="${PROJECT_DIR}/.env.production"
LOG_DIR="${PROJECT_DIR}/logs"
EVIDENCE_ID="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_FILE="${LOG_DIR}/dev-studio-rc-${EVIDENCE_ID}.log"
CHECKSUM_FILE="${EVIDENCE_FILE}.sha256"
MANIFEST_FILE="${EVIDENCE_FILE%.log}.json"
DEV_RUN_ID="${DEV_RUN_ID:-}"
COMMIT_SHA="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
STARTED_AT="$(date -u +%FT%TZ)"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

run_checks() {
  printf '\n=== DEV STUDIO RELEASE CANDIDATE ===\n'
  printf 'commit=%s\n' "$COMMIT_SHA"
  printf 'dev_run_id=%s\n' "${DEV_RUN_ID:-missing}"
  printf 'started_at=%s\n' "$STARTED_AT"

  printf '\n[1/5] Targeted Dev Studio gate\n'
  bash ./scripts/dev_studio_check.sh
  printf '[PASS] Targeted Dev Studio gate\n'

  if [[ ! -f "$ENV_FILE" ]]; then
    echo '[FAIL] .env.production not found; live RC evidence cannot be collected.' >&2
    return 1
  fi

  local compose=(sudo docker compose --env-file "$ENV_FILE" -f "$PROD_COMPOSE")

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
    return 1
  fi
  "${compose[@]}" exec -T backend python manage.py dev_studio_e2e_audit --run-id "$DEV_RUN_ID"
  printf '[PASS] Commercial Dev journey evidence\n'

  printf '\n[5/5] Evidence integrity precondition\n'
  printf 'finished_at=%s\n' "$(date -u +%FT%TZ)"
  printf 'DEV_STUDIO_RELEASE_CANDIDATE=PASS\n'
}

set +e
set +o pipefail
run_checks 2>&1 | tee "$EVIDENCE_FILE"
CHECK_EXIT="${PIPESTATUS[0]}"
set -o pipefail
set -e

if [[ "$CHECK_EXIT" -ne 0 ]]; then
  printf '\nDEV STUDIO RELEASE CANDIDATE: FAIL (exit=%s)\n' "$CHECK_EXIT" >&2
  printf 'evidence=%s\n' "$EVIDENCE_FILE" >&2
  exit "$CHECK_EXIT"
fi

# The evidence log is complete at this point. Never append to it after hashing.
EVIDENCE_SHA256="$(sha256sum "$EVIDENCE_FILE" | awk '{print $1}')"
printf '%s  %s\n' "$EVIDENCE_SHA256" "$EVIDENCE_FILE" > "$CHECKSUM_FILE"
FINISHED_AT="$(date -u +%FT%TZ)"

python3 - "$MANIFEST_FILE" "$COMMIT_SHA" "$DEV_RUN_ID" "$STARTED_AT" "$FINISHED_AT" "$EVIDENCE_FILE" "$EVIDENCE_SHA256" <<'PY'
import json
import sys
from pathlib import Path

manifest, commit, run_id, started, finished, evidence, digest = sys.argv[1:]
payload = {
    "schema_version": 1,
    "kind": "dev_studio_release_candidate",
    "status": "PASS",
    "commit_sha": commit,
    "dev_run_id": run_id,
    "started_at": started,
    "finished_at": finished,
    "evidence_file": evidence,
    "evidence_sha256": digest,
}
Path(manifest).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

printf '\nDEV STUDIO RELEASE CANDIDATE: PASS\n'
printf 'evidence=%s\nchecksum=%s\nmanifest=%s\n' "$EVIDENCE_FILE" "$CHECKSUM_FILE" "$MANIFEST_FILE"
