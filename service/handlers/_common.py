"""Shared authorization + path-allowlist helpers for command handlers."""
from __future__ import annotations

from telegram import Update

from core.config import ALLOWED_CHAT_IDS, ALLOWED_CWD_ROOTS
from utils.paths import is_cwd_allowed as _is_cwd_allowed_impl


def authorized(update: Update) -> bool:
    return bool(
        update.effective_chat
        and update.effective_chat.id in ALLOWED_CHAT_IDS
    )


def is_cwd_allowed(path: str) -> bool:
    return _is_cwd_allowed_impl(path, ALLOWED_CWD_ROOTS)
