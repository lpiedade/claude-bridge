"""Render daily and weekly cross-session cost charts as PNG bytes."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from integrations.claude_usage_agg import (
    FAMILIES,
    DailyBucket,
    MonthlyBucket,
    WeeklyBucket,
)

_BG = "#0d141d"
_FG = "#e8eef5"
_MUTED = "#aebac8"
_GRID = "#1a2230"
_SPINE = "#3a4654"

# Same palette as /context cells for visual consistency across the bot.
_FAMILY_COLORS: dict[str, str] = {
    "Opus": "#c87654",
    "Sonnet": "#7a78c1",
    "Haiku": "#d9b94a",
    "Other": "#6e6e6e",
}


def _setup_axes(ax) -> None:
    ax.set_facecolor(_BG)
    ax.tick_params(colors=_MUTED, labelsize=10)
    for spine in ax.spines.values():
        spine.set_color(_SPINE)
    ax.grid(True, color=_GRID, linewidth=0.8, axis="y")


def render_daily_bars_png(buckets: list[DailyBucket]) -> bytes:
    fig, ax = plt.subplots(figsize=(10, 5), dpi=110)
    fig.patch.set_facecolor(_BG)
    _setup_axes(ax)

    if not buckets or all(b.total == 0 for b in buckets):
        ax.text(0.5, 0.5, "No spend in window", color=_MUTED,
                fontsize=14, ha="center", va="center", transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
    else:
        days = [b.day for b in buckets]
        bottoms = [0.0] * len(buckets)
        for fam in FAMILIES:
            heights = [b.cost_by_family.get(fam, 0.0) for b in buckets]
            if not any(heights):
                continue
            ax.bar(days, heights, bottom=bottoms,
                   color=_FAMILY_COLORS[fam], label=fam, width=0.8, edgecolor="none")
            bottoms = [b + h for b, h in zip(bottoms, heights, strict=True)]
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        fig.autofmt_xdate(rotation=30, ha="right")
        ax.legend(loc="upper left", facecolor=_BG, edgecolor=_SPINE,
                  labelcolor=_FG, framealpha=0.9)

    total = sum(b.total for b in buckets)
    ax.set_title(f"Daily spend — last {len(buckets)} days · total ${total:.2f}",
                 color=_FG, fontsize=14, weight="bold", loc="left", pad=12)
    ax.set_ylabel("USD per day", color=_MUTED, fontsize=11)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(),
                bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    return buf.getvalue()


def render_weekly_bars_png(buckets: list[WeeklyBucket]) -> bytes:
    fig, ax = plt.subplots(figsize=(10, 5), dpi=110)
    fig.patch.set_facecolor(_BG)
    _setup_axes(ax)

    if not buckets or all(b.total == 0 for b in buckets):
        ax.text(0.5, 0.5, "No spend in window", color=_MUTED,
                fontsize=14, ha="center", va="center", transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
    else:
        labels = [b.week_start.strftime("%b %d") for b in buckets]
        x = list(range(len(buckets)))
        bottoms = [0.0] * len(buckets)
        for fam in FAMILIES:
            heights = [b.cost_by_family.get(fam, 0.0) for b in buckets]
            if not any(heights):
                continue
            ax.bar(x, heights, bottom=bottoms,
                   color=_FAMILY_COLORS[fam], label=fam, width=0.6, edgecolor="none")
            bottoms = [b + h for b, h in zip(bottoms, heights, strict=True)]
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.legend(loc="upper left", facecolor=_BG, edgecolor=_SPINE,
                  labelcolor=_FG, framealpha=0.9)

        # Annotate totals + WoW delta over the last bar.
        for i, b in enumerate(buckets):
            if b.total <= 0:
                continue
            label = f"${b.total:.2f}"
            if i > 0 and buckets[i - 1].total > 0:
                delta = (b.total - buckets[i - 1].total) / buckets[i - 1].total * 100
                arrow = "▲" if delta >= 0 else "▼"
                label += f"\n{arrow}{abs(delta):.0f}%"
            ax.text(i, bottoms[i], label, ha="center", va="bottom",
                    color=_FG, fontsize=9)

    ax.set_title(f"Weekly spend — last {len(buckets)} weeks (Mon-Sun)",
                 color=_FG, fontsize=14, weight="bold", loc="left", pad=12)
    ax.set_ylabel("USD per week", color=_MUTED, fontsize=11)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(),
                bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    return buf.getvalue()


def render_monthly_bars_png(buckets: list[MonthlyBucket]) -> bytes:
    fig, ax = plt.subplots(figsize=(10, 5), dpi=110)
    fig.patch.set_facecolor(_BG)
    _setup_axes(ax)

    if not buckets or all(b.total == 0 for b in buckets):
        ax.text(0.5, 0.5, "No spend in window", color=_MUTED,
                fontsize=14, ha="center", va="center", transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
    else:
        labels = [b.month_start.strftime("%b %Y") for b in buckets]
        x = list(range(len(buckets)))
        bottoms = [0.0] * len(buckets)
        for fam in FAMILIES:
            heights = [b.cost_by_family.get(fam, 0.0) for b in buckets]
            if not any(heights):
                continue
            ax.bar(x, heights, bottom=bottoms,
                   color=_FAMILY_COLORS[fam], label=fam, width=0.6, edgecolor="none")
            bottoms = [b + h for b, h in zip(bottoms, heights, strict=True)]
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.legend(loc="upper left", facecolor=_BG, edgecolor=_SPINE,
                  labelcolor=_FG, framealpha=0.9)

        # Annotate totals + MoM delta over each bar.
        for i, b in enumerate(buckets):
            if b.total <= 0:
                continue
            label = f"${b.total:.2f}"
            if i > 0 and buckets[i - 1].total > 0:
                delta = (b.total - buckets[i - 1].total) / buckets[i - 1].total * 100
                arrow = "▲" if delta >= 0 else "▼"
                label += f"\n{arrow}{abs(delta):.0f}%"
            ax.text(i, bottoms[i], label, ha="center", va="bottom",
                    color=_FG, fontsize=9)

    ax.set_title(f"Monthly spend — last {len(buckets)} months",
                 color=_FG, fontsize=14, weight="bold", loc="left", pad=12)
    ax.set_ylabel("USD per month", color=_MUTED, fontsize=11)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(),
                bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    return buf.getvalue()
