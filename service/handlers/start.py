"""/start and /status — show session info."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from core.config import PERMISSION_MODE
from repositories.session_repository import (
    claim_update,
    session_for,
    set_last_update_id,
)

from ._common import authorized


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await claim_update(update):
        return
    try:
        info = await session_for(update.effective_chat.id)
        await update.effective_message.reply_text(
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


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)
