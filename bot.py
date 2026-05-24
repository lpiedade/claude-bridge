#!/usr/bin/env python3
"""Telegram -> Claude Code bridge.

Receives messages from a whitelisted Telegram chat, forwards them to the
local `claude` CLI as a persistent session, and replies with the result.

Commands:
    /start   - show current session id, cwd, permission mode
    /new     - start a fresh session (clears memory)
    /status  - same info as /start
    /cd      - show or set working directory: `/cd ~/EDF/BlindBet`
    /pwd     - print current working directory
    /ls      - list entries in cwd (or in an allowed path): `/ls ~/EDF/BlindBet`
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.environ["CLAUDE_BRIDGE_TG_TOKEN"]
ALLOWED_CHAT_IDS = {
    int(x) for x in os.environ["CLAUDE_BRIDGE_ALLOWED_CHATS"].split(",") if x.strip()
}
DEFAULT_CWD = os.path.expanduser(
    os.environ.get("CLAUDE_BRIDGE_CWD", "~/EDF/Personal/Github")
)
ALLOWED_CWD_ROOTS = [
    Path(os.path.expanduser(p)).resolve()
    for p in os.environ.get(
        "CLAUDE_BRIDGE_CWD_ROOTS",
        "~/EDF/Personal/Github,~/EDF/BlindBet,/tmp",
    ).split(",")
    if p.strip()
]
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/opt/homebrew/bin/claude")
PERMISSION_MODE = os.environ.get("CLAUDE_BRIDGE_PERMISSION_MODE", "bypassPermissions")
TIMEOUT_SECONDS = int(os.environ.get("CLAUDE_BRIDGE_TIMEOUT", "600"))

STATE_FILE = Path.home() / ".claude-bridge" / "state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(STATE_FILE.parent, 0o700)

log = logging.getLogger("claude-bridge")

if STATE_FILE.exists():
    _mode = STATE_FILE.stat().st_mode & 0o777
    if _mode != 0o600:
        log.warning("state.json had mode %o; tightening to 0600", _mode)
        os.chmod(STATE_FILE, 0o600)


def _resolve_arg(arg: str, base_cwd: str) -> str:
    """Resolve a user-provided path argument with POSIX `cd` semantics.

    - `~` is expanded against the user's home directory.
    - Absolute paths (post-expansion) are returned unchanged.
    - Relative paths are joined with `base_cwd` and normalized so that
      `..` and `.` segments collapse before any allowlist check runs.
    """
    arg = os.path.expanduser(arg)
    if os.path.isabs(arg):
        return os.path.normpath(arg)
    return os.path.normpath(os.path.join(base_cwd, arg))


def _safe_resolve(path: str) -> str:
    """Best-effort resolve for logging; never raises."""
    try:
        return str(Path(os.path.expanduser(path)).resolve(strict=False))
    except (OSError, RuntimeError):
        return path


def is_cwd_allowed(path: str) -> bool:
    """Check that `path` exists and resolves under one of ALLOWED_CWD_ROOTS.

    Uses strict resolution so symlinks pointing outside the allowlist are
    rejected (a symlinked dir resolves to its real target before the check).
    """
    try:
        resolved = Path(os.path.expanduser(path)).resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return any(
        resolved == root or root in resolved.parents
        for root in ALLOWED_CWD_ROOTS
    )


if not is_cwd_allowed(DEFAULT_CWD):
    raise SystemExit(
        f"DEFAULT_CWD {DEFAULT_CWD!r} is not under any of "
        f"CLAUDE_BRIDGE_CWD_ROOTS={[str(r) for r in ALLOWED_CWD_ROOTS]!r}"
    )


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))
    os.chmod(STATE_FILE, 0o600)


def session_for(chat_id: int) -> dict:
    state = load_state()
    key = str(chat_id)
    if key not in state:
        state[key] = {
            "session_id": str(uuid.uuid4()),
            "cwd": DEFAULT_CWD,
            "started": False,
        }
        save_state(state)
    return state[key]


def update_session(chat_id: int, **changes) -> dict:
    state = load_state()
    key = str(chat_id)
    state.setdefault(key, session_for(chat_id))
    state[key].update(changes)
    save_state(state)
    return state[key]


def reset_session(chat_id: int) -> dict:
    return update_session(
        chat_id, session_id=str(uuid.uuid4()), started=False
    )


def authorized(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.id in ALLOWED_CHAT_IDS


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    info = session_for(update.effective_chat.id)
    await update.message.reply_text(
        "Claude bridge online.\n"
        f"Session: `{info['session_id']}`\n"
        f"CWD: `{info['cwd']}`\n"
        f"Permission mode: `{PERMISSION_MODE}`\n\n"
        "Commands: /new /cd /pwd /ls /status",
        parse_mode="Markdown",
    )


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    info = reset_session(update.effective_chat.id)
    await update.message.reply_text(
        f"New session: `{info['session_id']}`", parse_mode="Markdown"
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


async def cmd_pwd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    info = session_for(update.effective_chat.id)
    await update.message.reply_text(info["cwd"])


async def cmd_cd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    chat_id = update.effective_chat.id
    info = session_for(chat_id)
    if not ctx.args:
        await update.message.reply_text(
            f"CWD: `{info['cwd']}`", parse_mode="Markdown"
        )
        return
    new_cwd = _resolve_arg(ctx.args[0], info["cwd"])
    if not Path(new_cwd).is_dir():
        await update.message.reply_text(f"Not a directory: {new_cwd}")
        return
    if not is_cwd_allowed(new_cwd):
        log.warning(
            "blocked /cd: chat=%s requested=%r resolved=%r allowed_roots=%s",
            chat_id,
            new_cwd,
            _safe_resolve(new_cwd),
            [str(r) for r in ALLOWED_CWD_ROOTS],
        )
        allowed = ", ".join(str(r) for r in ALLOWED_CWD_ROOTS)
        await update.message.reply_text(
            f"Path not in allowed roots: {new_cwd}\nAllowed: {allowed}"
        )
        return
    info = update_session(chat_id, cwd=new_cwd)
    await update.message.reply_text(
        f"CWD: `{info['cwd']}`", parse_mode="Markdown"
    )


LS_MAX_ENTRIES = 80


async def cmd_ls(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    chat_id = update.effective_chat.id
    info = session_for(chat_id)
    target = _resolve_arg(ctx.args[0], info["cwd"]) if ctx.args else info["cwd"]
    if not Path(target).is_dir():
        await update.message.reply_text(f"Not a directory: {target}")
        return
    if not is_cwd_allowed(target):
        log.warning(
            "blocked /ls: chat=%s requested=%r resolved=%r allowed_roots=%s",
            chat_id,
            target,
            _safe_resolve(target),
            [str(r) for r in ALLOWED_CWD_ROOTS],
        )
        allowed = ", ".join(str(r) for r in ALLOWED_CWD_ROOTS)
        await update.message.reply_text(
            f"Path not in allowed roots: {target}\nAllowed: {allowed}"
        )
        return
    try:
        entries = sorted(Path(target).iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        await update.message.reply_text(f"Permission denied: {target}")
        return
    if not entries:
        await update.message.reply_text(f"{target}\n(empty)")
        return
    n_dirs = sum(1 for e in entries if e.is_dir())
    n_files = len(entries) - n_dirs
    shown = entries[:LS_MAX_ENTRIES]
    lines = [f"{target} — {n_dirs} dirs, {n_files} files", ""]
    for e in shown:
        suffix = "/" if e.is_dir() else ""
        lines.append(f"{e.name}{suffix}")
    if len(entries) > LS_MAX_ENTRIES:
        lines.append(f"... ({len(entries) - LS_MAX_ENTRIES} more)")
    await update.message.reply_text("\n".join(lines))


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        await update.message.reply_text("Unauthorized.")
        return

    chat_id = update.effective_chat.id
    info = session_for(chat_id)
    prompt = update.message.text or ""
    if not prompt.strip():
        return

    await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)

    cmd = [CLAUDE_BIN, "-p", prompt, "--permission-mode", PERMISSION_MODE,
           "--output-format", "json"]
    if info.get("started"):
        cmd += ["--resume", info["session_id"]]
    else:
        cmd += ["--session-id", info["session_id"]]

    log.info("chat=%s cwd=%s session=%s started=%s",
             chat_id, info["cwd"], info["session_id"], info["started"])

    try:
        result = subprocess.run(
            cmd,
            cwd=info["cwd"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        await update.message.reply_text(f"Timed out after {TIMEOUT_SECONDS}s.")
        return

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        await update.message.reply_text(f"Error (rc={result.returncode}):\n{err[:3500]}")
        return

    update_session(chat_id, started=True)

    text = result.stdout
    try:
        payload = json.loads(result.stdout)
        text = payload.get("result", result.stdout)
    except json.JSONDecodeError:
        pass

    text = text.strip() or "(no output)"
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("cd", cmd_cd))
    app.add_handler(CommandHandler("pwd", cmd_pwd))
    app.add_handler(CommandHandler("ls", cmd_ls))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("Bridge online. Allowed chats: %s", ALLOWED_CHAT_IDS)
    app.run_polling()


if __name__ == "__main__":
    main()
