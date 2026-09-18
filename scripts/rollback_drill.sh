#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

[[ "${1:-}" == "--confirm-rollback-drill" ]] || {
  echo "Refusing rollback drill without --confirm-rollback-drill" >&2
  exit 2
}
[[ -f "$ENV_FILE" ]] || { echo ".env.production not found" >&2; exit 3; }
[[ -d "$PROJECT_DIR/.git" ]] || { echo "Rollback drill requires a git checkout" >&2; exit 4; }
cd "$PROJECT_DIR"
git -c safe.directory="$PROJECT_DIR" diff --quiet || { echo "Working tree must be clean" >&2; exit 5; }

CURRENT_SHA="$(git -c safe.directory="$PROJECT_DIR" rev-parse HEAD)"
PREVIOUS_SHA="$(git -c safe.directory="$PROJECT_DIR" rev-parse HEAD^ 2>/dev/null || true)"
[[ -n "$PREVIOUS_SHA" ]] || { echo "No previous commit available" >&2; exit 6; }
compose(){ docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"; }
wait_ready(){
  local attempts=0
  until compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/', timeout=3)" >/dev/null 2>&1; do
    attempts=$((attempts+1)); [[ $attempts -lt 60 ]] || return 1; sleep 2
  done
}
restore_current(){
  git -c safe.directory="$PROJECT_DIR" reset --hard "$CURRENT_SHA" >/dev/null 2>&1 || true
  compose build >/dev/null 2>&1 || true
  compose up -d --remove-orphans >/dev/null 2>&1 || true
  wait_ready >/dev/null 2>&1 || true
}
trap restore_current EXIT ERR

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$PROJECT_DIR/backups"
mkdir -p "$BACKUP_DIR"
umask 077
bash "$PROJECT_DIR/scripts/backup_postgres.sh" "$BACKUP_DIR/rollback-drill-${STAMP}.dump" >/dev/null
bash "$PROJECT_DIR/scripts/backup_media.sh" "$BACKUP_DIR/rollback-drill-media-${STAMP}.tar.gz" >/dev/null

printf 'Testing rollback %s -> %s\n' "$CURRENT_SHA" "$PREVIOUS_SHA"
git -c safe.directory="$PROJECT_DIR" reset --hard "$PREVIOUS_SHA"
compose build
compose up -d --remove-orphans
wait_ready

printf 'Restoring current release %s\n' "$CURRENT_SHA"
git -c safe.directory="$PROJECT_DIR" reset --hard "$CURRENT_SHA"
compose build
compose up -d --remove-orphans
wait_ready
compose exec -T backend python manage.py verify_financial_invariants
compose exec -T backend python manage.py record_operational_drill rollback \
  --evidence "rollback:${CURRENT_SHA}->${PREVIOUS_SHA}->${CURRENT_SHA}" \
  --commit-sha "$PREVIOUS_SHA"

trap - EXIT ERR
echo "ROLLBACK DRILL: PASS"
