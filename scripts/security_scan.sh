#!/usr/bin/env bash
set -euo pipefail

pattern='-----BEGIN (RSA|OPENSSH|EC|DSA|PRIVATE) PRIVATE KEY-----|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}'

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  matches="$(git grep -n -E -- "$pattern" -- \
    ':!docs/**' ':!scripts/security_scan.sh' ':!backend/**/tests.py' ':!backend/**/test_*.py' || true)"
else
  command -v rg >/dev/null 2>&1 || {
    echo "Secret scan requires git or ripgrep." >&2
    exit 2
  }
  matches="$(rg -n --hidden -g '!docs/**' -g '!upload/**' \
    -g '!frontend/node_modules/**' -g '!frontend/.next/**' \
    -g '!backend/**/tests.py' -g '!backend/**/test_*.py' \
    -g '!scripts/security_scan.sh' -e "$pattern" . || true)"
fi

if [[ -n "$matches" ]]; then
  echo "Potential committed secret detected:" >&2
  echo "$matches" >&2
  exit 1
fi

echo "No high-confidence secret patterns detected."
