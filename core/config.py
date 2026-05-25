"""Centralized configuration loaded from environment variables."""
from __future__ import annotations

import os
from pathlib import Path

BOT_TOKEN = os.environ["CLAUDE_BRIDGE_TG_TOKEN"]
ALLOWED_CHAT_IDS = {
    int(x) for x in os.environ["CLAUDE_BRIDGE_ALLOWED_CHATS"].split(",") if x.strip()
}
DEFAULT_CWD = os.path.expanduser(
    os.environ.get("CLAUDE_BRIDGE_CWD", "~/EDF/Personal/Github")
)
ALLOWED_CWD_ROOTS = [
    Path(os.path.expanduser(p)).resolve()
    for p in os.environ.get(
        "CLAUDE_BRIDGE_CWD_ROOTS",
        "~/EDF/Personal/Github,~/EDF/BlindBet,/tmp",
    ).split(",")
    if p.strip()
]
CLAUDE_BIN = "/opt/homebrew/bin/claude"
PERMISSION_MODE = os.environ.get("CLAUDE_BRIDGE_PERMISSION_MODE", "bypassPermissions")
TIMEOUT_SECONDS = int(os.environ.get("CLAUDE_BRIDGE_TIMEOUT", "600"))

VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
VALID_MODELS = {"opus", "sonnet", "haiku"}


def parse_effort(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    return v if v in VALID_EFFORTS else None


def parse_model(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    return v if v in VALID_MODELS else None


DEFAULT_EFFORT = parse_effort(os.environ.get("CLAUDE_BRIDGE_EFFORT")) or "low"
DEFAULT_MODEL = parse_model(os.environ.get("CLAUDE_BRIDGE_MODEL")) or "haiku"
