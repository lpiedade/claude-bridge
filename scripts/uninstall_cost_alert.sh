#!/bin/bash
set -euo pipefail
TARGET=$HOME/Library/LaunchAgents/com.local.claude.cost-alert.plist

if [ -f "$TARGET" ]; then
  launchctl bootout "gui/$UID" "$TARGET" 2>/dev/null || true
  rm -f "$TARGET"
  echo "cost-alert removed."
else
  echo "No plist at $TARGET"
fi
