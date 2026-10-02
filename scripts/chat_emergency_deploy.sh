#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/ai-workspace}"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

[[ "${EUID}" -eq 0 ]] || { echo "Запустите от root/sudo" >&2; exit 1; }
[[ -f "${ENV_FILE}" ]] || { echo ".env.production не найден" >&2; exit 1; }

cd "${PROJECT_DIR}"

compose() {
  docker compose --ansi never --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

diagnose() {
  status=$?
  trap - ERR
  echo
  echo "================ CHAT DEPLOY FAILED ================" >&2
  echo "exit=${status}" >&2
  compose ps >&2 || true
  echo "---------------- CHAT_PIPELINE ----------------" >&2
  docker logs --since 10m ai-workspace-backend-1 2>&1     | grep -E 'CHAT_PIPELINE|GigaChat|Traceback|ERROR|Exception'     | tail -300 >&2 || true
  echo "------------------------------------------------" >&2
  exit "${status}"
}
trap diagnose ERR

echo "============================================================"
echo "AI CHAT EMERGENCY DEPLOY"
echo "============================================================"

echo "[1/8] Safe disk cleanup"
chmod +x scripts/disk_guard.sh 2>/dev/null || true
PROJECT_DIR="${PROJECT_DIR}" MIN_FREE_GB=4 TARGET_FREE_GB=6   bash scripts/disk_guard.sh
free_kb="$(df -Pk / | awk 'NR==2 {print $4}')"
if (( free_kb < 3145728 )); then
  echo "[FAIL] После безопасной очистки свободно меньше 3 GiB; сборка остановлена." >&2
  df -h / >&2
  exit 1
fi

echo "[2/8] Build slim backend"
COMPOSE_PARALLEL_LIMIT=1 compose build backend
docker builder prune -af >/dev/null 2>&1 || true
docker buildx prune -af >/dev/null 2>&1 || true

echo "[3/8] Build frontend"
COMPOSE_PARALLEL_LIMIT=1 compose build frontend
docker builder prune -af >/dev/null 2>&1 || true
docker buildx prune -af >/dev/null 2>&1 || true

echo "[4/8] Migrations + recreate customer runtime"
compose run --rm backend python manage.py migrate --noinput
compose up -d --force-recreate backend worker beat frontend

echo "[5/8] Wait for backend/frontend health"
backend_ok=false
for _attempt in $(seq 1 30); do
  if compose exec -T backend python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/api/v1/readiness/',timeout=5); assert r.status==200" >/dev/null 2>&1; then
    backend_ok=true
    break
  fi
  sleep 3
done
[[ "${backend_ok}" == true ]] || { echo "[FAIL] backend readiness timeout" >&2; exit 1; }

frontend_ok=false
for _attempt in $(seq 1 20); do
  if compose exec -T frontend node -e "fetch('http://127.0.0.1:3000/').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))" >/dev/null 2>&1; then
    frontend_ok=true
    break
  fi
  sleep 2
done
[[ "${frontend_ok}" == true ]] || { echo "[FAIL] frontend readiness timeout" >&2; exit 1; }

echo "[6/8] Real provider inference + client readiness"
compose exec -T backend python manage.py check
# Repair the historical GigaChat USD/RUB metadata mismatch when applicable.
compose exec -T backend python manage.py align_provider_funding_currency --provider gigachat --apply || true
compose exec -T backend python manage.py check_provider_health --live
compose exec -T backend python manage.py customer_ai_readiness_check

echo "[7/8] Mandatory customer chat acceptance"
compose exec -T backend python manage.py chat_asgi_transport_smoke
compose exec -T backend python manage.py chat_preflight_smoke --mode auto
compose exec -T backend python manage.py chat_live_smoke --mode auto
compose exec -T backend python manage.py chat_live_smoke --mode manual

echo "[8/8] Full chat postdeploy gate"
bash scripts/chat_postdeploy_check.sh

# Successful builds do not need BuildKit layers on this small host.
docker builder prune -af >/dev/null 2>&1 || true
docker buildx prune -af >/dev/null 2>&1 || true
docker image prune -af >/dev/null 2>&1 || true

echo
echo "============================================================"
echo "AI CHAT DEPLOY: PASS"
echo "AUTO, MANUAL, complexity tiers, provider inference, billing and SSE passed."
echo "============================================================"
