"""Wire all Telegram command/message handlers onto the Application."""
from __future__ import annotations

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from .context import cmd_context
from .cwd import cmd_cd, cmd_ls, cmd_pwd
from .effort import cmd_effort
from .message import on_message
from .model import cmd_model
from .session import cmd_new
from .start import cmd_start, cmd_status
from .usage import cmd_usage


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("cd", cmd_cd))
    app.add_handler(CommandHandler("pwd", cmd_pwd))
    app.add_handler(CommandHandler("ls", cmd_ls))
    app.add_handler(CommandHandler("effort", cmd_effort))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("context", cmd_context))
    app.add_handler(CommandHandler("usage", cmd_usage))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
