"""Tests for the pending-approval store and the --allowedTools spec builder."""
from __future__ import annotations

import time

import pytest

from integrations.claude_client import build_command
from service.handlers import _approvals
from service.handlers._approvals import (
    PendingApproval,
    allowed_tool_spec,
    claim,
    register,
)


def setup_function():
    _approvals._pending.clear()


def test_allowed_tool_spec_quotes_bash_command():
    denial = {"tool_name": "Bash", "tool_input": {"command": "rm /tmp/x y"}}
    spec = allowed_tool_spec(denial)
    assert spec == "Bash('rm /tmp/x y')"


def test_allowed_tool_spec_leaves_safe_bash_unquoted():
    denial = {"tool_name": "Bash", "tool_input": {"command": "echo hi"}}
    # The quoter only adds quotes when special chars are present; "echo hi"
    # contains a space → quoted.
    assert allowed_tool_spec(denial) == "Bash('echo hi')"
    denial2 = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert allowed_tool_spec(denial2) == "Bash(ls)"


def test_allowed_tool_spec_for_non_bash_tool():
    assert allowed_tool_spec({"tool_name": "Edit", "tool_input": {}}) == "Edit"


def test_pending_dedupes_repeat_tool_specs():
    p = PendingApproval(
        chat_id=1, prompt="p",
        denials=[
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            {"tool_name": "Edit", "tool_input": {}},
        ],
    )
    assert p.allowed_tools() == ["Bash(ls)", "Edit"]


def test_register_claim_roundtrip():
    token = register(42, "the prompt", [{"tool_name": "Edit", "tool_input": {}}])
    entry = claim(token)
    assert entry is not None
    assert entry.chat_id == 42
    assert entry.prompt == "the prompt"
    # claim is single-use
    assert claim(token) is None


def test_claim_returns_none_on_expiry(monkeypatch):
    token = register(42, "p", [])
    entry = _approvals._pending[token]
    entry.created_at = time.monotonic() - (_approvals.APPROVAL_TTL_SECONDS + 1)
    assert claim(token) is None
    # GC dropped it from the store too
    assert token not in _approvals._pending


def test_build_command_injects_allowed_tools_before_session_id():
    cmd = build_command(
        "p", "11111111-2222-3333-4444-555555555555",
        effort=None, model=None, started=False,
        allowed_tools=["Bash(ls)", "Edit"],
    )
    assert "--allowedTools" in cmd
    idx = cmd.index("--allowedTools")
    # Two tool tokens follow the flag, in order.
    assert cmd[idx + 1] == "Bash(ls)"
    assert cmd[idx + 2] == "Edit"


def test_build_command_omits_allowed_tools_when_empty():
    cmd = build_command(
        "p", "11111111-2222-3333-4444-555555555555",
        effort=None, model=None, started=False, allowed_tools=None,
    )
    assert "--allowedTools" not in cmd
