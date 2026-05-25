"""/new — start a fresh claude session."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from repositories.session_repository import (
    claim_update,
    reset_session,
    set_last_update_id,
)

from ._common import authorized


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await claim_update(update):
        return
    try:
        info = await reset_session(update.effective_chat.id)
        await update.message.reply_text(f"New session: {info['session_id']}")
    finally:
        await set_last_update_id(update.update_id)
