#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${PROJECT_DIR}/.env.production"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.prod.yml"

[[ "${EUID}" -eq 0 ]] || { echo "Запустите через sudo" >&2; exit 1; }
[[ -f "${ENV_FILE}" ]] || { echo ".env.production не найден" >&2; exit 1; }

cd "${PROJECT_DIR}"

if [[ -d .git ]]; then
  git -c safe.directory="${PROJECT_DIR}" diff --quiet || {
    echo "Есть незакоммиченные изменения; hotfix остановлен" >&2
    exit 1
  }
  git -c safe.directory="${PROJECT_DIR}" fetch origin main
  git -c safe.directory="${PROJECT_DIR}" merge --ff-only origin/main
fi

upsert_nonempty() {
  local key="$1" value="$2" current
  current="$(sed -n "s/^${key}=//p" "${ENV_FILE}" | head -n1)"
  if [[ -z "${current}" ]]; then
    if grep -q "^${key}=" "${ENV_FILE}"; then
      sed -i "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
    else
      printf '%s=%s\n' "${key}" "${value}" >>"${ENV_FILE}"
    fi
  fi
}

SEARXNG_SECRET="$(sed -n 's/^SEARXNG_SECRET=//p' "${ENV_FILE}" | head -n1)"
if [[ -z "${SEARXNG_SECRET}" ]]; then
  SEARXNG_SECRET="$(openssl rand -hex 32)"
  if grep -q '^SEARXNG_SECRET=' "${ENV_FILE}"; then
    sed -i "s|^SEARXNG_SECRET=.*|SEARXNG_SECRET=${SEARXNG_SECRET}|" "${ENV_FILE}"
  else
    printf 'SEARXNG_SECRET=%s\n' "${SEARXNG_SECRET}" >>"${ENV_FILE}"
  fi
fi

upsert_nonempty WEB_SEARCH_BASE_URL http://searxng:8080
upsert_nonempty WEB_SEARCH_TRUSTED_HOSTS searxng
upsert_nonempty WEB_SEARCH_MAX_RESULTS 8
upsert_nonempty WEB_CONTEXT_MAX_TOKENS 2200
upsert_nonempty CHAT_MAX_OUTPUT_TOKENS 4096
chmod 600 "${ENV_FILE}"

compose() {
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

# Do not pipe compose directly into grep under `set -o pipefail`: grep -q may close
# the pipe after the match and make docker compose exit with SIGPIPE, producing a
# false "service missing" failure.
COMPOSE_SERVICES="$(compose config --services)"
if ! grep -Fxq 'searxng' <<<"${COMPOSE_SERVICES}"; then
  echo "Текущий docker-compose.prod.yml не содержит searxng" >&2
  printf 'Compose services:\n%s\n' "${COMPOSE_SERVICES}" >&2
  exit 1
fi

compose pull searxng
compose build backend worker
compose up -d searxng backend worker

SEARCH_OK=false
for _attempt in $(seq 1 18); do
  if compose exec -T backend python -c "from apps.ai_registry.web_tools import search_web; r=search_web('искусственный интеллект 2026', limit=2); assert len(r) > 0; print(r)"; then
    SEARCH_OK=true
    break
  fi
  sleep 5
done

[[ "${SEARCH_OK}" == true ]] || {
  echo "Web-search не прошёл реальную проверку" >&2
  compose logs --tail=120 searxng >&2 || true
  exit 1
}

echo "WEB SEARCH HOTFIX: PASS"
echo "Commit: $(git -c safe.directory="${PROJECT_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
compose ps searxng backend worker
