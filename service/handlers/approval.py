"""Telegram callback handler for inline Approve/Reject buttons.

Approve  → re-run the original prompt with ``--allowedTools`` augmented to
           cover exactly the previously denied tool calls; post the new
           result.
Reject   → confirm in the chat and discard the parked prompt.
"""
from __future__ import annotations

import asyncio
import subprocess

from telegram import Update
from telegram.ext import ContextTypes

from core.config import TIMEOUT_SECONDS
from core.logger import conversation_log, log, permission_log
from integrations.claude_client import (
    extract_permission_denials,
    extract_result_text,
    run_claude,
)
from repositories.session_repository import session_for, update_session
from utils.redact import redact

from ._approvals import claim
from ._common import authorized

CALLBACK_PREFIX = "perm"


def make_callback_data(token: str, decision: str) -> str:
    return f"{CALLBACK_PREFIX}:{decision}:{token}"


def _truncate(s: str, limit: int = 4000) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"... [+{len(s) - limit} chars truncated]"


async def cmd_approval(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    query = update.callback_query
    if query is None:
        return

    data = query.data or ""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        await query.answer("Unknown action.")
        return
    decision, token = parts[1], parts[2]
    pending = claim(token)
    chat_id = update.effective_chat.id

    if pending is None:
        await query.answer("Request expired or already handled.")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            log.debug("edit_message_reply_markup failed", exc_info=True)
        return

    if pending.chat_id != chat_id:
        await query.answer("Not your request.")
        return

    if decision == "reject":
        permission_log.info("chat=%s REJECTED denials=%d", chat_id, len(pending.denials))
        await query.answer("Rejected.")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await update.effective_message.reply_text("❌ Rejected. No retry performed.")
        return

    if decision != "approve":
        await query.answer("Unknown decision.")
        return

    allowed = pending.allowed_tools()
    permission_log.info(
        "chat=%s APPROVED allowed_tools=%s", chat_id, allowed,
    )
    await query.answer("Approved — retrying...")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    info = await session_for(chat_id)
    await update.effective_message.reply_text(
        f"✅ Approved. Retrying with {len(allowed)} allowed tool(s)..."
    )

    try:
        result = await asyncio.to_thread(
            run_claude,
            pending.prompt,
            info["session_id"],
            info["cwd"],
            effort=info.get("effort"),
            model=info.get("model"),
            started=info.get("started", False),
            allowed_tools=allowed,
        )
    except subprocess.TimeoutExpired:
        log.warning("claude retry timeout chat=%s", chat_id)
        await update.effective_message.reply_text(f"Retry timed out after {TIMEOUT_SECONDS}s.")
        return

    if result.returncode != 0:
        err_full = (result.stderr or result.stdout or "").strip()
        log.error("claude retry rc=%s chat=%s stderr=%s",
                  result.returncode, chat_id, err_full[:5000])
        last = redact(err_full).splitlines()[-1] if err_full else "(no stderr)"
        await update.effective_message.reply_text(
            f"Retry exited rc={result.returncode}. Last line: {last[:500]}"
        )
        return

    await update_session(chat_id, started=True)

    denials = extract_permission_denials(result.stdout)
    if denials:
        permission_log.info(
            "chat=%s retry STILL denied tools=%s",
            chat_id,
            [d.get("tool_name") for d in denials],
        )
        await update.effective_message.reply_text(
            f"⚠️ Retry still hit {len(denials)} denial(s); aborting."
        )
        return

    text = extract_result_text(result.stdout).strip() or "(no output)"
    conversation_log.info(
        "chat=%s session=%s <<< RETRY_RESPONSE: %s",
        chat_id, info["session_id"], _truncate(redact(text)),
    )
    for i in range(0, len(text), 4000):
        await update.effective_message.reply_text(text[i:i + 4000])
