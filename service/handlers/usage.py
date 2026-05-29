"""/usage — token + cost report for the active session, or cross-session aggregates.

  /usage         → PNG line chart of cumulative cost over the active session.
  /usage day     → stacked bar chart of daily spend across all sessions (14d).
  /usage week    → stacked bar chart of weekly spend with WoW delta (4 weeks).
  /usage month   → stacked bar chart of monthly spend with MoM delta (6 months).

The cross-session modes walk every JSONL under `~/.claude/projects/*` and bin
cost by local date + model family; they do not invoke the Claude CLI.
"""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from core.logger import log
from integrations.claude_context_render import _model_display_name
from integrations.claude_usage import SessionUsage, parse_session_usage
from integrations.claude_usage_agg import (
    DailyBucket,
    MonthlyBucket,
    WeeklyBucket,
    aggregate_daily,
    aggregate_monthly,
    aggregate_weekly,
)
from integrations.claude_usage_agg_render import (
    render_daily_bars_png,
    render_monthly_bars_png,
    render_weekly_bars_png,
)
from integrations.claude_usage_render import render_usage_png
from repositories.session_repository import claim_update, session_for, set_last_update_id
from scripts.cost_alert import find_transcript

from ._common import authorized


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _caption(usage: SessionUsage) -> str:
    model_label = _model_display_name(usage.model) if usage.model else "(no calls yet)"
    lines = [
        f"Session Usage — {model_label}",
        f"Turns: {usage.turns}",
        f"Tokens: in {_fmt_tokens(usage.input_tokens)} · out {_fmt_tokens(usage.output_tokens)} · "
        f"cache r/w {_fmt_tokens(usage.cache_read_tokens)}/{_fmt_tokens(usage.cache_write_tokens)}",
        f"Cost: ${usage.total_cost_usd:.4f}",
    ]
    return "\n".join(lines)


def _daily_caption(buckets: list[DailyBucket]) -> str:
    total = sum(b.total for b in buckets)
    by_fam: dict[str, float] = {}
    for b in buckets:
        for fam, cost in b.cost_by_family.items():
            by_fam[fam] = by_fam.get(fam, 0.0) + cost
    fam_breakdown = " · ".join(
        f"{fam} ${cost:.2f}" for fam, cost in sorted(by_fam.items(), key=lambda kv: -kv[1])
    ) or "no spend"
    return (
        f"📊 Daily spend — {len(buckets)} days\n"
        f"Total: ${total:.2f}\n"
        f"By family: {fam_breakdown}"
    )


def _weekly_caption(buckets: list[WeeklyBucket]) -> str:
    total = sum(b.total for b in buckets)
    last = buckets[-1].total if buckets else 0.0
    prev = buckets[-2].total if len(buckets) >= 2 else 0.0
    if prev > 0:
        delta = (last - prev) / prev * 100
        wow = f"WoW: {'▲' if delta >= 0 else '▼'}{abs(delta):.0f}% (${last:.2f} vs ${prev:.2f})"
    else:
        wow = f"WoW: no baseline (current: ${last:.2f})"
    return (
        f"📊 Weekly spend — last {len(buckets)} weeks\n"
        f"Total: ${total:.2f}\n"
        f"{wow}"
    )


def _monthly_caption(buckets: list[MonthlyBucket]) -> str:
    total = sum(b.total for b in buckets)
    last = buckets[-1].total if buckets else 0.0
    prev = buckets[-2].total if len(buckets) >= 2 else 0.0
    if prev > 0:
        delta = (last - prev) / prev * 100
        mom = f"MoM: {'▲' if delta >= 0 else '▼'}{abs(delta):.0f}% (${last:.2f} vs ${prev:.2f})"
    else:
        mom = f"MoM: no baseline (current: ${last:.2f})"
    return (
        f"📊 Monthly spend — last {len(buckets)} months\n"
        f"Total: ${total:.2f}\n"
        f"{mom}"
    )


async def _serve_monthly(update: Update, ctx: ContextTypes.DEFAULT_TYPE, months: int) -> None:
    chat_id = update.effective_chat.id
    await ctx.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    buckets = await asyncio.to_thread(aggregate_monthly, months)
    try:
        png = await asyncio.to_thread(render_monthly_bars_png, buckets)
    except Exception:
        log.exception("render_monthly_bars_png failed chat=%s", chat_id)
        await update.effective_message.reply_text(_monthly_caption(buckets))
        return
    await update.effective_message.reply_photo(photo=png, caption=_monthly_caption(buckets))


async def _serve_daily(update: Update, ctx: ContextTypes.DEFAULT_TYPE, days: int) -> None:
    chat_id = update.effective_chat.id
    await ctx.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    buckets = await asyncio.to_thread(aggregate_daily, days)
    try:
        png = await asyncio.to_thread(render_daily_bars_png, buckets)
    except Exception:
        log.exception("render_daily_bars_png failed chat=%s", chat_id)
        await update.effective_message.reply_text(_daily_caption(buckets))
        return
    await update.effective_message.reply_photo(photo=png, caption=_daily_caption(buckets))


async def _serve_weekly(update: Update, ctx: ContextTypes.DEFAULT_TYPE, weeks: int) -> None:
    chat_id = update.effective_chat.id
    await ctx.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
    buckets = await asyncio.to_thread(aggregate_weekly, weeks)
    try:
        png = await asyncio.to_thread(render_weekly_bars_png, buckets)
    except Exception:
        log.exception("render_weekly_bars_png failed chat=%s", chat_id)
        await update.effective_message.reply_text(_weekly_caption(buckets))
        return
    await update.effective_message.reply_photo(photo=png, caption=_weekly_caption(buckets))


async def cmd_usage(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    if not await claim_update(update):
        return
    try:
        args = ctx.args or []
        sub = (args[0].lower() if args else "").strip()
        if sub == "day":
            await _serve_daily(update, ctx, days=14)
            return
        if sub == "week":
            await _serve_weekly(update, ctx, weeks=4)
            return
        if sub == "month":
            await _serve_monthly(update, ctx, months=6)
            return
        if sub and sub not in ("day", "week", "month"):
            await update.effective_message.reply_text(
                "Usage: /usage [day|week|month]. No arg = current session."
            )
            return

        chat_id = update.effective_chat.id
        info = await session_for(chat_id)
        sid = info["session_id"]

        transcript = await asyncio.to_thread(find_transcript, sid)
        if transcript is None:
            await update.effective_message.reply_text(
                f"No transcript yet for session {sid}. Send a message first."
            )
            return

        await ctx.bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
        try:
            usage = await asyncio.to_thread(parse_session_usage, transcript, sid)
        except Exception as exc:
            log.exception("parse_session_usage failed chat=%s", chat_id)
            await update.effective_message.reply_text(f"Failed to read transcript: {exc}")
            return

        if usage.turns == 0:
            await update.effective_message.reply_text(
                f"Session {sid} has no assistant turns yet."
            )
            return

        try:
            png = await asyncio.to_thread(render_usage_png, usage)
        except Exception:
            log.exception("render_usage_png failed chat=%s", chat_id)
            await update.effective_message.reply_text(_caption(usage))
            return

        await update.effective_message.reply_photo(photo=png, caption=_caption(usage))
    finally:
        await set_last_update_id(update.update_id)
