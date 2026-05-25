"""Logging setup for the claude-bridge service.

Three sinks:
- stderr (captured by launchd as ``launchd.err``) — WARNING+ only, so crashes
  and tracebacks remain visible without being drowned by polling noise.
- ``~/.claude-bridge/bridge.log`` — operational INFO+ log, rotated.
- ``~/.claude-bridge/conversation.log`` — prompt/response history, rotated.
  Written via the dedicated ``claude-bridge.conversation`` logger.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(os.path.expanduser("~/.claude-bridge"))
_BRIDGE_LOG = _LOG_DIR / "bridge.log"
_CONVERSATION_LOG = _LOG_DIR / "conversation.log"

_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 5

_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_CONV_FMT = "%(asctime)s %(message)s"


def _rotating(path: Path, fmt: str) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(fmt))
    return handler


def configure() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Drop any handlers a previous configure() / basicConfig() may have added.
    for h in list(root.handlers):
        root.removeHandler(h)

    # stderr -> launchd.err: only WARNING+ so polling INFO doesn't drown it.
    stderr_handler = logging.StreamHandler()
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter(_FMT))
    root.addHandler(stderr_handler)

    # bridge.log: full INFO+ stream, rotated.
    bridge_handler = _rotating(_BRIDGE_LOG, _FMT)
    bridge_handler.setLevel(logging.INFO)
    root.addHandler(bridge_handler)

    # Silence chatty third-party loggers.
    for name in ("httpx", "httpcore", "telegram", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Conversation logger: isolated from root, own rotating file.
    conv = logging.getLogger("claude-bridge.conversation")
    conv.setLevel(logging.INFO)
    conv.propagate = False
    for h in list(conv.handlers):
        conv.removeHandler(h)
    conv.addHandler(_rotating(_CONVERSATION_LOG, _CONV_FMT))


log = logging.getLogger("claude-bridge")
conversation_log = logging.getLogger("claude-bridge.conversation")
