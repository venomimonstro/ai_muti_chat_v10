#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${E2E_BASE_URL:-http://localhost:3000}"
API_URL="${E2E_API_URL:-${BASE_URL%/}/api/v1}"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

rand="$(date +%s)-$RANDOM"
username="e2e_${rand}"
email="e2e_${rand}@example.test"
password="CommercialE2E-${rand}-A!"

fail(){ printf 'E2E FAIL: %s\n' "$1" >&2; exit 1; }
json(){ python3 - "$@" <<'PY'
import json,sys
obj=json.load(sys.stdin)
for key in sys.argv[1].split('.'):
    obj=obj[int(key)] if isinstance(obj,list) else obj[key]
print(obj)
PY
}

printf '[1/12] Public pages\n'
for path in / /pricing /faq /login /register /forgot-password /api /use-cases/marketing /use-cases/coding /use-cases/documents; do curl -fsS --max-time 15 "${BASE_URL%/}${path}" >/dev/null || fail "public route ${path}"; done

printf '[2/12] CSRF\n'
csrf_payload="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" "${API_URL%/}/auth/csrf/")"; csrf="$(printf '%s' "$csrf_payload" | json csrf_token)"; [[ -n "$csrf" ]] || fail 'csrf token missing'

printf '[3/12] Registration\n'
register_payload="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H 'Content-Type: application/json' -d "{\"username\":\"$username\",\"email\":\"$email\",\"password\":\"$password\",\"accepted_terms\":true}" "${API_URL%/}/auth/register/")"
[[ "$(printf '%s' "$register_payload" | json email)" == "$email" ]] || fail 'registration payload mismatch'

printf '[4/12] Authenticated workspace APIs\n'
csrf_payload="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" "${API_URL%/}/auth/csrf/")"; csrf="$(printf '%s' "$csrf_payload" | json csrf_token)"
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/auth/me/" >/dev/null || fail 'auth/me'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/models/" >/dev/null || fail 'models'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/wallet/" >/dev/null || fail 'wallet'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/conversation-summaries/" >/dev/null || fail 'conversation summaries'

printf '[5/12] Folder and conversation organization\n'
folder="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H 'Content-Type: application/json' -d '{"name":"E2E folder"}' "${API_URL%/}/conversation-folders/")"; folder_id="$(printf '%s' "$folder" | json id)"; [[ -n "$folder_id" ]] || fail 'folder id missing'
models="$(curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/models/")"; model="$(printf '%s' "$models" | python3 -c 'import json,sys; rows=json.load(sys.stdin); print(next((x["slug"] for x in rows if x.get("available")), ""))')"; [[ -n "$model" ]] || fail 'no available AI model configured'
conversation="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H 'Content-Type: application/json' -d "{\"title\":\"Commercial E2E\",\"routing_mode\":\"balanced\",\"selected_model\":\"$model\"}" "${API_URL%/}/conversations/")"; conversation_id="$(printf '%s' "$conversation" | json id)"; [[ -n "$conversation_id" ]] || fail 'conversation id missing'
curl -fsS -X PATCH -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H 'Content-Type: application/json' -d "{\"folder\":\"$folder_id\",\"is_pinned\":true}" "${API_URL%/}/conversation-ui/${conversation_id}/" >/dev/null || fail 'move/pin conversation'

printf '[6/12] Draft durability\n'
draft='Черновик должен пережить сбой связи'
curl -fsS -X PUT -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H 'Content-Type: application/json' -d "{\"content\":\"$draft\"}" "${API_URL%/}/conversations/${conversation_id}/draft/" >/dev/null || fail 'save draft'
loaded_draft="$(curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/conversations/${conversation_id}/draft/")"; [[ "$(printf '%s' "$loaded_draft" | json content)" == "$draft" ]] || fail 'draft recovery mismatch'

printf '[7/12] Lightweight chat and settings\n'
workspace="$(curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/conversation-workspace/${conversation_id}/?limit=60")"; [[ "$(printf '%s' "$workspace" | json conversation.id)" == "$conversation_id" ]] || fail 'workspace conversation mismatch'
settings_payload="$(curl -fsS -X PATCH -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H 'Content-Type: application/json' -d '{"title":"Commercial E2E renamed","routing_mode":"economy"}' "${API_URL%/}/conversation-settings/${conversation_id}/")"; [[ "$(printf '%s' "$settings_payload" | json title)" == 'Commercial E2E renamed' ]] || fail 'lightweight settings'

printf '[8/12] Optional billable AI request\n'
if [[ "${E2E_BILLABLE:-0}" == "1" ]]; then
  response="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H "Idempotency-Key: e2e:$rand" -H 'Content-Type: application/json' -d "{\"content\":\"Ответь одним словом: тест\",\"client_message_id\":\"$(python3 -c 'import uuid; print(uuid.uuid4())')\"}" "${API_URL%/}/conversations/${conversation_id}/messages/")"; state="$(printf '%s' "$response" | json state)"; [[ "$state" == "completed" || "$state" == "running" ]] || fail "unexpected generation state: $state"
else printf 'Billable generation skipped.\n'; fi

printf '[9/12] Usage, account and payments\n'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/auth/usage/" >/dev/null || fail 'usage'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/auth/sessions/" >/dev/null || fail 'sessions'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/auth/export/" >/dev/null || fail 'account export'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/payments/" >/dev/null || fail 'payments list'

printf '[10/12] Soft-delete and restore conversation\n'
curl -fsS -X DELETE -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" "${API_URL%/}/conversations/${conversation_id}/" >/dev/null || fail 'soft delete conversation'
if curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/conversation-workspace/${conversation_id}/" >/dev/null 2>&1; then fail 'deleted conversation still visible'; fi
curl -fsS -X PATCH -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H 'Content-Type: application/json' -d '{"deleted":false}' "${API_URL%/}/conversation-ui/${conversation_id}/" >/dev/null || fail 'restore conversation'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/conversation-workspace/${conversation_id}/" >/dev/null || fail 'restored conversation unavailable'

printf '[11/12] Frontend authenticated routes\n'
for path in /app /app/account /app/wallet /app/usage /app/settings /app/projects; do curl -fsS --max-time 15 "${BASE_URL%/}${path}" >/dev/null || fail "frontend route ${path}"; done

printf '[12/12] Cleanup test account\n'
curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H 'Content-Type: application/json' -d "{\"password\":\"$password\",\"confirmation\":\"DELETE\"}" "${API_URL%/}/auth/delete-account/" >/dev/null || fail 'account cleanup'

printf 'COMMERCIAL CLIENT CABINET E2E: PASS\n'
