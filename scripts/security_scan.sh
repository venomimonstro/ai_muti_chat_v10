#!/usr/bin/env bash
set -euo pipefail

pattern='-----BEGIN (RSA|OPENSSH|EC|DSA|PRIVATE) PRIVATE KEY-----|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}'

matches="$(git grep -n -E -- "$pattern" -- \
  ':!docs/**' ':!scripts/security_scan.sh' ':!backend/**/tests.py' || true)"

if [[ -n "$matches" ]]; then
  echo "Potential committed secret detected:" >&2
  echo "$matches" >&2
  exit 1
fi

echo "No high-confidence secret patterns detected."
