#!/bin/bash
# Render the bridge plist with $HOME / project dir baked in, install it under
# ~/Library/LaunchAgents, then bootstrap + kickstart so the bot starts immediately
# and on every login. Idempotent: re-running cleanly replaces an existing install.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

TEMPLATE=launchd/com.local.claude-bridge.plist
LABEL=com.local.claude-bridge
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ ! -f .env ]]; then
    echo "Missing .env — copy .env.example to .env and fill it before installing." >&2
    exit 1
fi
ENV_MODE=$(stat -f '%Su:%A' .env)
if [[ "$ENV_MODE" != "$(whoami):600" ]]; then
    echo ".env must be owned by $(whoami) with mode 600 (was $ENV_MODE)." >&2
    echo "Run: chmod 600 .env && chown \"$(whoami)\" .env" >&2
    exit 1
fi

mkdir -p ~/.claude-bridge ~/Library/LaunchAgents

# Substitute placeholders into a temp file, validate, then move into place.
TMP=$(mktemp -t claude-bridge.plist)
trap 'rm -f "$TMP"' EXIT
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__HOME__|$HOME|g" \
    "$TEMPLATE" > "$TMP"
plutil -lint "$TMP" >/dev/null
mv "$TMP" "$TARGET"
trap - EXIT

launchctl bootout "gui/$UID" "$TARGET" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$TARGET"
launchctl kickstart "gui/$UID/$LABEL"

echo "$LABEL installed and started."
echo "Tail logs:"
echo "  tail -f ~/.claude-bridge/bridge.log"
echo "  tail -f ~/.claude-bridge/launchd.err"
echo "Inspect: launchctl print gui/\$UID/$LABEL"
