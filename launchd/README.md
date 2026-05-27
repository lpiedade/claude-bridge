# launchd

Versioned copy of the user-level launchd agent that supervises the bridge on
macOS.

## Install (or reinstall)

```bash
bash scripts/install_service.sh
```

The script renders `__PROJECT_DIR__` / `__HOME__` placeholders with the live
clone's path and the current user's `$HOME`, lints the rendered plist, and
re-bootstraps the agent. Re-running is idempotent.

## Uninstall

```bash
bash scripts/uninstall_service.sh
```

Stops the agent and removes the deployed plist. Logs and state under
`~/.claude-bridge/` are preserved; delete that directory manually if you want a
full wipe.

## Inspect

```bash
launchctl print gui/$UID/com.local.claude-bridge
tail -50 ~/.claude-bridge/launchd.out
tail -50 ~/.claude-bridge/launchd.err
```
