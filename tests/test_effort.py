"""Tests for the per-session effort level."""
from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_parse_effort_accepts_valid_values(bot_module):
    for v in ("low", "medium", "high", "xhigh", "max"):
        assert bot_module._parse_effort(v) == v


def test_parse_effort_normalizes_case_and_whitespace(bot_module):
    assert bot_module._parse_effort("  HIGH  ") == "high"


def test_parse_effort_rejects_invalid(bot_module):
    assert bot_module._parse_effort("turbo") is None
    assert bot_module._parse_effort("") is None
    assert bot_module._parse_effort(None) is None


def test_new_session_inherits_default_effort(bot_module, monkeypatch):
    monkeypatch.setattr(bot_module, "DEFAULT_EFFORT", "medium")
    info = _run(bot_module.session_for(1001))
    assert info["effort"] == "medium"


def test_new_session_effort_is_none_without_default(bot_module, monkeypatch):
    monkeypatch.setattr(bot_module, "DEFAULT_EFFORT", None)
    info = _run(bot_module.session_for(1002))
    assert info["effort"] is None


def test_update_session_sets_effort(bot_module):
    _run(bot_module.session_for(2001))
    updated = _run(bot_module.update_session(2001, effort="high"))
    assert updated["effort"] == "high"
    fresh = _run(bot_module.session_for(2001))
    assert fresh["effort"] == "high"


def test_update_session_clears_effort(bot_module):
    _run(bot_module.update_session(2002, effort="max"))
    cleared = _run(bot_module.update_session(2002, effort=None))
    assert cleared["effort"] is None


def test_valid_efforts_constant(bot_module):
    assert bot_module.VALID_EFFORTS == {"low", "medium", "high", "xhigh", "max"}
