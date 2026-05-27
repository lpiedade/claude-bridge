# Changelog

All notable changes to this project will be documented in this file. The
format is loosely based on [Keep a Changelog](https://keepachangelog.com/) and
this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-05-27

First tagged release. The bridge is feature-complete against the four-stage
project charter: capture & forward Telegram prompts to the local Claude CLI,
expose observability into model usage and context window, and surface
permission decisions to the user with interactive Approve/Reject.

### Added

- `/usage` command — replies with a cumulative-cost PNG line chart for the
  active session plus a textual caption (model, turns, input/output/cache
  tokens, total cost). Cost is computed locally from token counts × Anthropic
  public list prices (`integrations/claude_pricing.py`); no Claude CLI
  invocation, no token spend.
- `/context` command — renders a PNG mirroring Claude Code's `/context` view
  (10×20 grid + per-category breakdown). Invokes the CLI's synthetic
  `/context` slash command (`num_turns=0`, no cost).
- Interactive Approve/Reject for permission denials — denied Bash/Edit/Write
  calls now arrive with inline buttons. Approve re-invokes the CLI on the same
  session with `--allowedTools` narrowed to the exact denied invocation;
  Reject discards the prompt. Pending approvals TTL at 30 minutes and a
  second-round denial aborts rather than looping.
- Slow-response feedback — TYPING indicator refreshed every 4 s and, past
  `CLAUDE_BRIDGE_SLOW_RESPONSE_SECONDS` (default 30), a "still thinking…"
  notice that edits in place with elapsed seconds and is deleted on
  completion.
- Configurable app log level via `CLAUDE_BRIDGE_LOG_LEVEL` (DEBUG/INFO/
  WARNING/ERROR/CRITICAL). `launchd.err` stays pinned at WARNING regardless.
- Cost-alert launchd agent — hourly check of every tracked session's
  transcript; emails when any one crosses `COST_ALERT_THRESHOLD_USD` (default
  $10) via Mail.app. Hourly dedupe per session.
- `scripts/install_service.sh` and `scripts/uninstall_service.sh` — render
  the launchd plist's `__PROJECT_DIR__` / `__HOME__` placeholders, validate,
  bootstrap, and kickstart. Idempotent.
- `docs/security-review.md` Review #4 — covers Stages 2–3 surfaces (approval
  store, pricing module, cost-alert) and downgrades F-01's operational risk
  thanks to the new Approve/Reject path.

### Changed

- `scripts/cost_alert.cost_from_transcript` falls back to computed cost
  (tokens × pricing) when transcripts lack `costUSD` / `total_cost_usd` —
  newer CLI versions stopped emitting those fields, which previously zeroed
  out the hourly alert.
- The denial notice in `/message` now carries inline Approve/Reject buttons
  instead of a one-way warning. The "blocked (modo: acceptEdits)" framing
  was removed; the mode is no longer hard-coded into the user-facing copy.
- `launchd/com.local.claude-bridge.plist` is now a template with
  `__PROJECT_DIR__` / `__HOME__` placeholders. Existing installs need to
  re-run `bash scripts/install_service.sh` once to migrate from hard-coded
  paths.
- README: `/usage` and `/context` command rows added to the bot-commands
  table; a `/usage` screenshot is embedded under the table; the launchd
  install steps now call `install_service.sh` instead of `bootstrap`
  directly.

### Fixed

- F-12 (security review carry-over) — prompt bodies in
  `conversation.log` are now redacted via `utils.redact` and truncated at
  4000 chars before write.
- Permission-denial allowlist root cause — `~/.claude/settings.json`
  (user-level) is ignored at the project level by current Claude CLI
  versions (anthropics/claude-code#18846); the project's
  `.claude/settings.local.json` is the only effective allowlist. Routine
  git commands (`push`, `status`, `diff`, `log`, `fetch`, etc.) added there;
  destructive variants (`--force`, `reset --hard`, `branch -D`) left out
  intentionally.

### Security

- See `docs/security-review.md` Review #4 for the full risk register. The
  dominant residual remains F-01: `CLAUDE_BRIDGE_PERMISSION_MODE` defaults
  to `acceptEdits`, and operators relying on `bypassPermissions` should now
  consider that mode a power-user opt-in given the interactive flow makes
  `default` survivable.
