"""Tests for state persistence and session management."""
from __future__ import annotations

import asyncio
import json
import os

import pytest


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_load_state_returns_empty_when_missing(bot_module):
    assert bot_module.load_state() == {}


def test_save_then_load_roundtrip(bot_module):
    bot_module.save_state({"123": {"session_id": "s1", "cwd": "/x", "started": True}})
    assert bot_module.load_state() == {
        "123": {"session_id": "s1", "cwd": "/x", "started": True}
    }


def test_save_state_writes_0600(bot_module):
    bot_module.save_state({"k": "v"})
    mode = bot_module.STATE_FILE.stat().st_mode & 0o777
    assert mode == 0o600


def test_save_state_is_atomic_replace(bot_module):
    bot_module.save_state({"a": 1})
    # No leftover .tmp file after a successful write.
    assert not bot_module.STATE_FILE.with_suffix(".json.tmp").exists()


def test_load_state_handles_corrupt_file(bot_module):
    bot_module.STATE_FILE.write_text("{ not json")
    assert bot_module.load_state() == {}
    # Corrupt file is preserved alongside for forensics.
    assert bot_module.STATE_FILE.with_suffix(".json.corrupt").exists()
    assert not bot_module.STATE_FILE.exists()


def test_session_for_creates_entry_on_first_access(bot_module):
    info = _run(bot_module.session_for(999))
    assert info["session_id"]
    assert info["cwd"] == bot_module.DEFAULT_CWD
    assert info["started"] is False
    # Persisted to disk.
    on_disk = json.loads(bot_module.STATE_FILE.read_text())
    assert "999" in on_disk


def test_session_for_returns_same_entry_on_repeat(bot_module):
    a = _run(bot_module.session_for(42))
    b = _run(bot_module.session_for(42))
    assert a == b


def test_update_session_persists_changes(bot_module):
    _run(bot_module.session_for(7))
    updated = _run(bot_module.update_session(7, started=True, cwd="/new"))
    assert updated["started"] is True
    assert updated["cwd"] == "/new"
    fresh = _run(bot_module.session_for(7))
    assert fresh["started"] is True
    assert fresh["cwd"] == "/new"


def test_update_session_creates_missing_entry(bot_module):
    updated = _run(bot_module.update_session(555, started=True))
    assert updated["session_id"]
    assert updated["started"] is True


def test_reset_session_changes_session_id_and_clears_started(bot_module):
    first = _run(bot_module.session_for(1))
    _run(bot_module.update_session(1, started=True))
    reset = _run(bot_module.reset_session(1))
    assert reset["session_id"] != first["session_id"]
    assert reset["started"] is False


def test_last_update_id_monotonic(bot_module):
    assert _run(bot_module.get_last_update_id()) == 0
    _run(bot_module.set_last_update_id(10))
    _run(bot_module.set_last_update_id(5))  # older — must not regress
    assert _run(bot_module.get_last_update_id()) == 10
    _run(bot_module.set_last_update_id(20))
    assert _run(bot_module.get_last_update_id()) == 20
