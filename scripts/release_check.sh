#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

printf '[1/7] Secret scan\n'
./scripts/security_scan.sh

printf '[2/7] Backend test image\n'
docker build -f backend/Dockerfile.test -t ai-workspace-backend-test backend

printf '[3/7] Backend lint\n'
docker run --rm ai-workspace-backend-test ruff check .

printf '[4/7] Backend tests\n'
docker run --rm \
  -e DJANGO_SECRET_KEY=release-check-secret-key-release-check \
  -e DATABASE_URL=sqlite:////tmp/release-check.sqlite3 \
  -e REDIS_URL=redis://127.0.0.1:6379/0 \
  -e CACHE_URL=locmem:// \
  ai-workspace-backend-test pytest -q

printf '[5/7] Django structural checks\n'
docker run --rm \
  -e DJANGO_SECRET_KEY=release-check-secret-key-release-check \
  -e DATABASE_URL=sqlite:////tmp/release-check.sqlite3 \
  -e REDIS_URL=redis://127.0.0.1:6379/0 \
  -e CACHE_URL=locmem:// \
  ai-workspace-backend-test python manage.py check

printf '[6/7] Frontend lint/build\n'
docker build -t ai-workspace-frontend-test frontend

docker run --rm ai-workspace-frontend-test sh -c 'npm run lint'

printf '[7/7] Migration drift\n'
docker run --rm \
  -e DJANGO_SECRET_KEY=release-check-secret-key-release-check \
  -e DATABASE_URL=sqlite:////tmp/release-check.sqlite3 \
  -e REDIS_URL=redis://127.0.0.1:6379/0 \
  -e CACHE_URL=locmem:// \
  ai-workspace-backend-test python manage.py makemigrations --check --dry-run

printf 'RELEASE CHECK: PASS\n'
