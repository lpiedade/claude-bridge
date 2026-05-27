"""/export — send the active session's transcript as a markdown file.

Reads the local JSONL (no CLI invocation), renders headed turn-by-turn
markdown, redacts via `utils.redact`, and uploads via `reply_document`.
"""
from __future__ import annotations

import asyncio
import io

from telegram import Update
from telegram.ext import ContextTypes

from core.logger import log
from integrations.claude_context_render import _model_display_name
from integrations.claude_history import Turn, parse_session_turns
from integrations.claude_usage import parse_session_usage
from repositories.session_repository import claim_update, session_for, set_last_update_id
from scripts.cost_alert import find_transcript
from utils.redact import redact

from ._common import authorized


def _render_markdown(sid: str, model: str | None, turns: list[Turn], usage) -> str:
    lines: list[str] = []
    lines.append(f"# Claude session `{sid}`")
    lines.append("")
    lines.append(f"- Model: **{_model_display_name(model or 'unknown')}** (`{model or 'unknown'}`)")
    lines.append(f"- Operator turns: {len(turns)} · CLI turns (incl. tool calls): {usage.turns}")
    lines.append(f"- Tokens: in {usage.input_tokens:,} · out {usage.output_tokens:,} · "
                 f"cache r/w {usage.cache_read_tokens:,} / {usage.cache_write_tokens:,}")
    lines.append(f"- Total cost: **${usage.total_cost_usd:.4f}**")
    if turns and turns[0].timestamp:
        lines.append(f"- First turn: {turns[0].timestamp.isoformat()}")
    if turns and turns[-1].timestamp:
        lines.append(f"- Last turn: {turns[-1].timestamp.isoformat()}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, turn in enumerate(turns, start=1):
        ts = turn.timestamp.isoformat() if turn.timestamp else "?"
        lines.append(f"## Turn {i} — {ts}")
        lines.append("")
        lines.append("**You:**")
        lines.append("")
        lines.append(redact(turn.user_text.strip()) or "_(empty)_")
        lines.append("")
        lines.append("**Claude:**")
        lines.append("")
        lines.append(redact(turn.assistant_text.strip()) or "_(no response captured)_")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
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

        try:
            turns = await asyncio.to_thread(parse_session_turns, transcript)
            usage = await asyncio.to_thread(parse_session_usage, transcript, sid)
        except Exception as exc:
            log.exception("export parse failed chat=%s", chat_id)
            await update.effective_message.reply_text(f"Failed to read transcript: {exc}")
            return

        if not turns:
            await update.effective_message.reply_text(
                f"Session {sid} has no turns yet."
            )
            return

        markdown = await asyncio.to_thread(_render_markdown, sid, usage.model, turns, usage)
        buf = io.BytesIO(markdown.encode("utf-8"))
        buf.name = f"{sid}.md"

        caption = (
            f"📝 Transcript — {usage.turns} turns, "
            f"${usage.total_cost_usd:.4f}"
        )
        await update.effective_message.reply_document(document=buf, caption=caption)
    finally:
        await set_last_update_id(update.update_id)
