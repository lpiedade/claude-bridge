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
    /effort  - show or set effort level: `/effort high` (low|medium|high|xhigh|max|none)
    /model   - show or set model: `/model opus` (opus|sonnet|haiku|default)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import uuid
from pathlib import Path

from core.config import (
    ALLOWED_CHAT_IDS,
    ALLOWED_CWD_ROOTS,
    BOT_TOKEN,
    CLAUDE_BIN,
    DEFAULT_CWD,
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    PERMISSION_MODE,
    TIMEOUT_SECONDS,
    VALID_EFFORTS,
    VALID_MODELS,
    parse_effort as _parse_effort,
    parse_model as _parse_model,
)
from core.logger import configure as _configure_logging, log
from integrations.claude_client import extract_result_text
from utils.paths import (
    is_cwd_allowed as _is_cwd_allowed_impl,
    resolve_arg as _resolve_arg,
    safe_resolve as _safe_resolve,
)
from utils.redact import redact as _redact

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

STATE_FILE = Path.home() / ".claude-bridge" / "state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(STATE_FILE.parent, 0o700)

if STATE_FILE.exists():
    _mode = STATE_FILE.stat().st_mode & 0o777
    if _mode != 0o600:
        log.warning("state.json had mode %o; tightening to 0600", _mode)
        os.chmod(STATE_FILE, 0o600)


def is_cwd_allowed(path: str) -> bool:
    return _is_cwd_allowed_impl(path, ALLOWED_CWD_ROOTS)


if not is_cwd_allowed(DEFAULT_CWD):
    raise SystemExit(
        f"DEFAULT_CWD {DEFAULT_CWD!r} is not under any of "
        f"CLAUDE_BRIDGE_CWD_ROOTS={[str(r) for r in ALLOWED_CWD_ROOTS]!r}"
    )


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError as e:
        backup = STATE_FILE.with_suffix(".json.corrupt")
        log.error(
            "state.json is corrupt (%s); moved to %s and starting empty",
            e, backup,
        )
        STATE_FILE.rename(backup)
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_FILE)


_state_lock = asyncio.Lock()


def _new_session_entry() -> dict:
    return {
        "session_id": str(uuid.uuid4()),
        "cwd": DEFAULT_CWD,
        "effort": DEFAULT_EFFORT,
        "model": DEFAULT_MODEL,
        "started": False,
    }


async def session_for(chat_id: int) -> dict:
    async with _state_lock:
        state = load_state()
        key = str(chat_id)
        if key not in state:
            state[key] = _new_session_entry()
            save_state(state)
        else:
            defaults = _new_session_entry()
            missing = {k: v for k, v in defaults.items() if k not in state[key]}
            if missing:
                state[key].update(missing)
                save_state(state)
        return dict(state[key])


async def update_session(chat_id: int, **changes) -> dict:
    async with _state_lock:
        state = load_state()
        key = str(chat_id)
        if key not in state:
            state[key] = _new_session_entry()
        state[key].update(changes)
        save_state(state)
        return dict(state[key])


async def reset_session(chat_id: int) -> dict:
    async with _state_lock:
        state = load_state()
        key = str(chat_id)
        state[key] = _new_session_entry()
        save_state(state)
        return dict(state[key])


async def get_last_update_id() -> int:
    async with _state_lock:
        state = load_state()
        return state.get("_meta", {}).get("last_processed_update_id", 0)


async def set_last_update_id(update_id: int) -> None:
    async with _state_lock:
        state = load_state()
        meta = state.setdefault("_meta", {})
        if update_id > meta.get("last_processed_update_id", 0):
            meta["last_processed_update_id"] = update_id
            save_state(state)


async def _claim_update(update: Update) -> bool:
    """True if this update is fresh; False if it was already processed."""
    uid = update.update_id
    last = await get_last_update_id()
    if uid <= last:
        log.info("skipping replayed update_id=%s (last=%s)", uid, last)
        return False
    return True


