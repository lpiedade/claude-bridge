#!/bin/bash
# Stop the bridge launchd agent and remove its plist. Logs and state remain in
# ~/.claude-bridge for inspection — delete that directory manually if a full wipe
# is wanted.
set -euo pipefail

LABEL=com.local.claude-bridge
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ -f "$TARGET" ]]; then
    launchctl bootout "gui/$UID" "$TARGET" 2>/dev/null || true
    rm -f "$TARGET"
    echo "$LABEL uninstalled."
else
    echo "$LABEL was not installed (no plist at $TARGET)."
fi
echo "Logs/state preserved at ~/.claude-bridge — remove manually if no longer needed."
