#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f .env ]]; then
  # .env is sourced as shell; refuse to run with loose permissions.
  perms=$(stat -f '%Su:%A' .env)
  expected="$(whoami):600"
  if [[ "$perms" != "$expected" ]]; then
    echo "refusing to source .env: expected $expected, got $perms" >&2
    echo "fix with: chmod 600 .env && chown $(whoami) .env" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec ./.venv/bin/python bot.py