def authorized(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.id in ALLOWED_CHAT_IDS


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await _claim_update(update):
        return
    try:
        info = await session_for(update.effective_chat.id)
        await update.message.reply_text(
            "Claude bridge online.\n"
            f"Session: {info['session_id']}\n"
            f"CWD: {info['cwd']}\n"
            f"Permission mode: {PERMISSION_MODE}\n"
            f"Effort: {info.get('effort') or '(default)'}\n"
            f"Model: {info.get('model') or '(default)'}\n\n"
            "Commands: /new /cd /pwd /ls /effort /model /status",
        )
    finally:
        await set_last_update_id(update.update_id)


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await _claim_update(update):
        return
    try:
        info = await reset_session(update.effective_chat.id)
        await update.message.reply_text(f"New session: {info['session_id']}")
    finally:
        await set_last_update_id(update.update_id)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


async def cmd_pwd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await _claim_update(update):
        return
    try:
        info = await session_for(update.effective_chat.id)
        await update.message.reply_text(info["cwd"])
    finally:
        await set_last_update_id(update.update_id)


async def cmd_cd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await _claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
        if not ctx.args:
            await update.message.reply_text(f"CWD: {info['cwd']}")
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
        info = await update_session(chat_id, cwd=new_cwd)
        await update.message.reply_text(f"CWD: {info['cwd']}")
    finally:
        await set_last_update_id(update.update_id)


LS_MAX_ENTRIES = 80


async def cmd_ls(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await _claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
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
            entries = sorted(
                Path(target).iterdir(),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
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
    finally:
        await set_last_update_id(update.update_id)


async def cmd_effort(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await _claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
        if not ctx.args:
            await update.message.reply_text(
                f"Effort: {info.get('effort')}\n"
                f"Default: {DEFAULT_EFFORT}\n"
                f"Usage: /effort <{'|'.join(sorted(VALID_EFFORTS))}|default>"
            )
            return
        arg = ctx.args[0].strip().lower()
        if arg == "default":
            info = await update_session(chat_id, effort=DEFAULT_EFFORT)
            await update.message.reply_text(f"Effort: {info['effort']} (default)")
            return
        if arg not in VALID_EFFORTS:
            await update.message.reply_text(
                f"Invalid effort: {arg}. "
                f"Choose: {', '.join(sorted(VALID_EFFORTS))}, default"
            )
            return
        info = await update_session(chat_id, effort=arg)
        await update.message.reply_text(f"Effort: {info['effort']}")
    finally:
        await set_last_update_id(update.update_id)


async def cmd_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await _claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
        if not ctx.args:
            await update.message.reply_text(
                f"Model: {info.get('model') or '(default)'}\n"
                f"Default: {DEFAULT_MODEL}\n"
                f"Usage: /model <{'|'.join(sorted(VALID_MODELS))}|default>"
            )
            return
        arg = ctx.args[0].strip().lower()
        if arg == "default":
            info = await update_session(chat_id, model=DEFAULT_MODEL)
            await update.message.reply_text(f"Model: {info['model']} (default)")
            return
        if arg not in VALID_MODELS:
            await update.message.reply_text(
                f"Invalid model: {arg}. "
                f"Choose: {', '.join(sorted(VALID_MODELS))}, default"
            )
            return
        info = await update_session(chat_id, model=arg)
        await update.message.reply_text(f"Model: {info['model']}")
    finally:
        await set_last_update_id(update.update_id)


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        await update.message.reply_text("Unauthorized.")
        return
    if not await _claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
        prompt = update.message.text or ""
        if not prompt.strip():
            return

        await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)

        cmd = [CLAUDE_BIN, "-p", prompt, "--permission-mode", PERMISSION_MODE,
               "--output-format", "json"]
        effort = info.get("effort")
        if effort:
            cmd += ["--effort", effort]
        model = info.get("model")
        if model:
            cmd += ["--model", model]
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
            err_full = (result.stderr or result.stdout or "").strip()
            log.error(
                "claude rc=%s chat=%s stderr=%s",
                result.returncode, chat_id, err_full[:5000],
            )
            err_safe = _redact(err_full)
            last_line = err_safe.splitlines()[-1] if err_safe else "(no stderr)"
            await update.message.reply_text(
                f"Claude exited with rc={result.returncode}.\n"
                f"Last line: {last_line[:500]}\n"
                f"Full stderr in launchd.err"
            )
            return

        await update_session(chat_id, started=True)

        text = extract_result_text(result.stdout)

        text = text.strip() or "(no output)"
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i + 4000])
    finally:
        await set_last_update_id(update.update_id)


def main() -> None:
    _configure_logging()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("cd", cmd_cd))
    app.add_handler(CommandHandler("pwd", cmd_pwd))
    app.add_handler(CommandHandler("ls", cmd_ls))
    app.add_handler(CommandHandler("effort", cmd_effort))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("Bridge online. Allowed chats: %s", ALLOWED_CHAT_IDS)
    app.run_polling()


if __name__ == "__main__":
    main()
