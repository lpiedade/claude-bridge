"""Tests for extract_permission_denials and the message handler's denial notice."""
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


@pytest.mark.asyncio
async def test_handler_posts_denial_notice(bot_module, monkeypatch):
    """Handler must send a Telegram message summarizing denied tool calls."""
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
    monkeypatch.setattr(
        message_mod, "claim_update", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        message_mod, "session_for",
        AsyncMock(return_value={
            "cwd": "/tmp", "session_id": "sid-1", "started": False,
            "effort": None, "model": None,
        }),
    )
    monkeypatch.setattr(message_mod, "update_session", AsyncMock())
    monkeypatch.setattr(message_mod, "set_last_update_id", AsyncMock())
    monkeypatch.setattr(message_mod, "authorized", lambda u: True)

    reply = AsyncMock()
    chat_action = AsyncMock()
    update = MagicMock()
    update.effective_chat.id = 111
    update.update_id = 7
    update.message.text = "please write a file"
    update.message.reply_text = reply
    ctx = MagicMock()
    ctx.bot.send_chat_action = chat_action

    await message_mod.on_message(update, ctx)

    bodies = [c.args[0] for c in reply.call_args_list]
    assert any("pediu permissão" in b for b in bodies)
    assert any("Write" in b and "/tmp/x.txt" in b for b in bodies)
    # Original result text still delivered.
    assert any("couldn't write" in b for b in bodies)


@pytest.mark.asyncio
async def test_handler_no_denial_notice_when_clean(bot_module, monkeypatch):
    from service.handlers import message as message_mod

    stdout = json.dumps({"result": "all good", "permission_denials": []})
    fake_proc = MagicMock(returncode=0, stdout=stdout, stderr="")
    monkeypatch.setattr(message_mod, "run_claude", lambda *a, **kw: fake_proc)
    monkeypatch.setattr(
        message_mod, "claim_update", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        message_mod, "session_for",
        AsyncMock(return_value={
            "cwd": "/tmp", "session_id": "sid-2", "started": False,
            "effort": None, "model": None,
        }),
    )
    monkeypatch.setattr(message_mod, "update_session", AsyncMock())
    monkeypatch.setattr(message_mod, "set_last_update_id", AsyncMock())
    monkeypatch.setattr(message_mod, "authorized", lambda u: True)

    reply = AsyncMock()
    update = MagicMock()
    update.effective_chat.id = 111
    update.update_id = 8
    update.message.text = "hi"
    update.message.reply_text = reply
    ctx = MagicMock()
    ctx.bot.send_chat_action = AsyncMock()

    await message_mod.on_message(update, ctx)

    bodies = [c.args[0] for c in reply.call_args_list]
    assert not any("pediu permissão" in b for b in bodies)
    assert any("all good" in b for b in bodies)
