"""Tests for the per-session model selection."""
from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_parse_model_accepts_valid_values(bot_module):
    for v in ("opus", "sonnet", "haiku"):
        assert bot_module._parse_model(v) == v


def test_parse_model_normalizes_case_and_whitespace(bot_module):
    assert bot_module._parse_model("  HAIKU  ") == "haiku"


def test_parse_model_rejects_invalid(bot_module):
    assert bot_module._parse_model("turbo") is None
    assert bot_module._parse_model("") is None
    assert bot_module._parse_model(None) is None


def test_default_model_is_haiku(bot_module):
    assert bot_module.DEFAULT_MODEL == "haiku"


def test_new_session_inherits_default_model(bot_module, monkeypatch):
    import repositories.session_repository as repo
    monkeypatch.setattr(bot_module, "DEFAULT_MODEL", "sonnet")
    monkeypatch.setattr(repo, "DEFAULT_MODEL", "sonnet")
    info = _run(bot_module.session_for(3001))
    assert info["model"] == "sonnet"


def test_new_session_model_defaults_to_haiku(bot_module):
    info = _run(bot_module.session_for(3002))
    assert info["model"] == "haiku"


def test_update_session_sets_model(bot_module):
    _run(bot_module.session_for(3003))
    updated = _run(bot_module.update_session(3003, model="opus"))
    assert updated["model"] == "opus"
    fresh = _run(bot_module.session_for(3003))
    assert fresh["model"] == "opus"


def test_valid_models_constant(bot_module):
    assert bot_module.VALID_MODELS == {"opus", "sonnet", "haiku"}
