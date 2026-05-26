"""/context — show context-window usage for the active Claude session.

Invokes ``claude --resume <sid> -p "/context"`` (free; runs synthetically),
parses the output, and replies with a rendered PNG plus a textual caption.
"""
from __future__ import annotations

import asyncio
import subprocess

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from core.config import TIMEOUT_SECONDS
from core.logger import log
from integrations.claude_context import fetch_context
from integrations.claude_context_render import render_context_png, _model_display_name
from repositories.session_repository import (
    claim_update,
    session_for,
    set_last_update_id,
)

from ._common import authorized


def _caption(usage) -> str:
    pct = f"{usage.used_pct:.1f}%"
    return (
        f"Context Usage — {_model_display_name(usage.model)}\n"
        f"{_fmt(usage.used)}/{_fmt(usage.total)} tokens ({pct})"
    )


def _fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


async def cmd_context(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)

        if not info.get("started"):
            await update.effective_message.reply_text(
                f"Session not started yet (id={info['session_id']}).\n"
                f"Send a message first, then /context."
            )
            return

        await ctx.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)

        try:
            usage = await asyncio.to_thread(
                fetch_context,
                info["session_id"],
                info["cwd"],
                model=info.get("model"),
                timeout=min(TIMEOUT_SECONDS, 30),
            )
        except subprocess.TimeoutExpired:
            await update.effective_message.reply_text("Timed out querying /context.")
            return
        except Exception as exc:
            log.exception("fetch_context failed chat=%s", chat_id)
            await update.effective_message.reply_text(f"Failed to query /context: {exc}")
            return

        try:
            png = await asyncio.to_thread(render_context_png, usage)
        except Exception:
            log.exception("render_context_png failed chat=%s", chat_id)
            await update.effective_message.reply_text(_text_fallback(usage))
            return

        await update.effective_message.reply_photo(photo=png, caption=_caption(usage))
    finally:
        await set_last_update_id(update.update_id)


def _text_fallback(usage) -> str:
    lines = [
        f"Context Usage — {_model_display_name(usage.model)}",
        f"{_fmt(usage.used)}/{_fmt(usage.total)} tokens ({usage.used_pct:.1f}%)",
        "",
        "Estimated usage by category:",
    ]
    for cat in usage.categories:
        lines.append(f"  {cat.name}: {_fmt(cat.tokens)} ({cat.pct}%)")
    return "\n".join(lines)
