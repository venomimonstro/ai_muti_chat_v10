#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

kind="${1:-}"
evidence="${2:-}"
checksum="${3:-}"
case "$kind" in
  provider_outage|duplicate_webhook|payment_failure|refund) ;;
  *) echo "Usage: $0 {provider_outage|duplicate_webhook|payment_failure|refund} EVIDENCE_REFERENCE [SHA256]" >&2; exit 2 ;;
esac
[[ -n "$evidence" ]] || { echo "Evidence reference is required" >&2; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo ".env.production not found" >&2; exit 3; }

args=(python manage.py record_commercial_drill "$kind" --evidence "$evidence")
if [[ -n "$checksum" ]]; then args+=(--checksum "$checksum"); fi
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T backend "${args[@]}"
printf 'Recorded reviewed drill evidence: %s\n' "$kind"
