"""Pytest configuration: set env vars and isolate HOME before importing bot."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Allowlist roots must exist on disk, so build them in a session-scoped tempdir
# and inject the env vars BEFORE bot.py is first imported.
_TMP_HOME = Path(tempfile.mkdtemp(prefix="claude-bridge-tests-"))
_ALLOWED_A = _TMP_HOME / "allowed_a"
_ALLOWED_B = _TMP_HOME / "allowed_b"
_ALLOWED_A.mkdir()
_ALLOWED_B.mkdir()

os.environ["HOME"] = str(_TMP_HOME)
os.environ["CLAUDE_BRIDGE_TG_TOKEN"] = "test-token"
os.environ["CLAUDE_BRIDGE_ALLOWED_CHATS"] = "111,222"
os.environ["CLAUDE_BRIDGE_CWD"] = str(_ALLOWED_A)
os.environ["CLAUDE_BRIDGE_CWD_ROOTS"] = f"{_ALLOWED_A},{_ALLOWED_B}"
os.environ["CLAUDE_BRIDGE_PERMISSION_MODE"] = "bypassPermissions"
os.environ["CLAUDE_BRIDGE_TIMEOUT"] = "5"

# Ensure repo root is on sys.path so `import bot` works.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def tmp_home() -> Path:
    return _TMP_HOME


@pytest.fixture(scope="session")
def allowed_dirs() -> tuple[Path, Path]:
    return _ALLOWED_A, _ALLOWED_B


@pytest.fixture
def bot_module(tmp_path, monkeypatch):
    """Import bot fresh per test and point STATE_FILE into tmp_path."""
    if "bot" in sys.modules:
        del sys.modules["bot"]
    bot = importlib.import_module("bot")
    state_file = tmp_path / "state.json"
    # STATE_FILE lives in repositories.session_repository now; patch both
    # so tests that read bot_module.STATE_FILE see the redirected path.
    repo = importlib.import_module("repositories.session_repository")
    monkeypatch.setattr(repo, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    return bot
