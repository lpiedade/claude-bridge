"""Free-form Telegram messages → forwarded to the claude CLI."""
from __future__ import annotations

import asyncio
import json
import subprocess
import time

from telegram import Message, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from core.config import (
    SLOW_RESPONSE_NOTICE_SECONDS,
    SLOW_RESPONSE_UPDATE_INTERVAL_SECONDS,
    TIMEOUT_SECONDS,
)
from core.logger import conversation_log, log, permission_log
from integrations.claude_client import (
    extract_permission_denials,
    extract_result_text,
    run_claude,
)
from repositories.session_repository import (
    claim_update,
    session_for,
    set_last_update_id,
    update_session,
)
from utils.redact import redact

from ._common import authorized

_MAX_LOG = 4000


def _truncate(s: str, limit: int = _MAX_LOG) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"... [+{len(s) - limit} chars truncated]"


_INPUT_PREVIEW = 300


async def _keepalive(
    ctx: ContextTypes.DEFAULT_TYPE,
    reply_to: Message,
    chat_id: int,
    started: float,
) -> None:
    """Refresh the TYPING indicator and, past a threshold, post a 'still thinking'
    notice that grows with elapsed seconds. Designed to be cancelled when the
    underlying CLI call returns; absorbs Telegram errors silently."""
    notice: Message | None = None
    notice_text = ""
    typing_interval = 4.0  # Telegram TYPING expires after ~5s
    next_update = time.monotonic() + typing_interval
    try:
        while True:
            try:
                await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
            except TelegramError:
                log.debug("keepalive: send_chat_action failed", exc_info=True)
            elapsed = time.monotonic() - started
            if elapsed >= SLOW_RESPONSE_NOTICE_SECONDS:
                text = f"🤔 Still thinking ({int(elapsed)}s elapsed)..."
                try:
                    if notice is None:
                        notice = await reply_to.reply_text(text)
                    elif text != notice_text:
                        await notice.edit_text(text)
                    notice_text = text
                except TelegramError:
                    log.debug("keepalive: notice update failed", exc_info=True)
            sleep_for = min(typing_interval, SLOW_RESPONSE_UPDATE_INTERVAL_SECONDS)
            next_update += sleep_for
            await asyncio.sleep(max(0.5, next_update - time.monotonic()))
    except asyncio.CancelledError:
        if notice is not None:
            try:
                await notice.delete()
            except TelegramError:
                pass
        raise


def _format_denial(denial: dict) -> str:
    tool = denial.get("tool_name", "?")
    raw = denial.get("tool_input", {})
    try:
        body = json.dumps(raw, ensure_ascii=False)
    except (TypeError, ValueError):
        body = str(raw)
    body = redact(body)
    if len(body) > _INPUT_PREVIEW:
        body = body[:_INPUT_PREVIEW] + "…"
    return f"• {tool}: {body}"


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        await update.effective_message.reply_text("Unauthorized.")
        return
    if not await claim_update(update):
        return
    chat_id = update.effective_chat.id
    try:
        info = await session_for(chat_id)
        prompt = update.effective_message.text or ""
        if not prompt.strip():
            return

        await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)

        log.info(
            "chat=%s cwd=%s session=%s started=%s",
            chat_id, info["cwd"], info["session_id"], info["started"],
        )
        conversation_log.info(
            "chat=%s session=%s >>> PROMPT: %s",
            chat_id,
            info["session_id"],
            _truncate(redact(prompt)),
        )

        keepalive_task = asyncio.create_task(_keepalive(
            ctx, update.effective_message, chat_id, time.monotonic(),
        ))
        try:
            result = await asyncio.to_thread(
                run_claude,
                prompt,
                info["session_id"],
                info["cwd"],
                effort=info.get("effort"),
                model=info.get("model"),
                started=info.get("started", False),
            )
        except subprocess.TimeoutExpired:
            log.warning("claude timeout chat=%s session=%s", chat_id, info["session_id"])
            await update.effective_message.reply_text(f"Timed out after {TIMEOUT_SECONDS}s.")
            return
        finally:
            keepalive_task.cancel()
            try:
                await keepalive_task
            except asyncio.CancelledError:
                pass

        if result.returncode != 0:
            err_full = (result.stderr or result.stdout or "").strip()
            log.error(
                "claude rc=%s chat=%s stderr=%s",
                result.returncode, chat_id, err_full[:5000],
            )
            err_safe = redact(err_full)
            last_line = err_safe.splitlines()[-1] if err_safe else "(no stderr)"
            await update.effective_message.reply_text(
                f"Claude exited with rc={result.returncode}.\n"
                f"Last line: {last_line[:500]}\n"
                f"Full stderr in bridge.log"
            )
            return

        await update_session(chat_id, started=True)

        denials = extract_permission_denials(result.stdout)
        if denials:
            for d in denials:
                permission_log.info(
                    "chat=%s session=%s DENIED tool=%s input=%s",
                    chat_id,
                    info["session_id"],
                    d.get("tool_name", "?"),
                    _truncate(redact(json.dumps(d.get("tool_input", {}), ensure_ascii=False))),
                )
            header = (
                f"⚠️ Claude pediu permissão para {len(denials)} ação(ões) "
                f"e foi bloqueado (modo: acceptEdits):"
            )
            body = "\n".join(_format_denial(d) for d in denials)
            notice = f"{header}\n{body}"
            for i in range(0, len(notice), 4000):
                await update.effective_message.reply_text(notice[i:i + 4000])

        text = extract_result_text(result.stdout)
        text = text.strip() or "(no output)"
        conversation_log.info(
            "chat=%s session=%s <<< RESPONSE: %s",
            chat_id,
            info["session_id"],
            _truncate(redact(text)),
        )
        for i in range(0, len(text), 4000):
            await update.effective_message.reply_text(text[i:i + 4000])
    except Exception:
        log.exception("unhandled error in on_message chat=%s", chat_id)
        try:
            await update.effective_message.reply_text("Internal error — check bridge.log.")
        except Exception:
            log.exception("failed to send error notice to chat=%s", chat_id)
    finally:
        await set_last_update_id(update.update_id)
