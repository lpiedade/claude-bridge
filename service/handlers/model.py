"""/model — show or set the per-session model."""
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


async def cmd_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
        if not ctx.args:
            await update.effective_message.reply_text(
                f"Model: {info.get('model') or '(default)'}\n"
                f"Default: {config.DEFAULT_MODEL}\n"
                f"Usage: /model <{'|'.join(sorted(config.VALID_MODELS))}|default>"
            )
            return
        arg = ctx.args[0].strip().lower()
        if arg == "default":
            info = await update_session(chat_id, model=config.DEFAULT_MODEL)
            await update.effective_message.reply_text(f"Model: {info['model']} (default)")
            return
        if arg not in config.VALID_MODELS:
            await update.effective_message.reply_text(
                f"Invalid model: {arg}. "
                f"Choose: {', '.join(sorted(config.VALID_MODELS))}, default"
            )
            return
        info = await update_session(chat_id, model=arg)
        await update.effective_message.reply_text(f"Model: {info['model']}")
    finally:
        await set_last_update_id(update.update_id)
