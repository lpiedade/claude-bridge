"""/usage — show token consumption and cost for the active Claude session.

Parses the session transcript at ``~/.claude/projects/<cwd>/<sid>.jsonl`` and
replies with a PNG line chart of cumulative cost + a textual caption.
"""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from core.logger import log
from integrations.claude_context_render import _model_display_name
from integrations.claude_usage import SessionUsage, parse_session_usage
from integrations.claude_usage_render import render_usage_png
from repositories.session_repository import claim_update, session_for, set_last_update_id
from scripts.cost_alert import find_transcript

from ._common import authorized


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _caption(usage: SessionUsage) -> str:
    model_label = _model_display_name(usage.model) if usage.model else "(no calls yet)"
    lines = [
        f"Session Usage — {model_label}",
        f"Turns: {usage.turns}",
        f"Tokens: in {_fmt_tokens(usage.input_tokens)} · out {_fmt_tokens(usage.output_tokens)} · "
        f"cache r/w {_fmt_tokens(usage.cache_read_tokens)}/{_fmt_tokens(usage.cache_write_tokens)}",
        f"Cost: ${usage.total_cost_usd:.4f}",
    ]
    return "\n".join(lines)


async def cmd_usage(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
        sid = info["session_id"]

        transcript = await asyncio.to_thread(find_transcript, sid)
        if transcript is None:
            await update.effective_message.reply_text(
                f"No transcript yet for session {sid}. Send a message first."
            )
            return

        await ctx.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
        try:
            usage = await asyncio.to_thread(parse_session_usage, transcript, sid)
        except Exception as exc:
            log.exception("parse_session_usage failed chat=%s", chat_id)
            await update.effective_message.reply_text(f"Failed to read transcript: {exc}")
            return

        if usage.turns == 0:
            await update.effective_message.reply_text(
                f"Session {sid} has no assistant turns yet."
            )
            return

        try:
            png = await asyncio.to_thread(render_usage_png, usage)
        except Exception:
            log.exception("render_usage_png failed chat=%s", chat_id)
            await update.effective_message.reply_text(_caption(usage))
            return

        await update.effective_message.reply_photo(photo=png, caption=_caption(usage))
    finally:
        await set_last_update_id(update.update_id)
