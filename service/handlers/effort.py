"""/effort — show or set the per-session effort level."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from core import config
from repositories.session_repository import (
    claim_update,
    session_for,
    set_last_update_id,
    update_session,
)

from ._common import authorized


async def cmd_effort(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
        if not ctx.args:
            await update.effective_message.reply_text(
                f"Effort: {info.get('effort')}\n"
                f"Default: {config.DEFAULT_EFFORT}\n"
                f"Usage: /effort <{'|'.join(sorted(config.VALID_EFFORTS))}|default>"
            )
            return
        arg = ctx.args[0].strip().lower()
        if arg == "default":
            info = await update_session(chat_id, effort=config.DEFAULT_EFFORT)
            await update.effective_message.reply_text(f"Effort: {info['effort']} (default)")
            return
        if arg not in config.VALID_EFFORTS:
            await update.effective_message.reply_text(
                f"Invalid effort: {arg}. "
                f"Choose: {', '.join(sorted(config.VALID_EFFORTS))}, default"
            )
            return
        info = await update_session(chat_id, effort=arg)
        await update.effective_message.reply_text(f"Effort: {info['effort']}")
    finally:
        await set_last_update_id(update.update_id)
