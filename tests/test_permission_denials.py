"""Tests for extract_permission_denials and the message handler's denial flow.

These cover both the pure parser in `claude_client` and the post-Stage-3
denial UI: when denials surface, the handler attaches an inline keyboard
with Approve/Reject buttons and returns early (the original result text
is *not* posted — Approve & retry will produce one).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_no_denials_returns_empty_list(bot_module):
    stdout = json.dumps({"result": "hi"})
    assert bot_module.extract_permission_denials(stdout) == []


def test_extracts_denials_from_dict_payload(bot_module):
    stdout = json.dumps({
        "result": "blocked",
        "permission_denials": [
            {"tool_name": "Write", "tool_use_id": "t1",
             "tool_input": {"file_path": "/etc/passwd", "content": "x"}},
        ],
    })
    denials = bot_module.extract_permission_denials(stdout)
    assert len(denials) == 1
    assert denials[0]["tool_name"] == "Write"


def test_extracts_denials_from_list_payload(bot_module):
    stdout = json.dumps([
        {"type": "system"},
        {"type": "result", "permission_denials": [
            {"tool_name": "Bash", "tool_use_id": "t2",
             "tool_input": {"command": "rm -rf /"}},
        ]},
    ])
    denials = bot_module.extract_permission_denials(stdout)
    assert len(denials) == 1
    assert denials[0]["tool_name"] == "Bash"


def test_invalid_json_returns_empty(bot_module):
    assert bot_module.extract_permission_denials("not json") == []


def test_malformed_denials_filtered(bot_module):
    stdout = json.dumps({"permission_denials": ["bad", {"tool_name": "ok"}, 42]})
    denials = bot_module.extract_permission_denials(stdout)
    assert denials == [{"tool_name": "ok"}]


def test_empty_stdout_returns_empty(bot_module):
    assert bot_module.extract_permission_denials("") == []


def _make_update(text: str, chat_id: int = 111, update_id: int = 7) -> tuple[MagicMock, AsyncMock]:
    """Build an Update mock where effective_message routes to a single reply mock."""
    reply = AsyncMock()
    msg = MagicMock()
    msg.text = text
    msg.reply_text = reply
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.update_id = update_id
    update.effective_message = msg
    return update, reply


def _patch_handler_deps(monkeypatch, message_mod, sid: str) -> None:
    monkeypatch.setattr(
        message_mod, "claim_update", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        message_mod, "session_for",
        AsyncMock(return_value={
            "cwd": "/tmp", "session_id": sid, "started": False,
            "effort": None, "model": None,
        }),
    )
    monkeypatch.setattr(message_mod, "update_session", AsyncMock())
    monkeypatch.setattr(message_mod, "set_last_update_id", AsyncMock())
    monkeypatch.setattr(message_mod, "authorized", lambda u: True)


@pytest.mark.asyncio
async def test_handler_posts_denial_notice(bot_module, monkeypatch):
    """A denied tool call must produce a Telegram message with Approve/Reject buttons,
    and the handler must return early without posting the original result text."""
    from service.handlers import message as message_mod

    stdout = json.dumps({
        "result": "I couldn't write the file.",
        "permission_denials": [
            {"tool_name": "Write", "tool_use_id": "t1",
             "tool_input": {"file_path": "/tmp/x.txt", "content": "hi"}},
        ],
    })
    fake_proc = MagicMock(returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(message_mod, "run_claude", lambda *a, **kw: fake_proc)
    _patch_handler_deps(monkeypatch, message_mod, "sid-1")

    update, reply = _make_update("please write a file")
    ctx = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()

    await message_mod.on_message(update, ctx)

    bodies = [c.args[0] for c in reply.call_args_list]
    assert any("pediu permissão" in b for b in bodies)
    assert any("Write" in b and "/tmp/x.txt" in b for b in bodies)
    # Original result text is NOT delivered post-Stage 3 — Approve & retry
    # owns the retry path. Asserting the *absence* of leak.
    assert not any("couldn't write" in b for b in bodies)

    # The final reply (the one carrying the denial body) must have an
    # InlineKeyboardMarkup attached with two callback_data tokens — one
    # approve, one reject.
    last_call = reply.call_args_list[-1]
    markup = last_call.kwargs.get("reply_markup")
    assert markup is not None, "denial notice missing reply_markup"
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert any(":approve:" in cb for cb in callbacks)
    assert any(":reject:" in cb for cb in callbacks)


@pytest.mark.asyncio
async def test_handler_no_denial_notice_when_clean(bot_module, monkeypatch):
    """When permission_denials is empty, the normal result text reaches the chat
    and no inline keyboard is attached."""
    from service.handlers import message as message_mod

    stdout = json.dumps({"result": "all good", "permission_denials": []})
    fake_proc = MagicMock(returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(message_mod, "run_claude", lambda *a, **kw: fake_proc)
    _patch_handler_deps(monkeypatch, message_mod, "sid-2")

    update, reply = _make_update("hi", update_id=8)
    ctx = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()

    await message_mod.on_message(update, ctx)

    bodies = [c.args[0] for c in reply.call_args_list]
    assert not any("pediu permissão" in b for b in bodies)
    assert any("all good" in b for b in bodies)
    # No keyboard on any of the calls.
    assert all(c.kwargs.get("reply_markup") is None for c in reply.call_args_list)
