#!/usr/bin/env python3
"""Telegram -> Claude Code bridge.

Receives messages from a whitelisted Telegram chat, forwards them to the
local `claude` CLI as a persistent session, and replies with the result.

Commands:
    /start   - show current session id, cwd, permission mode
    /new     - start a fresh session (clears memory)
    /status  - same info as /start
    /cwd     - show or set working directory: `/cwd ~/EDF/BlindBet`
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
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/opt/homebrew/bin/claude")
PERMISSION_MODE = os.environ.get("CLAUDE_BRIDGE_PERMISSION_MODE", "bypassPermissions")
TIMEOUT_SECONDS = int(os.environ.get("CLAUDE_BRIDGE_TIMEOUT", "600"))

STATE_FILE = Path.home() / ".claude-bridge" / "state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("claude-bridge")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


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
        "Commands: /new /cwd /status",
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


async def cmd_cwd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    chat_id = update.effective_chat.id
    if not ctx.args:
        info = session_for(chat_id)
        await update.message.reply_text(
            f"CWD: `{info['cwd']}`", parse_mode="Markdown"
        )
        return
    new_cwd = os.path.expanduser(ctx.args[0])
    if not Path(new_cwd).is_dir():
        await update.message.reply_text(f"Not a directory: {new_cwd}")
        return
    info = update_session(chat_id, cwd=new_cwd)
    await update.message.reply_text(
        f"CWD: `{info['cwd']}`", parse_mode="Markdown"
    )


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
    app.add_handler(CommandHandler("cwd", cmd_cwd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("Bridge online. Allowed chats: %s", ALLOWED_CHAT_IDS)
    app.run_polling()


if __name__ == "__main__":
    main()
