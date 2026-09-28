#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${ENV_FILE:-.env.production}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

if [[ ! -f "$ENV_FILE" ]]; then
  echo "PRODUCT EXPANSION CHECK: FAIL · missing $ENV_FILE" >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "PRODUCT EXPANSION CHECK: FAIL · docker compose is unavailable" >&2
  exit 1
fi

echo "[76-79 1/6] Build fresh backend + frontend images"
"${COMPOSE[@]}" build backend frontend

echo "[76-79 2/6] Django configuration + migration drift"
"${COMPOSE[@]}" run --rm --no-deps backend python manage.py check
"${COMPOSE[@]}" run --rm --no-deps backend python manage.py makemigrations --check --dry-run

echo "[76-79 3/6] Search failover regression"
"${COMPOSE[@]}" run --rm --no-deps backend \
  pytest -q apps/ai_registry/test_web_search_failover.py

echo "[76-79 4/6] OpenAI image edit security/billing/credential regression"
"${COMPOSE[@]}" run --rm --no-deps backend \
  pytest -q apps/image_studio/test_image_edit.py

echo "[76-79 5/6] OpenAI Images production configuration"
"${COMPOSE[@]}" run --rm --no-deps backend \
  python manage.py image_runtime_diagnose

echo "[76-79 6/6] Live search diagnostic"
if "${COMPOSE[@]}" ps --services --status running | grep -qx searxng; then
  "${COMPOSE[@]}" run --rm --no-deps backend \
    python manage.py web_search_diagnose --query "OpenAI официальный сайт"
else
  echo "PRODUCT EXPANSION CHECK: FAIL · searxng is not running" >&2
  exit 1
fi

echo "PRODUCT EXPANSION CHECK: PASS"
