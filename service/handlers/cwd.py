"""/cd, /pwd, /ls — working-directory commands."""
from __future__ import annotations

from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from core.config import ALLOWED_CWD_ROOTS
from core.logger import log
from repositories.session_repository import (
    claim_update,
    session_for,
    set_last_update_id,
    update_session,
)
from utils.paths import resolve_arg, safe_resolve

from ._common import authorized, is_cwd_allowed

LS_MAX_ENTRIES = 80


async def cmd_pwd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await claim_update(update):
        return
    try:
        info = await session_for(update.effective_chat.id)
        await update.message.reply_text(info["cwd"])
    finally:
        await set_last_update_id(update.update_id)


async def cmd_cd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
        if not ctx.args:
            await update.message.reply_text(f"CWD: {info['cwd']}")
            return
        new_cwd = resolve_arg(ctx.args[0], info["cwd"])
        if not Path(new_cwd).is_dir():
            await update.message.reply_text(f"Not a directory: {new_cwd}")
            return
        if not is_cwd_allowed(new_cwd):
            log.warning(
                "blocked /cd: chat=%s requested=%r resolved=%r allowed_roots=%s",
                chat_id,
                new_cwd,
                safe_resolve(new_cwd),
                [str(r) for r in ALLOWED_CWD_ROOTS],
            )
            allowed = ", ".join(str(r) for r in ALLOWED_CWD_ROOTS)
            await update.message.reply_text(
                f"Path not in allowed roots: {new_cwd}\nAllowed: {allowed}"
            )
            return
        info = await update_session(chat_id, cwd=new_cwd)
        await update.message.reply_text(f"CWD: {info['cwd']}")
    finally:
        await set_last_update_id(update.update_id)


async def cmd_ls(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await claim_update(update):
        return
    try:
        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
        target = resolve_arg(ctx.args[0], info["cwd"]) if ctx.args else info["cwd"]
        if not Path(target).is_dir():
            await update.message.reply_text(f"Not a directory: {target}")
            return
        if not is_cwd_allowed(target):
            log.warning(
                "blocked /ls: chat=%s requested=%r resolved=%r allowed_roots=%s",
                chat_id,
                target,
                safe_resolve(target),
                [str(r) for r in ALLOWED_CWD_ROOTS],
            )
            allowed = ", ".join(str(r) for r in ALLOWED_CWD_ROOTS)
            await update.message.reply_text(
                f"Path not in allowed roots: {target}\nAllowed: {allowed}"
            )
            return
        try:
            entries = sorted(
                Path(target).iterdir(),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except PermissionError:
            await update.message.reply_text(f"Permission denied: {target}")
            return
        if not entries:
            await update.message.reply_text(f"{target}\n(empty)")
            return
        n_dirs = sum(1 for e in entries if e.is_dir())
        n_files = len(entries) - n_dirs
        shown = entries[:LS_MAX_ENTRIES]
        lines = [f"{target} — {n_dirs} dirs, {n_files} files", ""]
        for e in shown:
            suffix = "/" if e.is_dir() else ""
            lines.append(f"{e.name}{suffix}")
        if len(entries) > LS_MAX_ENTRIES:
            lines.append(f"... ({len(entries) - LS_MAX_ENTRIES} more)")
        await update.message.reply_text("\n".join(lines))
    finally:
        await set_last_update_id(update.update_id)
