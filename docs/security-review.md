# Security review — claude-bridge

**Reviewed:** 2026-05-24
**Commit Evaluated:** `ffedea5`
**Scope:** `bot.py`, `run.sh`, `.env.example`, `~/Library/LaunchAgents/com.local.claude-bridge.plist` (out-of-repo).

This is a single-user automation that exposes the local `claude` CLI to a Telegram bot. The risk profile is dominated by the fact that `--permission-mode bypassPermissions` gives the bot full shell-equivalent capability on the host. Any path that lets an attacker post a Telegram message to the bot is therefore equivalent to remote code execution on the Mac under the user's UID.

The review below is intentionally rigorous: even findings the author may consider acceptable for personal use are listed, with severity and remediation, so that the trust boundary stays explicit.

## Threat model

| Actor | Capability assumed | Mitigation today |
|---|---|---|
| Internet at large | Can DM the bot if they learn the token | `chat_id` allowlist in `authorized()` (`bot.py:87-88`) |
| Token leak (no chat_id) | Bot becomes spammable but cannot reach `on_message` | Same allowlist |
| Owner's Telegram account compromise (SIM swap, stolen device, hijacked session) | Full message authorship as the owner → **RCE on the Mac** | None — this is the dominant risk; see F-01 |
| Local user on the same Mac with read access to `$HOME` | Can read `.env` token, state file, source code | File permissions are not enforced by code (F-03, F-09) |
| Local user with write access to `.env` or `bot.py` | Can pivot to RCE via `source .env` in `run.sh` or by editing the script | Same trust boundary as full machine compromise; noted but not mitigated |
| Network MITM | TLS to Telegram terminates it | n/a |

## Findings

### F-01 — `bypassPermissions` makes Telegram-account compromise equivalent to host RCE  &nbsp;`Severity: Critical`

**Location:** `bot.py:40`, `bot.py:151`
**Evidence:**
```python
PERMISSION_MODE = os.environ.get("CLAUDE_BRIDGE_PERMISSION_MODE", "bypassPermissions")
...
cmd = [CLAUDE_BIN, "-p", prompt, "--permission-mode", PERMISSION_MODE, ...]
```

**Description:** Every prompt is forwarded to Claude with permissions bypassed. Whoever can author a Telegram message from an allowlisted chat can have Claude execute arbitrary shell commands inside `cwd`, edit any file the user owns, and exfiltrate data (SSH keys, browser cookies, password manager state if unlocked). The Mac account, not the bot's `cwd`, is the actual blast radius — `cwd` only sets the *starting* directory; nothing prevents Claude from doing `cd $HOME && ...`.

**Telegram-side exposure** is the realistic attack vector: SIM-swap, stolen unlocked device, Telegram session hijack via desktop session export, or a phishing capture of the linked-device login code. None of these are exotic; they are routine for high-value targets.

**Impact:** Remote shell on the Mac under the user's UID. Persistent if the attacker plants a launchd agent or modifies the user's shell rc files.

**Remediation (in order of cost):**
1. **Lowest cost:** switch default to `--permission-mode acceptEdits`. Claude can still edit files but cannot run shell commands. Bot loses "execute tasks for real" capability — accept this for routine use; flip back only for explicit sessions via a `/dangerous on` command guarded by a confirmation token.
2. **Medium cost:** add a `confirm` step — for any prompt likely to issue a destructive command, require the user to reply `yes <token>` before forwarding. This requires pre-parsing prompts and reduces the convenience the bot was built for; partial mitigation.
3. **High cost:** run the bot under a dedicated UNIX user with no sudo, no SSH keys, and a sandboxed home; expose only the project directory via mount/ACL. Defeats most blast-radius scenarios at the cost of setup complexity.
4. **Operational:** enable Telegram two-step verification (passcode in addition to SMS) and audit linked devices monthly. This is the cheapest practical control against the dominant attack vector.

### F-02 — `/cd` accepts any directory the user can read  &nbsp;`Severity: High`

**Location:** `bot.py:128-132`
**Evidence:**
```python
new_cwd = os.path.expanduser(ctx.args[0])
if not Path(new_cwd).is_dir():
    await update.message.reply_text(f"Not a directory: {new_cwd}")
    return
```

**Description:** The only validation is `is_dir()`. `/cd /`, `/cd ~/.ssh`, `/cd ~/Library/Group Containers`, `/cd ~/EDF/BlindBet` are all accepted. Combined with F-01, this lets any Telegram-side attacker move into the most sensitive directories on the machine before issuing a destructive prompt. Symlinks are not resolved, so `/cd /tmp/symlink-to-anywhere` is also accepted.

**Impact:** Amplifier for F-01. Removing it does not fix the underlying problem but raises the cost of casual mistakes (a typo turning into a wipe of `~/Library`).

**Remediation:**
- Enforce an allowlist root via env var (e.g. `CLAUDE_BRIDGE_CWD_ROOTS=~/EDF/Personal/Github`) and reject any `new_cwd` whose `Path.resolve()` is not under one of those roots.
- Reject paths containing `..` after expansion. Reject if `Path(new_cwd).resolve()` differs from a normalized form (catches symlink escapes).

### F-03 — State file is written with default umask and may be world-readable  &nbsp;`Severity: High`

**Location:** `bot.py:55-56`, `bot.py:43-44`
**Evidence:**
```python
def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))
```

