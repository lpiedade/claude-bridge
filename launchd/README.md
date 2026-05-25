# launchd

Versioned copy of the user-level launchd agent that supervises the bridge on
macOS.

## Install (or reinstall)

```bash
cp launchd/com.local.claude-bridge.plist ~/Library/LaunchAgents/
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.local.claude-bridge.plist 2>/dev/null || true
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.local.claude-bridge.plist
launchctl kickstart gui/$UID/com.local.claude-bridge
```

## Inspect

```bash
launchctl print gui/$UID/com.local.claude-bridge
tail -50 ~/.claude-bridge/launchd.out
tail -50 ~/.claude-bridge/launchd.err
```

The plist references the repo's `run.sh` at its absolute path. If you clone
the repo elsewhere, edit `ProgramArguments` and `WorkingDirectory` accordingly
before bootstrapping.
