"""Entrypoint for the Telegram -> Claude Code bridge.

Run with: ``python -m app.main`` (or ``claude-bridge`` after install).
"""
from __future__ import annotations

from telegram.ext import Application

from core.config import ALLOWED_CHAT_IDS, ALLOWED_CWD_ROOTS, BOT_TOKEN, DEFAULT_CWD
from core.logger import configure as configure_logging, log
from service.handlers import register
from service.handlers._common import is_cwd_allowed


def main() -> None:
    configure_logging()

    if not is_cwd_allowed(DEFAULT_CWD):
        raise SystemExit(
            f"DEFAULT_CWD {DEFAULT_CWD!r} is not under any of "
            f"CLAUDE_BRIDGE_CWD_ROOTS={[str(r) for r in ALLOWED_CWD_ROOTS]!r}"
        )

    app = Application.builder().token(BOT_TOKEN).build()
    register(app)
    log.info("Bridge online. Allowed chats: %s", ALLOWED_CHAT_IDS)
    app.run_polling()


if __name__ == "__main__":
    main()
