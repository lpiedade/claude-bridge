"""Tests for the on_message keepalive task (TYPING refresh + slow-response notice)."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import service.handlers.message as msg_module


@pytest.mark.asyncio
async def test_keepalive_refreshes_typing_before_threshold(monkeypatch):
    """Before SLOW_RESPONSE_NOTICE_SECONDS elapse, only TYPING is sent — no notice."""
    monkeypatch.setattr(msg_module, "SLOW_RESPONSE_NOTICE_SECONDS", 9999)

    ctx = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()
    reply_to = MagicMock()
    reply_to.reply_text = AsyncMock()

    task = asyncio.create_task(msg_module._keepalive(ctx, reply_to, 1, time.monotonic()))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert ctx.bot.send_chat_action.await_count >= 1
    assert reply_to.reply_text.await_count == 0


@pytest.mark.asyncio
async def test_keepalive_posts_notice_when_threshold_already_passed(monkeypatch):
    """If the task starts already past the threshold, the notice is sent immediately."""
    monkeypatch.setattr(msg_module, "SLOW_RESPONSE_NOTICE_SECONDS", 0)
    monkeypatch.setattr(msg_module, "SLOW_RESPONSE_UPDATE_INTERVAL_SECONDS", 60)

    ctx = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()
    notice = MagicMock()
    notice.edit_text = AsyncMock()
    notice.delete = AsyncMock()
    reply_to = MagicMock()
    reply_to.reply_text = AsyncMock(return_value=notice)

    task = asyncio.create_task(msg_module._keepalive(ctx, reply_to, 1, time.monotonic()))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert reply_to.reply_text.await_count == 1
    notice.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_keepalive_swallows_telegram_errors(monkeypatch):
    """A TelegramError on send_chat_action must not kill the keepalive loop."""
    from telegram.error import TelegramError

    monkeypatch.setattr(msg_module, "SLOW_RESPONSE_NOTICE_SECONDS", 9999)

    ctx = MagicMock()
    ctx.bot.send_chat_action = AsyncMock(side_effect=TelegramError("boom"))
    reply_to = MagicMock()
    reply_to.reply_text = AsyncMock()

    task = asyncio.create_task(msg_module._keepalive(ctx, reply_to, 1, time.monotonic()))
    await asyncio.sleep(0.05)
    assert not task.done()  # still running despite the errors
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
