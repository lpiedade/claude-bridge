"""Tests for the log/error redaction helper."""
from __future__ import annotations

import os


def test_redact_home_path(bot_module):
    home = os.path.expanduser("~")
    s = f"error opening {home}/secrets/key"
    out = bot_module._redact(s)
    assert home not in out
    assert "~" in out


def test_redact_users_path(bot_module):
    s = "stack: /Users/alice/project/file.py:42"
    out = bot_module._redact(s)
    assert "/Users/alice" not in out
    assert "/Users/<user>" in out


def test_redact_email(bot_module):
    s = "contact: foo.bar+spam@example.co.uk for help"
    out = bot_module._redact(s)
    assert "foo.bar+spam@example.co.uk" not in out
    assert "<email>" in out


def test_redact_long_hex(bot_module):
    h = "a" * 40
    s = f"hash={h} end"
    out = bot_module._redact(s)
    assert h not in out
    assert "<hex>" in out


def test_redact_api_key(bot_module):
    s = "Authorization: Bearer sk-AbCdEf0123456789xyz_-token"
    out = bot_module._redact(s)
    assert "sk-AbCdEf0123456789xyz_-token" not in out
    assert "<api-key>" in out


def test_redact_idempotent_on_clean_text(bot_module):
    s = "nothing sensitive here"
    assert bot_module._redact(s) == s
