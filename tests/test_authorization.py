"""Tests for chat authorization and update deduplication."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fake_update(chat_id=None, update_id=1):
    chat = SimpleNamespace(id=chat_id) if chat_id is not None else None
    return SimpleNamespace(effective_chat=chat, update_id=update_id)


def test_authorized_accepts_whitelisted_chat(bot_module):
    assert bot_module.authorized(_fake_update(chat_id=111)) is True


def test_authorized_rejects_unknown_chat(bot_module):
    assert bot_module.authorized(_fake_update(chat_id=999)) is False


def test_authorized_rejects_missing_chat(bot_module):
    # effective_chat may be None for some edge updates.
    assert not bot_module.authorized(_fake_update(chat_id=None))


def test_claim_update_accepts_fresh(bot_module):
    upd = _fake_update(chat_id=111, update_id=50)
    assert _run(bot_module._claim_update(upd)) is True


def test_claim_update_rejects_replay(bot_module):
    _run(bot_module.set_last_update_id(100))
    upd = _fake_update(chat_id=111, update_id=50)
    assert _run(bot_module._claim_update(upd)) is False


def test_claim_update_rejects_equal_id(bot_module):
    _run(bot_module.set_last_update_id(75))
    upd = _fake_update(chat_id=111, update_id=75)
    assert _run(bot_module._claim_update(upd)) is False
