#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

PLIST=launchd/com.local.claude.cost-alert.plist
TARGET=$HOME/Library/LaunchAgents/com.local.claude.cost-alert.plist

plutil -lint "$PLIST" >/dev/null
mkdir -p ~/.claude-bridge
cp "$PLIST" "$TARGET"

launchctl bootout "gui/$UID" "$TARGET" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$TARGET"
launchctl kickstart "gui/$UID/com.local.claude.cost-alert"

echo "cost-alert installed. Tail logs:"
echo "  tail -f ~/.claude-bridge/cost-alert.out ~/.claude-bridge/cost-alert.err"
