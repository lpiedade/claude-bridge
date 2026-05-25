# claude-bridge

Telegram ↔ Claude Code CLI bridge running on the Mac. Lets you talk to the Claude Code subscription from your phone without installing Claude on mobile.

## Architecture

```
[Telegram app on phone]
        ↓
[Telegram Bot API]
        ↓ (long-poll)
[python -m app.main on the Mac via launchd]
        ↓ (subprocess)
[claude -p <prompt> --resume <session-id> --permission-mode bypassPermissions]
```

Per-chat state persisted in `~/.claude-bridge/state.json` (session_id + cwd + `started` flag).

### Module layout

```
app/main.py                       # entrypoint (python -m app.main)
core/
├── config.py                     # env-var loading + defaults
└── logger.py                     # logging setup, shared `log`
utils/
├── paths.py                      # resolve_arg, is_cwd_allowed, safe_resolve
└── redact.py                     # scrub home path / emails / hex / api keys
integrations/
└── claude_client.py              # subprocess wrapper + extract_result_text
repositories/
└── session_repository.py         # state.json load/save + per-chat session
service/handlers/
├── __init__.py                   # register(app) wires all CommandHandlers
├── _common.py                    # authorized() + 1-arg is_cwd_allowed wrapper
├── start.py                      # /start, /status
├── session.py                    # /new
├── cwd.py                        # /cd, /pwd, /ls
├── effort.py                     # /effort
├── model.py                      # /model
└── message.py                    # free-form text → claude CLI
run.sh                            # launchd entrypoint (sources .env, execs python -m app.main)
launchd/                          # versioned plist + install README
tests/                            # pytest, 56 cases
pyproject.toml                    # package declaration; `claude-bridge` console script
```

## Initial setup

### 1. Create the Telegram bot
- In Telegram, talk to `@BotFather` → `/newbot` → follow the prompts → save the **token**.
- Talk to `@userinfobot` → save your numeric **chat_id**.

### 2. Install local dependencies
```bash
cd ~/EDF/Personal/Github/claude-bridge
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env: fill in CLAUDE_BRIDGE_TG_TOKEN and CLAUDE_BRIDGE_ALLOWED_CHATS
chmod 600 .env
```

### 3. Foreground smoke test
```bash
./run.sh
# in another window: send /start to the bot in Telegram
# ctrl-c once it works
```

### 4. Enable as a service (launchd)
```bash
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.local.claude-bridge.plist
launchctl print gui/$UID/com.local.claude-bridge | head -30
tail -f ~/.claude-bridge/launchd.err   # ctrl-c to exit
```

## Bot commands (in Telegram)

| Command | Function |
|---|---|
| `/start` | Show current session_id, cwd, and permission mode |
| `/status` | Alias for `/start` |
| `/new` | Generate a new session_id (clears conversation memory) |
| `/cd` | Show the current working directory |
| `/cd ~/EDF/BlindBet` | Change the working directory |
| `/pwd` | Print the current working directory |
| `/ls` | List entries in the current cwd |
| `/ls ~/EDF/BlindBet` | List entries in a path (must be inside an allowed root) |
| `/effort` | Show the effort level for this chat (or `(default)` if unset) |
| `/effort high` | Set effort for this chat. Valid: `low`, `medium`, `high`, `xhigh`, `max`, `none` (clears override) |
| `/model` | Show the model for this chat and the default |
| `/model opus` | Set model for this chat. Valid: `opus`, `sonnet`, `haiku`, `default` (resets to `CLAUDE_BRIDGE_MODEL`/`haiku`) |
| `<any text>` | Send as a prompt to Claude Code |

## Management (launchd)

```bash
# Inspect state (loaded? last exit? PID?)
launchctl print gui/$UID/com.local.claude-bridge

# Restart after editing code or .env
launchctl kickstart -k gui/$UID/com.local.claude-bridge

# Stop temporarily (keeps plist on disk)
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.local.claude-bridge.plist

# Reload after editing the plist
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.local.claude-bridge.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.local.claude-bridge.plist

# Remove permanently
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.local.claude-bridge.plist
rm ~/Library/LaunchAgents/com.local.claude-bridge.plist

# Validate plist syntax before reloading
plutil -lint ~/Library/LaunchAgents/com.local.claude-bridge.plist
```

## Logs

```bash
tail -50 ~/.claude-bridge/launchd.out    # stdout (bot INFO logs)
tail -50 ~/.claude-bridge/launchd.err    # stderr (errors, tracebacks)
cat ~/.claude-bridge/state.json          # per-chat session state
```

## Configuration (.env)

| Variable | Default | Notes |
|---|---|---|
| `CLAUDE_BRIDGE_TG_TOKEN` | (required) | Token from BotFather |
| `CLAUDE_BRIDGE_ALLOWED_CHATS` | (required) | Comma-separated numeric `chat_id`s |
| `CLAUDE_BRIDGE_CWD` | `~/EDF/Personal/Github` | Default working directory for new sessions |
| `CLAUDE_BRIDGE_CWD_ROOTS` | `~/EDF/Personal/Github,~/EDF/BlindBet,/tmp` | Allowlist of roots `/cd` may switch into (comma-separated). `DEFAULT_CWD` must be under one of these or the bot refuses to start. Symlinks are resolved before the check. |
| `CLAUDE_BRIDGE_PERMISSION_MODE` | `bypassPermissions` | See "Security" below |
| `CLAUDE_BRIDGE_TIMEOUT` | `600` | Per-message timeout in seconds |
| `CLAUDE_BRIDGE_EFFORT` | (unset) | Default effort level passed as `--effort` to the Claude CLI. One of `low`, `medium`, `high`, `xhigh`, `max`. Per-chat override via `/effort`. |
| `CLAUDE_BRIDGE_MODEL` | `haiku` | Default model passed as `--model` to the Claude CLI. One of `opus`, `sonnet`, `haiku`. Per-chat override via `/model`. Haiku is the default to keep costs low. |

After editing `.env`, reload with `launchctl kickstart -k gui/$UID/com.local.claude-bridge`.

## Security

- **Chat allowlist** — only chats in `CLAUDE_BRIDGE_ALLOWED_CHATS` get replies; everyone else sees "Unauthorized".
- **`bypassPermissions`** — required to run tasks without human approval. Equivalent to "yes to everything": Claude can edit/delete files inside `cwd` and run arbitrary shell commands. Mitigations:
  - Keep `cwd` in a safe directory (not `$HOME` root).
  - Switch to `acceptEdits` in `.env` to block shell command execution (file edits only).
- **Token and chat_id in `.env`** — chmod 600. Never commit to git.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Bot does not reply on Telegram | Service is not running | `launchctl print gui/$UID/com.local.claude-bridge` — if "could not find service", reload |
| `last exit code` ≠ 0 | Error in `app/main.py` or missing `.env` | `tail -50 ~/.claude-bridge/launchd.err` |
| "Unauthorized" reply on Telegram | Your chat_id is not in `ALLOWED_CHATS` | Re-fetch via `@userinfobot`, update `.env`, kickstart |
| `claude: command not found` in logs | Plist `PATH` does not include `/opt/homebrew/bin` | Check the `EnvironmentVariables` block in the plist |
| Reply is cut off | Message >4000 chars | Expected — the bot splits into chunks; confirm all arrived |
| Session "forgot" context | Mac slept or bot restarted | State persists in `~/.claude-bridge/state.json`; `--resume` continues working after restart |

## Stack

- `python-telegram-bot>=21.0` (long-polling)
- `claude` CLI (authenticated on the Mac)
- launchd (not cron — recovers from sleep, auto-restart on crash)
