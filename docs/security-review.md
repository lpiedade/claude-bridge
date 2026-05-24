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
- Enforce an allowlist root via env var (e.g. `CLAUDE_BRIDGE_CWD_ROOTS=~/EDF/Personal/Github,/tmp`) and reject any `new_cwd` whose `Path.resolve()` is not under one of those roots.
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
