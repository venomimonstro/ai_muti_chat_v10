#!/usr/bin/env bash
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"
LOG_DIR="${PROJECT_DIR}/logs"

cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

compose() {
  docker compose --ansi never --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

sanitize_failures() {
  local log_file="$1"
  [[ -f "$log_file" ]] || return 0
  grep -E '(^FAILED |^ERROR |^E +|\[FAIL\]|\[ABORT\]|AssertionError|Traceback|CommandError|CHAT_[A-Z_]+)' "$log_file" 2>/dev/null \
    | tail -n 40 \
    | sed -E \
      -e 's#https?://[^[:space:]]+#<url>#g' \
      -e 's/([Aa][Pp][Ii][-_ ]?[Kk][Ee][Yy]|[Tt][Oo][Kk][Ee][Nn]|[Ss][Ee][Cc][Rr][Ee][Tt]|[Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd])([=: ][^[:space:]]+)/\1=<redacted>/g' \
      -e 's/[A-Za-z0-9_=-]{80,}/<redacted-long-value>/g'
}

write_status() {
  local status="$1" exit_code="$2" log_file="$3" phase="$4" failures="$5"
  local commit log_name failures_b64
  commit="$(git -c safe.directory="$PROJECT_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
  log_name="$(basename "$log_file" 2>/dev/null || echo unknown)"
  failures_b64="$(printf '%s' "$failures" | base64 | tr -d '\n')"

  compose exec -T \
    -e UPDATE_STATUS="$status" \
    -e UPDATE_EXIT_CODE="$exit_code" \
    -e UPDATE_PHASE="$phase" \
    -e UPDATE_COMMIT="$commit" \
    -e UPDATE_LOG_NAME="$log_name" \
    -e UPDATE_FAILURES_B64="$failures_b64" \
    backend python -c '
import base64, json, os
from datetime import datetime, timezone
from pathlib import Path
raw = os.environ.get("UPDATE_FAILURES_B64", "")
text = base64.b64decode(raw).decode("utf-8", "replace") if raw else ""
payload = {
    "status": os.environ.get("UPDATE_STATUS", "unknown"),
    "phase": os.environ.get("UPDATE_PHASE", ""),
    "exit_code": int(os.environ.get("UPDATE_EXIT_CODE", "0") or 0),
    "commit": os.environ.get("UPDATE_COMMIT", ""),
    "occurred_at": datetime.now(timezone.utc).isoformat(),
    "log_file": os.environ.get("UPDATE_LOG_NAME", ""),
    "failures": [line[:500] for line in text.splitlines() if line.strip()][:40],
}
path = Path("/app/logs/latest-update-status.json")
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
' >/dev/null 2>&1 || true
}

post_deploy_chat_smoke() {
  local smoke_log="$LOG_DIR/chat-post-deploy-$(date -u +%Y%m%dT%H%M%SZ).log"
  printf '\n[POST-DEPLOY] Проверка реального клиентского пути чата...\n' | tee -a "$smoke_log"
  if ! compose exec -T backend python manage.py chat_preflight_smoke --mode balanced 2>&1 | tee -a "$smoke_log"; then
    printf '[FAIL] Клиентский chat preflight не прошёл.\n' | tee -a "$smoke_log" >&2
    POST_DEPLOY_LOG="$smoke_log"
    return 1
  fi
  if ! compose exec -T backend python manage.py chat_runtime_check --model gigachat-2-pro --live 2>&1 | tee -a "$smoke_log"; then
    printf '[FAIL] Live LLM System smoke не прошёл.\n' | tee -a "$smoke_log" >&2
    POST_DEPLOY_LOG="$smoke_log"
    return 1
  fi
  printf '[PASS] Клиентский preflight и live LLM System работают.\n' | tee -a "$smoke_log"
  POST_DEPLOY_LOG="$smoke_log"
  return 0
}

before="$(find "$LOG_DIR" -maxdepth 1 -type f -name 'update-*.log' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2- || true)"

set +e
bash "$PROJECT_DIR/scripts/update.sh" "$@"
exit_code=$?
set -e

after="$(find "$LOG_DIR" -maxdepth 1 -type f -name 'update-*.log' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2- || true)"
log_file="$after"
[[ -n "$log_file" ]] || log_file="$before"

if [[ "$exit_code" -eq 0 ]]; then
  POST_DEPLOY_LOG=""
  if post_deploy_chat_smoke; then
    phase="8/8 completed + customer chat smoke"
    failures=""
    write_status "passed" 0 "${POST_DEPLOY_LOG:-$log_file}" "$phase" "$failures"
    exit 0
  fi
  exit_code=1
  log_file="${POST_DEPLOY_LOG:-$log_file}"
  phase="post-deploy customer chat smoke"
  failures="$(sanitize_failures "$log_file")"
  write_status "failed" "$exit_code" "$log_file" "$phase" "$failures"
else
  phase="$(grep -E '\[FAIL\] Обновление остановлено на этапе:' "$log_file" 2>/dev/null | tail -n1 | sed -E 's/^.*этапе: //' || true)"
  [[ -n "$phase" ]] || phase="unknown"
  failures="$(sanitize_failures "$log_file")"
  write_status "failed" "$exit_code" "$log_file" "$phase" "$failures"
fi

printf '\n============================================================\n' >&2
printf 'SAFE UPDATE DIAGNOSTICS\n' >&2
printf 'Phase: %s\n' "$phase" >&2
printf 'Exit code: %s\n' "$exit_code" >&2
if [[ -n "$failures" ]]; then
  printf '%s\n' "$failures" >&2
else
  printf 'Краткая ошибка не распознана; полный лог сохранён локально.\n' >&2
fi
printf 'Диагностический статус сохранён для админки.\n' >&2
printf '============================================================\n' >&2
exit "$exit_code"
