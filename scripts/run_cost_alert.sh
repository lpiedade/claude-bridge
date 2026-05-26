#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  perms=$(stat -f '%Su:%A' .env)
  expected="$(whoami):600"
  if [[ "$perms" != "$expected" ]]; then
    echo "refusing to source .env: expected $expected, got $perms" >&2
    exit 1
  fi
  set -a
  source .env
  set +a
fi

if [[ -x ./.venv/bin/python ]]; then
  exec ./.venv/bin/python scripts/cost_alert.py
else
  exec /usr/bin/env python3 scripts/cost_alert.py
fi
