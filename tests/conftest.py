"""Pytest configuration: set env vars and isolate HOME before importing app modules."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

# Allowlist roots must exist on disk, so build them in a session-scoped tempdir
# and inject the env vars BEFORE any app module is first imported.
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

# Ensure repo root is on sys.path so packages resolve.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def tmp_home() -> Path:
    return _TMP_HOME


@pytest.fixture(scope="session")
def allowed_dirs() -> tuple[Path, Path]:
    return _ALLOWED_A, _ALLOWED_B


_AGGREGATE_MODULES = [
    # _common first so its 1-arg is_cwd_allowed wrapper wins over utils.paths.
    "service.handlers._common",
    "core.config",
    "core.logger",
    "utils.paths",
    "utils.redact",
    "integrations.claude_client",
    "repositories.session_repository",
]


@pytest.fixture
def bot_module(tmp_path, monkeypatch):
    """Aggregate view over the app modules, redirected to an isolated STATE_FILE.

    Historical tests reference `bot_module.<symbol>` for symbols that now live
    across `core/`, `utils/`, `repositories/`, `integrations/`, and
    `service.handlers`. This fixture exposes them on a single namespace so
    tests don't need to know the new layout.

    `bot_module` is also writable: monkeypatching an attribute also mirrors
    the change onto every underlying module that exposes that name, so
    e.g. patching `DEFAULT_EFFORT` reaches the repository helpers.
    """
    # Fresh imports so module-level state (locks, paths) is re-initialized.
    for name in list(_AGGREGATE_MODULES):
        sys.modules.pop(name, None)
    sys.modules.pop("bot", None)

    modules = [importlib.import_module(name) for name in _AGGREGATE_MODULES]

    repo = importlib.import_module("repositories.session_repository")
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(repo, "STATE_FILE", state_file)

    aliases = {
        "_resolve_arg": ("utils.paths", "resolve_arg"),
        "_safe_resolve": ("utils.paths", "safe_resolve"),
        "_redact": ("utils.redact", "redact"),
        "_claim_update": ("repositories.session_repository", "claim_update"),
        "_parse_effort": ("core.config", "parse_effort"),
        "_parse_model": ("core.config", "parse_model"),
    }

    class Aggregate(SimpleNamespace):
        """Read across modules; write mirrors to every module that has the name."""

        def __getattribute__(self, name):
            if name.startswith("_") and name in object.__getattribute__(self, "__dict__"):
                return object.__getattribute__(self, name)
            try:
                return object.__getattribute__(self, name)
            except AttributeError:
                pass
            for m in modules:
                if hasattr(m, name):
                    return getattr(m, name)
            if name in aliases:
                mod_name, attr = aliases[name]
                return getattr(importlib.import_module(mod_name), attr)
            raise AttributeError(name)

        def __setattr__(self, name, value):
            mirrored = False
            for m in modules:
                if hasattr(m, name):
                    setattr(m, name, value)
                    mirrored = True
            if not mirrored:
                object.__setattr__(self, name, value)

    agg = Aggregate()
    agg.STATE_FILE = state_file  # mirrored onto repository module
    return agg
