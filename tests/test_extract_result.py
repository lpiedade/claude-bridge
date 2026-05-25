"""Tests for extract_result_text — parses `claude --output-format json` stdout."""
from __future__ import annotations

import json


def test_dict_payload_returns_result_field(bot_module):
    stdout = json.dumps({"result": "hello", "session_id": "abc"})
    assert bot_module.extract_result_text(stdout) == "hello"


def test_dict_payload_without_result_returns_raw_stdout(bot_module):
    stdout = json.dumps({"session_id": "abc"})
    assert bot_module.extract_result_text(stdout) == stdout


def test_list_payload_picks_last_item_with_result(bot_module):
    stdout = json.dumps([
        {"type": "system", "session_id": "abc"},
        {"type": "assistant", "result": "first"},
        {"type": "assistant", "result": "final"},
    ])
    assert bot_module.extract_result_text(stdout) == "final"


def test_list_payload_without_result_returns_raw_stdout(bot_module):
    stdout = json.dumps([{"type": "system"}, {"type": "tool_use"}])
    assert bot_module.extract_result_text(stdout) == stdout


def test_list_payload_with_non_dict_items_returns_raw_stdout(bot_module):
    stdout = json.dumps(["a", "b", 1])
    assert bot_module.extract_result_text(stdout) == stdout


def test_invalid_json_returns_raw_stdout(bot_module):
    stdout = "not json at all"
    assert bot_module.extract_result_text(stdout) == stdout


def test_empty_stdout_returns_empty(bot_module):
    assert bot_module.extract_result_text("") == ""
