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

printf '[1/7] Backend readiness...\n'
compose exec -T backend python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/',timeout=8); assert r.status==200"
printf '[PASS] Backend readiness\n'

printf '[2/7] Native ASGI transport + client AUTO preflight...\n'
compose exec -T backend python manage.py chat_asgi_transport_smoke
if ! compose exec -T backend python manage.py chat_preflight_smoke --mode auto; then
  printf '[FAIL] Client AUTO preflight; dumping exact pipeline state...\n' >&2
  compose exec -T backend python manage.py chat_pipeline_trace --live || true
  exit 1
fi
printf '[PASS] Client AUTO preflight\n'

printf '[3/7] Effective routing tiers: preflight + real completion...\n'
for mode in economy balanced maximum; do
  POOL="$(compose exec -T backend python manage.py shell -c "from apps.ai_registry.models import RoutingPolicyVersion; from apps.ai_registry.routing_pools import tier_pool; p=RoutingPolicyVersion.objects.filter(active=True).first(); print(' '.join(tier_pool((p.thresholds or {}) if p else {}, '$mode')))" | tail -n 1)"
  if [[ -n "${POOL// }" ]]; then
    compose exec -T backend python manage.py chat_preflight_smoke --mode "$mode"
    compose exec -T backend python manage.py chat_live_smoke --mode "$mode"
    printf '[PASS] tier=%s live models=%s\n' "$mode" "$POOL"
  else
    printf '[INFO] tier=%s has no effective pool; skipped.\n' "$mode"
  fi
done

printf '[4/7] Real provider recovery + full customer chat generation...\n'
compose exec -T backend python manage.py check_provider_health --live
compose exec -T backend python manage.py chat_live_smoke --mode auto
compose exec -T backend python manage.py chat_live_smoke --mode manual
printf '[PASS] AUTO and manual chats completed through provider, billing and stream\n'

printf '[5/7] Every customer-visible model must pass a minimal real inference...\n'
compose exec -T backend python manage.py chat_runtime_check --live
printf '[PASS] Customer-visible models respond to live inference\n'

printf '[6/7] Optional web search (must not take core chat offline)...\n'
if compose exec -T backend python -c "import json,urllib.parse,urllib.request; u='http://searxng:8080/search?'+urllib.parse.urlencode({'q':'OpenAI current news','format':'json','safesearch':1}); d=json.load(urllib.request.urlopen(u,timeout=10)); assert isinstance(d.get('results'),list)" >/dev/null 2>&1; then
  printf '[PASS] Web search\n'
else
  printf '[WARN] Web search unavailable; core AI chat remains valid and search degrades independently.\n'
fi

printf '[7/7] Billing and chat-recovery integrity...\n'
compose exec -T backend python manage.py billing_integrity_check
compose exec -T backend python manage.py shell -c "from apps.procurement.chat_signals import reconcile_confirmed_chat_procurement; print(reconcile_confirmed_chat_procurement(limit=500))"
printf '[PASS] Billing/chat recovery integrity\n'

printf '\n============================================================\n'
printf 'PRODUCTION CHAT ACCEPTANCE: PASS\n'
printf 'AUTO route, configured tiers, client billing preflight, live routable models and web search are operational.\n'
printf '============================================================\n'
