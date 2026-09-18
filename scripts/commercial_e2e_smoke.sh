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
path=sys.argv[1].split('.')
for key in path:
    obj=obj[int(key)] if isinstance(obj,list) else obj[key]
print(obj)
PY
}

printf '[1/8] Public pages\n'
for path in / /pricing /faq /login /register /forgot-password /api /use-cases/marketing /use-cases/coding /use-cases/documents; do
  curl -fsS --max-time 15 "${BASE_URL%/}${path}" >/dev/null || fail "public route ${path}"
done

printf '[2/8] CSRF\n'
csrf_payload="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" "${API_URL%/}/auth/csrf/")"
csrf="$(printf '%s' "$csrf_payload" | json csrf_token)"
[[ -n "$csrf" ]] || fail 'csrf token missing'

printf '[3/8] Registration\n'
register_payload="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H 'Content-Type: application/json' -d "{\"username\":\"$username\",\"email\":\"$email\",\"password\":\"$password\",\"accepted_terms\":true}" "${API_URL%/}/auth/register/")"
[[ "$(printf '%s' "$register_payload" | json email)" == "$email" ]] || fail 'registration payload mismatch'

printf '[4/8] Authenticated workspace APIs\n'
csrf_payload="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" "${API_URL%/}/auth/csrf/")"
csrf="$(printf '%s' "$csrf_payload" | json csrf_token)"
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/auth/me/" >/dev/null || fail 'auth/me'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/models/" >/dev/null || fail 'models'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/wallet/" >/dev/null || fail 'wallet'

printf '[5/8] Create conversation\n'
models="$(curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/models/")"
model="$(printf '%s' "$models" | python3 -c 'import json,sys; rows=json.load(sys.stdin); print(next((x["slug"] for x in rows if x.get("available")), ""))')"
[[ -n "$model" ]] || fail 'no available AI model configured'
conversation="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H 'Content-Type: application/json' -d "{\"title\":\"Commercial E2E\",\"routing_mode\":\"balanced\",\"selected_model\":\"$model\"}" "${API_URL%/}/conversations/")"
conversation_id="$(printf '%s' "$conversation" | json id)"
[[ -n "$conversation_id" ]] || fail 'conversation id missing'

printf '[6/8] AI request\n'
request_key="e2e:$rand"
response="$(curl -fsS -c "$COOKIE_JAR" -b "$COOKIE_JAR" -H "X-CSRFToken: $csrf" -H "Idempotency-Key: $request_key" -H 'Content-Type: application/json' -d "{\"content\":\"Ответь одним словом: тест\",\"client_message_id\":\"$(python3 -c 'import uuid; print(uuid.uuid4())')\"}" "${API_URL%/}/conversations/${conversation_id}/messages/")"
state="$(printf '%s' "$response" | json state)"
[[ "$state" == "completed" || "$state" == "running" ]] || fail "unexpected generation state: $state"

printf '[7/8] Account surfaces\n'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/auth/sessions/" >/dev/null || fail 'sessions'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/auth/account-export/" >/dev/null || fail 'account export'
curl -fsS -b "$COOKIE_JAR" "${API_URL%/}/payments/" >/dev/null || fail 'payments list'

printf '[8/8] Frontend authenticated routes\n'
for path in /app /app/account /app/wallet; do
  curl -fsS --max-time 15 "${BASE_URL%/}${path}" >/dev/null || fail "frontend route ${path}"
done

printf 'COMMERCIAL E2E: PASS\n'
