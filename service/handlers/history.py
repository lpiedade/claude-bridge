"""/history — show the last N turns of the active session.

Reads the local transcript at ``~/.claude/projects/<encoded-cwd>/<sid>.jsonl``
(no Claude CLI invocation, no token spend). Each turn rendered as a compact
block: relative timestamp, truncated user prompt, truncated assistant reply.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from telegram import Update
from telegram.ext import ContextTypes

from core.logger import log
from integrations.claude_history import Turn, parse_session_turns
from repositories.session_repository import claim_update, session_for, set_last_update_id
from scripts.cost_alert import find_transcript
from utils.redact import redact

from ._common import authorized

DEFAULT_N = 10
MAX_N = 50
SNIPPET_CHARS = 500
TELEGRAM_MAX = 4000


def _parse_n(args: list[str]) -> tuple[int, str | None]:
    if not args:
        return DEFAULT_N, None
    try:
        n = int(args[0])
    except ValueError:
        return DEFAULT_N, f"Argument must be an integer, got {args[0]!r}."
    if n < 1 or n > MAX_N:
        return DEFAULT_N, f"N must be between 1 and {MAX_N} (got {n})."
    return n, None


def _ago(ts: datetime | None) -> str:
    if ts is None:
        return "?"
    now = datetime.now(UTC)
    delta = now - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}min ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _truncate(text: str, limit: int = SNIPPET_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"… [+{len(text) - limit} chars]"


def _format_turn(turn: Turn, idx: int) -> str:
    user = _truncate(redact(turn.user_text))
    assistant = _truncate(redact(turn.assistant_text)) or "_(no response captured)_"
    header = f"#{idx} · ⏱ {_ago(turn.timestamp)}"
    return f"{header}\n👤 {user}\n🤖 {assistant}"


def _chunk(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    return [text[i:i + limit] for i in range(0, len(text), limit)] or [text]


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
        sid = info["session_id"]

        n, err = _parse_n(ctx.args or [])
        if err:
            await update.effective_message.reply_text(err)
            return

        transcript = await asyncio.to_thread(find_transcript, sid)
        if transcript is None:
            await update.effective_message.reply_text(
                f"No transcript yet for session {sid}. Send a message first."
            )
            return

        try:
            turns = await asyncio.to_thread(parse_session_turns, transcript)
        except Exception as exc:
            log.exception("parse_session_turns failed chat=%s", chat_id)
            await update.effective_message.reply_text(f"Failed to read transcript: {exc}")
            return

        if not turns:
            await update.effective_message.reply_text(
                f"Session {sid} has no turns yet."
            )
            return

        recent = turns[-n:]
        total = len(turns)
        header = f"📜 Last {len(recent)} of {total} turns — session {sid[:8]}…\n\n"
        body = "\n\n".join(_format_turn(t, total - len(recent) + i + 1) for i, t in enumerate(recent))
        full = header + body

        for chunk in _chunk(full):
            await update.effective_message.reply_text(chunk)
    finally:
        await set_last_update_id(update.update_id)
