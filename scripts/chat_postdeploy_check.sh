#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

cd "$PROJECT_DIR"

[[ -f "$ENV_FILE" ]] || { printf '[FAIL] .env.production не найден\n' >&2; exit 1; }

compose() {
  docker compose --ansi never --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

printf '\n============================================================\n'
printf 'PRODUCTION CHAT ACCEPTANCE\n'
printf '============================================================\n'

printf '[1/6] Backend readiness...\n'
compose exec -T backend python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/',timeout=8); assert r.status==200"
printf '[PASS] Backend readiness\n'

printf '[2/6] Client AUTO preflight: router + pricing + procurement + wallet reserve...\n'
if ! compose exec -T backend python manage.py chat_preflight_smoke --mode auto; then
  printf '[FAIL] Client AUTO preflight; dumping exact pipeline state...\n' >&2
  compose exec -T backend python manage.py chat_pipeline_trace --live || true
  exit 1
fi
printf '[PASS] Client AUTO preflight\n'

printf '[3/6] Explicit routing tiers configured by admin...\n'
CONFIGURED_TIERS="$(compose exec -T backend python manage.py shell -c "from apps.ai_registry.models import RoutingTierAssignment; print(' '.join(sorted(set(RoutingTierAssignment.objects.filter(enabled=True).values_list('tier',flat=True)))))" | tail -n 1)"
for mode in economy balanced maximum; do
  if [[ " ${CONFIGURED_TIERS} " == *" ${mode} "* ]]; then
    compose exec -T backend python manage.py chat_preflight_smoke --mode "$mode"
    printf '[PASS] configured tier=%s\n' "$mode"
  else
    printf '[INFO] tier=%s intentionally has no enabled admin assignment; skipped.\n' "$mode"
  fi
done

printf '[4/7] Real provider recovery + full customer chat generation...\n'
compose exec -T backend python manage.py check_provider_health --live
compose exec -T backend python manage.py chat_live_smoke --mode auto
printf '[PASS] AUTO chat completed through provider, billing and stream\n'

printf '[5/7] Every customer-visible model must pass a minimal real inference...\n'
compose exec -T backend python manage.py chat_runtime_check --live
printf '[PASS] Customer-visible models respond to live inference\n'

printf '[6/7] Free web search...\n'
compose exec -T backend python -c "import json,urllib.parse,urllib.request; u='http://searxng:8080/search?'+urllib.parse.urlencode({'q':'OpenAI current news','format':'json','safesearch':1}); d=json.load(urllib.request.urlopen(u,timeout=10)); assert isinstance(d.get('results'),list)"
printf '[PASS] Web search\n'

printf '[7/7] Billing and stale-operation integrity...\n'
compose exec -T backend python manage.py billing_integrity_check
compose exec -T backend python manage.py agent_recovery_audit >/dev/null
printf '[PASS] Billing/recovery integrity\n'

printf '\n============================================================\n'
printf 'PRODUCTION CHAT ACCEPTANCE: PASS\n'
printf 'AUTO route, configured tiers, client billing preflight, live routable models and web search are operational.\n'
printf '============================================================\n'