**Description:** `write_text` uses the process umask. On macOS default (022), the resulting file is `-rw-r--r--`. The state file contains `chat_id`s (sensitive — they are the bot's authentication anchor) and `session_id`s (a writer can inject context into the next Claude session by editing the file). The parent directory is created with `mkdir(parents=True, exist_ok=True)` which also inherits umask.

**Impact:** On multi-user Macs (rare) or any process running under another UID with home read access, the state file is readable. More importantly, the precedent of "secrets file with weak permissions" tends to spread.

**Remediation:**
- After `write_text`, call `os.chmod(STATE_FILE, 0o600)`.
- After `mkdir`, call `os.chmod(STATE_FILE.parent, 0o700)`.
- On startup, refuse to run if the existing state file or parent dir has group/other bits set.

### F-04 — State file is not written atomically  &nbsp;`Severity: High`

**Location:** `bot.py:55-56`
**Description:** `write_text` truncates and writes in a single call but is not atomic. If the process is killed mid-write (e.g. macOS sleep + termination, OOM, manual `kill`, launchd `bootout` during a write), the file may end up empty or partial. Next startup calls `json.loads("")` which raises `JSONDecodeError`, crashes the bot, and **all sessions are lost** (state.json is the source of truth).

**Impact:** Reliability and minor security — corrupted state forces session reset, which may make the user think "the bot has memory" when in fact context was silently lost.

**Remediation:**
```python
def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_FILE)
```

### F-05 — Concurrent message handlers race on `state.json`  &nbsp;`Severity: High`

**Location:** `bot.py:59-78`, `bot.py:138-189`
**Description:** `python-telegram-bot` v21 dispatches handlers concurrently. Two messages arriving in quick succession to the same chat both call `load_state()` → mutate → `save_state()` without locking. The interleaving can:

- Lose the `started=True` flag set by the first handler, so the second handler also passes `--session-id` instead of `--resume`. Claude CLI rejects duplicate-session creation, and the second message fails with an opaque error.
- Lose a `/new` rotation if it interleaves with a regular message: the new `session_id` written by `cmd_new` can be overwritten by the in-flight handler's `update_session(started=True)`.
- Lose `cwd` changes the same way.

**Impact:** Intermittent failures, hard to diagnose. Not a direct security issue, but unreliable session state makes audit logs misleading.

**Remediation:** Wrap state mutation in an `asyncio.Lock` keyed by `chat_id`. Or move to a tiny SQLite store with `BEGIN IMMEDIATE` per write. For a single-user bot, a single global lock is enough and simplest.

### F-06 — Polling resumes in-flight updates after restart, allowing replay  &nbsp;`Severity: Medium`

**Location:** `bot.py:204` (`app.run_polling()`)
**Description:** Long polling acknowledges updates only after the handler returns. If a message triggers a long Claude run and the bot is killed (launchd `bootout`, crash, sleep), Telegram redelivers the update on next start. The same prompt is processed again — Claude may rerun the side-effectful task (file edits, shell commands).

**Impact:** Idempotency violation. With F-01's blast radius, a destructive prompt could run twice (e.g., "delete the build folder" → delete, killed mid-reply, restart, delete again — fine here; but "send email" double-fires).

**Remediation:** Persist `last_processed_update_id` in `state.json` and drop incoming updates with `update_id <= last_processed_update_id` before invoking the Claude subprocess.

### F-07 — No per-chat rate limiting or quota cap  &nbsp;`Severity: Medium`

**Location:** `bot.py:138-189`
**Description:** Every text message triggers a `claude` subprocess (10-min timeout). An attacker (or a chat-bombing buddy) can burst hundreds of messages. With F-01's compromise vector, this also drains subscription quota and racks up costs invisibly.

**Impact:** DoS on the Mac (each `claude` spawns a process and consumes context window), quota exhaustion, possible compliance issue if the subscription has usage-based billing.

**Remediation:** Add a sliding-window rate limiter per `chat_id` (e.g. 30 messages/hour, 5/minute). Reject excess with a single "rate limited" message — do not silently drop, since that masks the abuse signal.

**Addendum — 2026-05-24 re-read with concurrency model in mind:**

The original framing ("hundreds of simultaneous subprocesses") is incorrect for the current configuration. `python-telegram-bot` v21 defaults to `concurrent_updates=False`, so the `Application` processes updates one at a time. On top of that, `on_message` calls `subprocess.run(...)` synchronously inside an async handler, which blocks the event loop until Claude returns — effectively serializing message handling even if `concurrent_updates=True` were set later. So a burst of 100 messages does not spawn 100 parallel processes; it produces a long queue handled sequentially.

This changes the threat model but does not eliminate the finding:

- **Quota burn is still real.** A serialized burst still drains the Accenture subscription one prompt at a time, just slower.
- **Bot becomes unresponsive during the burst.** Without a rate limit, every command (including `/new`) sits behind the queue; the only escape is stopping the bot via `launchctl`. With a rate limit, excess messages are rejected in ~1ms and admin commands stay reachable.
- **F-01's compromise vector (Telegram account takeover) still benefits from a cap** — attacker cannot indefinitely tie up the bot's quota and attention.

Severity remains `Medium`; the original remediation (sliding-window rate limiter) still applies. Decision recorded after discussion on 2026-05-24: not implemented for now — relying on `concurrent_updates=False` serialization plus Telegram 2FA (F-01 operational mitigation). Reopen this finding if `concurrent_updates` is ever flipped to `True`, if the bot moves to async Claude invocations (e.g. via `asyncio.create_subprocess_exec`), or if a second authorized chat is added.

### F-08 — Subprocess error output is reflected to Telegram (and to Telegram cloud backups)  &nbsp;`Severity: Medium`

**Location:** `bot.py:173-176`
**Description:** `result.stderr` and `result.stdout` from `claude` are echoed to the chat. Errors from the CLI can include absolute paths, env-derived strings, and occasionally fragments of the prompt or system state. Telegram messages are stored on Telegram's servers and (if enabled) in cloud chat backups, broadening the exposure of these strings beyond the local machine.

**Impact:** Information disclosure of host details. Not credentials directly, but useful for an attacker doing recon.

**Remediation:** Redact known patterns (home dir, project paths) before sending; keep full stderr in `launchd.err` only.

### F-09 — `run.sh` sources `.env` without checking its mode or owner  &nbsp;`Severity: Medium`

**Location:** `run.sh:6-10`
**Evidence:**
```bash
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
```

**Description:** `source .env` executes the file as shell. If `.env` is world-writable, group-writable, or owned by another user, anyone with that write capability gets shell as the bot's UID at bot start. The README says `chmod 600`, but nothing enforces it.

**Impact:** Local privilege misuse if the box has multiple users or shared write paths. Negligible on a single-user Mac, but the cost to enforce is trivial.

**Remediation (add to `run.sh`):**
```bash
if [[ -f .env ]]; then
  if [[ "$(stat -f '%Su:%A' .env)" != "$(whoami):600" ]]; then
    echo ".env must be owned by $(whoami) with mode 600" >&2
    exit 1
  fi
  set -a; source .env; set +a
fi
```

### F-10 — `CLAUDE_BIN` is env-controlled with no path check  &nbsp;`Severity: Low`

**Location:** `bot.py:39`
**Description:** `CLAUDE_BIN` can be set via `.env` to any executable. The default is correct, but a tampered `.env` (which already implies write access — see F-09) can swap in a wrapper that captures prompts. This is the same trust boundary as F-09 and is mostly noted for completeness.

**Remediation:** Pin to `/opt/homebrew/bin/claude` in code and remove the override; or, if the override is needed for portability, require the path to resolve to an absolute path and verify it is owned by root or the current user with no group/other write.

### F-11 — Markdown parse mode breaks on special characters in `cwd`  &nbsp;`Severity: Low (robustness)`

**Location:** `bot.py:101`, `bot.py:125`, `bot.py:133`
**Description:** `parse_mode="Markdown"` is used with values interpolated into backticks. A path containing `_`, `*`, or unescaped backticks can cause Telegram to return 400 Bad Request and the user sees no reply at all (the message just fails silently). Not a security issue, but it produces ghost failures that mask other problems.

**Remediation:** Switch to `parse_mode=None` and drop the backticks, or use `MarkdownV2` with `telegram.helpers.escape_markdown(text, version=2)`.

### F-12 — Logs persist `chat_id` and `session_id` in plaintext  &nbsp;`Severity: Low`

**Location:** `bot.py:158-159`
**Description:** Each message logs `chat=… session=…` to `~/.claude-bridge/launchd.out`. These rotate only when launchd recycles them (rarely). Anyone with read access to the user's home gets full chat metadata history.

**Remediation:** Log only a hash of `chat_id` and a truncated `session_id` (first 8 chars). Keep DEBUG verbosity off by default.

### F-13 — No upper bound on prompt length  &nbsp;`Severity: Informational`

**Location:** `bot.py:145-146`
**Description:** Telegram caps text messages at 4096 chars, so practical exposure is low. Defense-in-depth: enforce a length check in code so that future changes (caption forwarding, document text extraction) do not blow past `ARG_MAX` (~256 KB on macOS) or trigger pathological Claude context expansion.

### F-14 — Plist runs without sandboxing  &nbsp;`Severity: Informational`

**Location:** `~/Library/LaunchAgents/com.local.claude-bridge.plist`
**Description:** No `SoftResourceLimits`, no `sandbox-exec`. The process inherits the full user environment. Acceptable for a single-user personal tool, but worth recording as a deliberate trade-off so future hardening has a starting point.

## Quick-win checklist

If only one finding is fixed, fix **F-01** by flipping the default to `acceptEdits` and re-enabling `bypassPermissions` only behind an explicit per-session toggle. Everything else compounds on F-01.

After F-01, the highest leverage-per-line-of-code fixes are:

1. F-04 (atomic state write) — 4 lines, prevents data loss.
2. F-03 (chmod 600 on state) — 2 lines, blocks the broadest local read.
3. F-02 (cwd allowlist) — ~10 lines, removes the easiest amplification.
4. F-05 (asyncio.Lock around state) — ~5 lines, eliminates an entire class of intermittent bugs.
5. F-09 (verify `.env` mode in `run.sh`) — 4 lines, cheap.

## Out of scope

- Telegram Bot API itself (we trust HTTPS to Telegram's servers).
- Claude Code CLI internals and its handling of the prompt.
- macOS keychain / SIP / FileVault posture.
- Supply-chain integrity of `python-telegram-bot` and the Python interpreter.

## Re-review trigger

Re-run this review when any of the following change:
- `PERMISSION_MODE` default in `bot.py`.
- The set of bot commands (`/start`, `/new`, `/cd`, …) or their argument shape.
- The state schema in `state.json`.
- Number of allowed chats (single-user → multi-user widens several findings).

---

# Review #2 — 2026-05-24 (post-remediation pass)

**Scope:** working-tree state of `bot.py`, `run.sh`, `.env.example`, `README.md` after walking F-01 through F-11 with the author and applying agreed remediations. Not yet committed at the time of this review. Same threat model and out-of-scope list as Review #1.

**Method:** for each finding in Review #1, classify status as `Fixed`, `Accepted (operational)`, or `Open`; re-read the modified code to confirm the fix matches what was claimed and to surface any new observations introduced by the changes. The `Severity` column carries forward Review #1 unless explicitly downgraded.

## Status of Review #1 findings

| ID | Severity | Status | Where verified |
|---|---|---|---|
| F-01 | Critical | Accepted (operational) | `PERMISSION_MODE` default at `bot.py:52` unchanged. Mitigation = Telegram 2FA enabled by the user. |
| F-02 | High | Fixed | `is_cwd_allowed()` + `_resolve_arg()` at `bot.py:68-101`; gate in `cmd_cd` and `cmd_ls`; startup fail-fast if `DEFAULT_CWD` outside roots. Blocked attempts logged with `chat_id`, requested path, resolved path, and roots. |
| F-03 | High | Fixed | `bot.py:56-65`: `mkdir(mode=0o700)` + idempotent `os.chmod(STATE_FILE.parent, 0o700)` on every startup; startup auto-tightens existing `state.json` to `0o600` with a `WARNING` log. `save_state` chmods 0600 on every write. |
| F-04 | High | Fixed | `save_state` writes to `.json.tmp`, chmods to 0600 before rename, then `os.replace` (atomic on macOS APFS). `load_state` catches `JSONDecodeError`, renames the broken file to `state.json.corrupt` for forensics, and returns empty state. |
| F-05 | High | Fixed | Module-level `_state_lock = asyncio.Lock()`; `session_for`, `update_session`, `reset_session` are async and wrap read-modify-write in the lock; return a shallow copy so callers cannot mutate shared state. All 7 call sites use `await`. |
| F-06 | Medium | Fixed | `state["_meta"]["last_processed_update_id"]` cursor; `_claim_update()` early-return on replayed updates; `try/finally` ensures `set_last_update_id()` runs even when a handler early-returns on validation failures. Applied to all 6 handlers (`cmd_status` inherits via delegation to `cmd_start`). |
| F-07 | Medium | Accepted (with addendum) | See addendum on F-07 above. Rate limiter not implemented; relying on `concurrent_updates=False` serialization + Telegram 2FA. |
| F-08 | Medium | Fixed | `_redact()` with five patterns (`$HOME`, `/Users/<user>`, email, hex blob ≥32, `sk-…`); chat receives only `rc` + redacted last line + pointer to `launchd.err`; full untruncated stderr (up to 5KB) logged via `log.error`. |
| F-09 | Medium | Fixed | `run.sh` rejects start unless `stat -f '%Su:%A' .env` equals `$(whoami):600`. Error message includes the exact fix command. |
| F-10 | Low | Fixed (by removal) | `CLAUDE_BIN` is now a hard-coded constant at `bot.py:51`; the env override and corresponding `.env.example`/README entry were removed. Closes the configuration-driven path-hijack vector entirely rather than validating it. |
| F-11 | Low | Fixed | All four `parse_mode="Markdown"` sites removed; all backticks around interpolated UUIDs/paths removed. Plain-text replies are robust to `_`/`*`/backtick in user-controlled values. |
| F-12 | Low | Open | `bot.py:380` still logs `chat=… session=… started=…` in plaintext. Will be addressed in the next walkthrough step. |
| F-13 | Informational | Open | No prompt length cap in `on_message`. Defense-in-depth only — Telegram caps at 4096. |
| F-14 | Informational | Open | Plist still runs without `SoftResourceLimits` or `sandbox-exec`. Acceptable for single-user personal tool. |

## New observations introduced by the remediation

The remediation added three commands (`/pwd`, `/ls`) and POSIX-style relative-path resolution in `/cd`. Each was inspected for new attack surface:

- **`/pwd`** is read-only and only reveals the session's `cwd`. The `cwd` is already constrained to the allowlist (F-02 fix) and the operator already knows it, so no new info disclosure.

- **`/ls`** lists directory entries inside the allowlist. Goes through `is_cwd_allowed()` with the same logging on blocked attempts. Caps output at 80 entries (`LS_MAX_ENTRIES`) to bound message size. Uses `Path.iterdir()` (no subprocess, no shell) and `PermissionError` is caught. **No new finding.**

- **POSIX `cd` semantics** (`_resolve_arg`) — relative paths are joined with the session's `cwd`, then `os.path.normpath` collapses `..` and `.` **before** the allowlist check runs. Verified: `/cd ../../.ssh` from inside `~/EDF/Personal/Github/claude-bridge` resolves to `~/.ssh`, is rejected by the allowlist, and is logged. Symlink escape is still blocked by `resolve(strict=True)` in `is_cwd_allowed`. **No new finding.**

- **`state["_meta"]` key namespace** — added by F-06 to hold the update-id cursor. Chat IDs are integers serialized to strings, never equal to `"_meta"`. Existing iteration code (none currently iterates the top-level dict beyond keyed access) is safe today but a future audit (e.g. weekly review) should filter out `_meta` if it ever iterates the chat map. **Minor — recorded for future re-review.**

- **`os.chmod` calls at import time** (`bot.py:56-65`) run before `logging.basicConfig` in `main()`. The `log.warning` for legacy state-file permissions therefore uses the root logger's default handler — output still reaches `launchd.err` (it is stderr), but without the configured timestamp format. Cosmetic only; acceptable.

- **Behavior change visible to operator:** the bot now refuses to start if `DEFAULT_CWD` is outside `ALLOWED_CWD_ROOTS` (SystemExit), and `run.sh` refuses to start if `.env` is not `600`. Both are intentional fail-fasts; both are documented in the README. No silent failure modes were introduced.

## Residual risk summary

The dominant residual risk after this pass is **F-01 (`bypassPermissions`)**, which remains in code; the entire mitigation is Telegram-side 2FA. If the Telegram account is compromised, every other fix in this review is bypassed in seconds. The trust anchor is now explicit and singular.

Secondary residuals:

- **F-07 (rate limit)** — accepted; bot is responsive to flooding only insofar as `concurrent_updates=False` serialization holds. Flipping to true async invocation would re-open the original framing.
- **F-12 (log plaintext)** — minor local info disclosure if home directory is read by another UID.
- **F-13, F-14** — informational; no action planned this pass.

## Quick-win checklist (delta from Review #1)

After this pass, Review #1's "fix F-01 first" recommendation is unchanged in code — only the operational layer (2FA) was added. The next high-leverage fix is **F-12** (~5 lines), which is what the operator chose to walk next.

## Re-review trigger (additive to Review #1)

In addition to the original triggers, re-review when:
- The `_meta` schema in `state.json` grows additional keys (e.g. rate-limit buckets, audit logs) — confirm key namespace stays disjoint from `chat_id` strings.
- The `_redact` pattern list changes (verify no over-redaction breaks legitimate output).
- The `ALLOWED_CWD_ROOTS` default expands beyond the current three roots.
- `concurrent_updates` is set to `True` in the `Application` builder — re-open F-05 and F-07.

---

# Review #3 — 2026-05-25 (post-modularization pass)

**Reviewed:** 2026-05-25
**Commit Evaluated:** `3542445`
**Scope:** the modular tree introduced by commits `2d80262` → `3542445`:
`app/main.py`, `core/{config,logger}.py`, `service/handlers/{__init__,_common,cwd,effort,message,model,session,start}.py`,
`integrations/claude_client.py`, `repositories/session_repository.py`, `utils/{paths,redact}.py`,
`run.sh`, `launchd/com.local.claude-bridge.plist`, `.env.example`. The monolithic `bot.py`
referenced in Reviews #1 and #2 no longer exists (removed in `822d0b6`).

**Method:** re-classify every Review #1 finding against the new modular code; then read each
module end-to-end for *new* attack surface introduced by the split (handler registration, two
new commands `/effort` and `/model`, JSON output parsing in `claude_client`, packaging via
`pyproject.toml`). Same threat model and out-of-scope list as Review #1.

## Status of Review #1 findings against the modular tree

| ID | Severity | Status | Where verified |
|---|---|---|---|
| F-01 | Critical | Accepted (operational) | `PERMISSION_MODE` default still `"bypassPermissions"` at `core/config.py:23`; threaded into `integrations/claude_client.py:21`. Mitigation unchanged: Telegram 2FA. |
| F-02 | High | Fixed | Allowlist logic moved to `utils/paths.py:31-45` (`resolve(strict=True)` + ancestor check). Re-exposed via `service/handlers/_common.py:17-19`. Gated in `cwd.py:51-63` (`/cd`) and `cwd.py:82-94` (`/ls`). Startup fail-fast preserved at `app/main.py:18-22`. Blocked attempts still log `chat_id` + requested + resolved + roots. |
| F-03 | High | Fixed | `repositories/session_repository.py:13-15` creates parent with `mode=0o700` and unconditionally re-applies `0o700`; `:17-21` tightens an existing `state.json` to `0o600` with a `log.warning`. `save_state` chmods the tmp file to `0o600` before atomic rename. |
| F-04 | High | Fixed | `repositories/session_repository.py:39-43`: write to `.json.tmp` → `chmod 0600` → `os.replace`. `load_state` (`:24-36`) catches `JSONDecodeError`, renames the bad file to `state.json.corrupt`, and returns `{}`. |
| F-05 | High | Fixed | Module-level `_state_lock = asyncio.Lock()` at `repositories/session_repository.py:46`; all eight async accessors (`session_for`, `update_session`, `reset_session`, `get_last_update_id`, `set_last_update_id`) wrap read-modify-write inside `async with _state_lock`. Each returns a `dict(...)` shallow copy. All call sites in `service/handlers/*.py` use `await`. |
| F-06 | Medium | Fixed | `claim_update()` + `set_last_update_id()` at `repositories/session_repository.py:95-117`. Applied in `try/finally` blocks in every handler: `start.py:20,34`, `session.py:19,25`, `cwd.py:27/33`, `cwd.py:39/67`, `cwd.py:73/117`, `effort.py:21,47`, `model.py:21,47`, `message.py:28,79`. `cmd_status` inherits via delegation to `cmd_start`. |
| F-07 | Medium | Accepted (with addendum) | Rate limiter still not implemented. `concurrent_updates` not set explicitly, so the default `False` still applies; `message.py` still uses synchronous `subprocess.run` in `integrations/claude_client.py:49-55`. Addendum from Review #1 holds. |
| F-08 | Medium | Fixed | Redaction extracted to `utils/redact.py:8-13` (same five patterns: `$HOME`, `/Users/<user>`, email, hex ≥32, `sk-…`). Chat receives `rc` + redacted last line + pointer to `launchd.err` (`message.py:57-69`); full stderr (truncated to 5000 chars) goes to the log only. |
| F-09 | Medium | Fixed | `run.sh:6-13` rejects start unless `stat -f '%Su:%A' .env` equals `<whoami>:600`; error includes the exact `chmod`/`chown` fix command. |
| F-10 | Low | Fixed (by removal) | `CLAUDE_BIN` is a hard-coded constant at `core/config.py:22` (`/opt/homebrew/bin/claude`). No env override path; `.env.example` does not list it. |
| F-11 | Low | Fixed | No `parse_mode=` arguments survive in the modular tree. `grep -R "parse_mode" service/ app/` returns no hits. All reply text is plain. |
| F-12 | Low | **Still open** | `service/handlers/message.py:39-42` still logs `chat=<id> cwd=<path> session=<uuid> started=<bool>` in plaintext. Was scheduled as the "next walkthrough step" after Review #2 but is unchanged in this revision. |
| F-13 | Informational | Open | No upper-bound check on `prompt` length in `message.py:33-34`. Telegram caps at 4096 chars, so practical exposure unchanged. |
| F-14 | Informational | Open | `launchd/com.local.claude-bridge.plist` still has no `SoftResourceLimits` or `sandbox-exec`. The plist is now version-controlled in-tree (commit `79c0b21`), which is itself a positive — drift between the deployed and reviewed copy is now visible in `git diff`. |

## New observations introduced by the refactor

The split touched many files but rearranged behavior more than it changed it. Each delta below
was inspected for new attack surface:

- **Module-level side effects in `repositories/session_repository.py:13-21`.** `mkdir`, two
  `chmod` calls, and a conditional `log.warning` execute at *import* time, before `app/main.py`
  calls `configure_logging()`. The warning therefore goes through the root logger's default
  handler — output still reaches `launchd.err` (it is stderr) but without the configured
  timestamp prefix. Cosmetic only. **No new finding.** Same observation appears in Review #2;
  carried forward verbatim.

- **`/effort` and `/model` (`effort.py`, `model.py`) — new user-controllable flags forwarded to
  the `claude` subprocess.** Both handlers validate the argument against an in-code allowlist
  (`VALID_EFFORTS`, `VALID_MODELS` in `core/config.py:26-27`) and reject anything else with an
  error message. The accepted value is passed as a *separate* argv element (`["--effort", v]`,
  `["--model", v]`) in `integrations/claude_client.py:24-27` — no shell interpolation, no
  `shell=True`, no risk of argument splitting. Defaults read from the environment are likewise
  funneled through `parse_effort` / `parse_model` (`core/config.py:30-41`) which return `None`
  for any value outside the allowlist, so a tampered `.env` cannot inject a custom flag value.
  **No new finding.**

- **`integrations/claude_client.py:58-74` (`extract_result_text`).** Parses the JSON envelope
  emitted by `claude --output-format json`. Uses `json.loads` (no `eval`, no shell) and
  gracefully falls back to the raw stdout when the payload is not parseable or lacks a
  `result` key. The function reads from `claude`'s stdout, which we already trust as the
  subprocess we spawn ourselves. Memory exposure is bounded by Claude's own output size and by
  the 10-minute (configurable) subprocess timeout. **No new finding.**

- **Handler registration centralized in `service/handlers/__init__.py:14-23`.** Nine handlers
  total; every one wraps real work in `if not authorized(update): return` and `if not await
  claim_update(update): return`, then a `try / finally: await set_last_update_id(...)`. The
  pattern is mechanical and uniform — easy to audit, easy to break next time a handler is
  added. Recommend (operationally, not a finding) that any future handler PR is reviewed
  specifically for the auth + claim + finally trio. **No new finding.**

- **`cmd_status` delegates to `cmd_start` (`start.py:37-38`).** Both calls execute
  `claim_update` + `set_last_update_id`. The `claim_update` in the *outer* `cmd_status` would
  see the update as fresh, the *inner* call inside `cmd_start` would then see it as already
  processed — but `claim_update` only returns False; it does not raise, and the early-return
  path means `cmd_start`'s reply still fires because the cursor is not yet advanced (the
  `set_last_update_id` is inside the `finally` of `cmd_status`, not yet executed when
  `cmd_start` runs). Verified by tracing: `cmd_status` calls `cmd_start` before its own
  `finally` runs, so `last_processed_update_id` is still the old value when `cmd_start`'s
  `claim_update` runs. Both fire; the cursor is then set twice to the same value (idempotent).
  Functionally correct, but the call graph is non-obvious — **N-01** below.

- **`cmd_ls` output formatting.** Filenames are written to Telegram verbatim
  (`cwd.py:110-114`). A filename containing a newline (legal on POSIX) could split the
  rendered listing across visual rows. Not a security issue — Telegram already escapes
  control bytes for display — but worth a note for future work: **N-02** below.

- **Packaging via `pyproject.toml`.** Introduces `claude-bridge` as a console-script entry
  point. The launchd plist still calls `run.sh` (not the entry-point binary), so the `.env`
  permission check still runs. If the deployment ever switches to invoking the console script
  directly (skipping `run.sh`), the `.env` mode check is bypassed. **N-03** below — recorded
  as a re-review trigger, not an active finding.

## New findings (this pass)

### N-01 — Double-claim of `update_id` in `/status` delegation chain &nbsp;`Severity: Informational`

**Location:** `service/handlers/start.py:37-38` (delegation), `start.py:20`, `start.py:34` (inner claim + finally)

**Description:** `cmd_status` is a one-liner that awaits `cmd_start`. Both functions call
`claim_update` and both schedule `set_last_update_id` in `finally`. The outer `claim_update`
returns True (fresh update), runs `cmd_start`, which itself calls `claim_update` — also True
because the outer cursor advance hasn't happened yet — and then both `finally` blocks run
`set_last_update_id`. The second call is a no-op (`set_last_update_id` only advances when the
new id is strictly greater than the stored one), so behavior is correct, but the code reads
as if it depends on that idempotency property without saying so.

**Impact:** None today. Risk is that someone refactors `set_last_update_id` to always assign
(removing the `>` guard) and silently breaks no other handler — only this one.

**Remediation:** simplest fix is to invert the delegation: `cmd_start` becomes a thin alias for
`cmd_status`, or both share a `_show_status(update, ctx)` private function and each is a
single-claim handler. Either approach removes the implicit dependency on idempotency.

### N-02 — `/ls` echoes raw filenames into Telegram &nbsp;`Severity: Informational`

**Location:** `service/handlers/cwd.py:110-114`

**Description:** `Path.iterdir()` returns names verbatim. A directory containing a filename
like `"hello\nworld"` (legal on POSIX) renders as two visual rows in the Telegram reply, and a
malicious filename could mimic the formatted header (`{target} — N dirs, M files`). This is
inside the allowlist and Markdown is disabled (F-11 fix), so it cannot produce a clickable
exploit, but it can mislead the operator about what's on disk.

**Impact:** Cosmetic / minor display-spoofing. Not exploitable beyond the existing F-01 trust
boundary.

**Remediation:** replace control characters in each entry before printing, e.g.
`e.name.encode("unicode_escape").decode()` or `repr(e.name)` when the name contains
non-printables.

### N-03 — Future deployment path (`claude-bridge` console script) would bypass `.env` mode check &nbsp;`Severity: Informational`

**Location:** `pyproject.toml` (entry point), `run.sh:6-13` (the check that would be skipped),
`launchd/com.local.claude-bridge.plist:9-11` (currently still calls `run.sh`).

**Description:** Today the launchd job invokes `run.sh`, which enforces the `.env`
ownership/mode invariant (F-09 fix). The new packaging adds `claude-bridge` as a console-script
entry point installable via pip. If the operator (or a future README change) switches the
plist or any other invocation path to call the entry-point binary directly, the `.env` check
is silently bypassed — and the bot starts whether or not `.env` is `0600`.

**Impact:** Latent regression of F-09. Not exploitable today; flagged because the packaging
change increases the number of ways the bot can be launched.

**Remediation:**
- (Lowest cost) keep `run.sh` as the only blessed entrypoint; document this in `README.md` and
  in a comment at the top of `pyproject.toml`'s `[project.scripts]` block.
- (Better) move the `.env` mode check into Python (e.g. early in `app/main.py:main()`) so that
  every invocation path enforces it regardless of how the process is started.

## Residual risk summary (delta from Review #2)

The dominant residual risk is still **F-01 (`bypassPermissions`)**; the mitigation is still
Telegram 2FA only. Everything else compounds on F-01, and the refactor did not change that.

Secondary residuals carried forward:

- **F-07 (rate limit)** — still accepted; serialization via `concurrent_updates=False` holds.
- **F-12 (log plaintext)** — still open; intended as the "next walkthrough step" but unchanged.
- **F-13, F-14** — informational; no action planned this pass.
- **N-01, N-02, N-03** — new, all Informational; recorded for future hardening.

## Quick-win checklist (delta from Review #2)

After this pass, the highest-leverage remaining fixes are unchanged in priority:

1. **F-12** (~5 lines in `service/handlers/message.py:39-42` and `repositories/session_repository.py`)
   — hash `chat_id`, truncate `session_id` to 8 chars.
2. **N-03** (~5 lines in `app/main.py`) — move the `.env` mode check into Python so every
   launch path enforces it.
3. **N-01** (~5 lines in `service/handlers/start.py`) — collapse the delegation into a shared
   private helper to remove the implicit idempotency assumption.

## Re-review trigger (additive to Reviews #1 and #2)

Re-run this review when any of the following change:
- The `_meta` schema in `state.json` grows additional keys.
- The `_redact` pattern list in `utils/redact.py` changes.
- `ALLOWED_CWD_ROOTS` default expands beyond the current three roots.
- `concurrent_updates` is set to `True` on the `Application` builder.
- The launchd plist (or any other invocation path) stops calling `run.sh` and invokes the
  `claude-bridge` console script directly (see **N-03**).
- A new handler is added under `service/handlers/` — verify the `authorized + claim_update +
  try/finally set_last_update_id` trio is present and that any new subprocess flag is
  allowlisted in `core/config.py`.
- `VALID_EFFORTS` or `VALID_MODELS` in `core/config.py` are widened, especially to include
  values that contain shell-special characters.

---

# Review #4 — 2026-05-27 (post-Stages-2/3 pass)

**Reviewed:** 2026-05-27
**Commit Evaluated:** `418c9ce`
**Scope:** every commit between `3542445` (end of Review #3) and `418c9ce`:
permission-denial surfacing (`1ba1214`), conversation logging sink (`4f7de4a`), `/context`
slash command (`aace8ef`), configurable log level (`3f37f5a`), `effective_message` switch
(`57930ff`), the cost-alert system (`051cce1`), `/usage` command and the `claude_pricing`
module (`00e8839`), the slow-response keepalive (`f8da3e5`), and the interactive
Approve/Reject flow added in Stage 3 (`418c9ce`). New code touched: `service/handlers/{approval,context,usage,_approvals}.py`,
`service/handlers/message.py` (rewrites), `integrations/{claude_context,claude_context_render,claude_usage,claude_usage_render,claude_pricing}.py`,
`scripts/cost_alert.py` + launchd plist, install/uninstall scripts.

**Method:** re-classify the carry-overs (F-12, F-13, F-14, N-01, N-02, N-03) against the new
tree; then read each new handler and integration module end-to-end for surface added by the
Stages 2–3 work, plus the cost-alert system. Same threat model and out-of-scope list as
Review #1, with one update under "Threat model delta" below.

## Threat model delta

Two pieces of code now widen the trust surface:

1. **`/context` and `/usage` re-invoke the Claude CLI from the bot.** `/context` does so to
   synthesise the live `/context` slash command (`integrations/claude_context.py:129-164`);
   `/usage` reads the local transcript instead, so it does *not* invoke the CLI but does
   parse JSONL with attacker-influenced content if the transcript ever contains crafted rows
   (out of scope today — only Claude writes there). Both run on the same trust anchor as
   `/message`: an authorised `chat_id`.
2. **The cost-alert agent is now an independent launchd job** that reads
   `~/.claude-bridge/state.json` and every transcript under `~/.claude/projects/*` and sends
   email via `osascript`. It runs under the user UID with no allowlist concept — it does not
   accept inputs from Telegram, so the attack surface is local files only.

Both are evaluated below; neither changes the dominant F-01 risk.

## Status of carry-over findings

| ID | Severity | Status (Review #4) | Where verified |
|---|---|---|---|
| F-01 | Critical | **Downgraded operationally (still Open in code)** | `core/config.py:23` still defaults to `acceptEdits`; the new Stage 3 inline-approval flow (`418c9ce`) lets the operator keep `default`/`acceptEdits` without losing convenience. The Telegram-2FA mitigation still anchors the residual risk. See **F-01 update** below. |
| F-12 | Low | **Fixed** | `service/handlers/message.py:68-77` now redacts the prompt and truncates at 4000 chars before writing to `conversation.log`; the `bridge.log` line at `message.py:68-71` still emits `session=<uuid>` in full but no longer co-locates the prompt body. The conversation log is a separate rotating sink (`core/logger.py`). Residual exposure: `session_id` still in plaintext; documented as accepted in the operator-only log path. |
| F-13 | Informational | Open | No upper-bound check on `prompt` length in `message.py`. Unchanged. |
| F-14 | Informational | Open | Plist still has no `SoftResourceLimits` / `sandbox-exec`. Now templated with `__HOME__` / `__PROJECT_DIR__` placeholders (Stage 4), but the security envelope is identical. |
| N-01 | Informational | Open | `cmd_status` still delegates to `cmd_start`; both call `claim_update`. Idempotency of `set_last_update_id` continues to mask the double-claim. Not exploitable; documented technical debt. |
| N-02 | Informational | Open | `cwd.py` listing of filenames unchanged. |
| N-03 | Informational | **Closed (by deployment choice)** | Stage 4 added `scripts/install_service.sh` which itself checks `.env` mode before installing the plist, and the plist still calls `run.sh` (which re-checks). The `pyproject.toml` entry point is not referenced by any deployed path. Re-open if that changes. |

### F-01 update (operational downgrade only)

Stage 3 added inline `✅ Approve & retry` / `❌ Reject` buttons in the permission-denial
notice (`service/handlers/approval.py` + `_approvals.py`). The operator can now keep
`CLAUDE_BRIDGE_PERMISSION_MODE=default` without giving up the ability to authorise a
sensitive Bash call from Telegram in-flight. The retry path narrows `--allowedTools` to the
*exact* denied invocation (`Bash('rm /tmp/x y')` with POSIX shell-quoting in `_approvals.allowed_tool_spec`)
rather than a pattern, so an approval cannot be widened to a family by accident. Approvals are
single-use (`claim()` pops the entry on first decode) and TTL'd at 30 minutes. A second-round
denial aborts instead of looping (`approval.py:124-131`).

Net effect: the operational layer is meaningfully thicker. The compromise vector (Telegram
account takeover) still bypasses the buttons — an attacker authoring messages from an
allow-listed chat just clicks Approve themselves — so F-01 stays open in code. Severity is
unchanged in the threat model; only the cost of running in `default`/`acceptEdits` dropped to
the point where keeping `bypassPermissions` is no longer the obvious default. Recommendation
flipped: **flip the default to `default`** in a future commit (not done here to avoid coupling
Stage 4 with a behavioural change for existing deployments).

## New observations (this pass)

### N-04 — Approval token search space &nbsp;`Severity: Informational`

**Location:** `service/handlers/_approvals.py:42` (`secrets.token_urlsafe(9)`).

**Description:** approval tokens are 9 random bytes (72 bits) urlsafe-encoded. They are not
cryptographic identifiers per se — the `claim()` lookup compares the chat ID before
returning the entry, so a guessing attacker would also need to be in the allow-listed chat
to land on a live token. Even ignoring that, 72 bits is well beyond practical brute force.
**Not a finding**; recorded so future tightening (e.g. attempting to shorten the callback
payload) doesn't accidentally drop below 64 bits.

### N-05 — In-memory pending-approval store &nbsp;`Severity: Informational`

**Location:** `service/handlers/_approvals.py:51-83`.

**Description:** the pending-approval store is a module-level dict. It is rebuilt empty on
every process restart — meaning a denied prompt's buttons become inert after a launchd
crash + restart. The user sees "Request expired or already handled" if they click. This is
the desired failure mode (do not silently retry across restarts) and matches the TTL semantics;
the only concern would be if anyone later moves the store to disk without preserving the
trust requirement that approved retries only fire for the *same* chat that issued the prompt.
**No finding today**; documented for re-review trigger.

### N-06 — Inline-keyboard messages persist on Telegram's servers indefinitely &nbsp;`Severity: Informational`

**Location:** `service/handlers/message.py:131-142` (the keyboard send), `approval.py:71-75`
(the post-decision `edit_message_reply_markup` call).

**Description:** when Approve or Reject runs, the bot removes the keyboard from the *original*
denial notice but does not delete the message itself. The text body — which includes the
redacted but still potentially revealing `tool_input` (e.g. a file path) — remains on
Telegram's servers. Same exposure shape as F-08 (subprocess stderr to chat): info reaches
Telegram's cloud and any enabled backup. Not exploitable beyond the F-08 envelope; flagged so
that a future hardening pass treating Telegram as untrusted storage is internally consistent.

### N-07 — `claude_pricing.MODEL_RATES` is fixed at code time &nbsp;`Severity: Informational`

**Location:** `integrations/claude_pricing.py:23-42`.

**Description:** the cost computed by `/usage` and the cost-alert fallback both rely on a
hard-coded rate table (Opus/Sonnet/Haiku 4.x, Anthropic public list prices as of 2026-05).
When Anthropic publishes a new model family or revises rates, those numbers diverge silently.
The fallback (`rates_for` returns `DEFAULT_RATES = MODEL_RATES["haiku-4"]` for unknown ids)
errs toward under-reporting, which is the right direction for an alert threshold (no spurious
emails) but the wrong direction for `/usage`'s user-visible total (under-charge surprise).
**No finding** today; recorded so the next family bump remembers to update the table.

### N-08 — `cost_alert` reads every transcript under `~/.claude/projects/*` &nbsp;`Severity: Informational`

**Location:** `scripts/cost_alert.py:60-66`, `:217-219`.

**Description:** the agent matches transcripts by `glob("*/<session_id>.jsonl")` and reads
each match in full. A transcript can be hundreds of MB on a long-running session. Memory
exposure is per-line streaming so the cap is bounded, but the agent does open every state
file's matched transcript every hour. If `~/.claude/projects` were ever symlinked or used as
a generic working directory (unusual), the agent would happily traverse the link. Today the
directory is fully under Claude CLI's control. **No finding**; trigger a re-review if the
projects directory ever stops being claude-CLI-only.

### N-09 — `osascript` recipient is interpolated into AppleScript without escaping the `@` host parse &nbsp;`Severity: Low`

**Location:** `scripts/cost_alert.py:173-190`.

**Description:** `send_email` formats `subject`, `body`, and `recipient` into an AppleScript
literal via `.format()`. `subject` and `body` are escaped for quotes and newlines. `recipient`
is interpolated verbatim — a value containing a stray `"` would break the script. Today
`recipient` comes from `COST_ALERT_RECIPIENT` (env, operator-controlled), so the only attacker
is the operator who already controls `.env`. Same trust boundary as F-09. Hardening cost is
~3 lines (same quote-replace as the other fields). **Recorded as minor-debt**, not blocking.

## Residual risk summary (delta from Review #3)

- **F-01** stays open in code; operational mitigation is now `Approve & retry` + Telegram 2FA.
  Severity unchanged.
- **F-12** moved to Fixed.
- **N-03** moved to Closed.
- **New (N-04 through N-09)** all Informational or Low — none actionable today; all carry a
  re-review trigger.

## Quick-win checklist (delta from Review #3)

After this pass, the highest-leverage remaining fixes are:

1. **F-01 default flip** — change `core/config.py:23` to `default` (or document loudly that
   `bypassPermissions` is now a *power user* opt-in, given Approve buttons exist).
2. **N-09** (~3 lines in `cost_alert.send_email`) — escape `recipient` like the other fields.
3. **N-01** (~5 lines in `service/handlers/start.py`) — still the leftover from Review #3.

## Re-review trigger (additive to Reviews #1, #2, #3)

Re-run this review when any of the following change:

- `_approvals._pending` ever moves to disk (durable retry across restart) — confirm the
  chat-id binding still enforces the trust requirement under restart.
- `MODEL_RATES` in `claude_pricing.py` is updated for a new Claude family — verify both
  `/usage` and the cost-alert fallback continue to under-report rather than over-report on
  unknown model ids.
- A new permission-denial-handling path is added (e.g. a slash-command equivalent of Approve)
  — re-evaluate the chat-id binding and TTL there.
- The cost-alert agent learns to read or write any path outside `~/.claude-bridge/` and
  `~/.claude/projects/` — re-scope N-08.
- `service/handlers/approval.py` ever calls `run_claude` with a *broader* `allowed_tools`
  than the exact denied invocation — that would re-widen F-01 in a way the operator may not
  expect.
- The plist's `__PROJECT_DIR__` / `__HOME__` placeholders are referenced from any path that
  is *not* `scripts/install_service.sh` — confirm the substitution happens before
  `plutil -lint` accepts it.

