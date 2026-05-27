"""Render a `SessionUsage` timeline as a cumulative-cost line chart PNG."""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from integrations.claude_context_render import _model_display_name
from integrations.claude_usage import SessionUsage

_BG = "#0d141d"
_FG = "#e8eef5"
_MUTED = "#aebac8"
_LINE = "#7a78c1"
_FILL = "#7a78c133"


def render_usage_png(usage: SessionUsage) -> bytes:
    fig, ax = plt.subplots(figsize=(10, 5), dpi=110)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    if usage.timeline:
        xs = [p.timestamp for p in usage.timeline]
        ys = [p.cumulative_cost for p in usage.timeline]
        ax.plot(xs, ys, color=_LINE, linewidth=2.0, marker="o", markersize=3)
        ax.fill_between(xs, ys, color=_FILL)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        fig.autofmt_xdate()
    else:
        ax.text(0.5, 0.5, "No usage data yet",
                color=_MUTED, fontsize=14, ha="center", va="center",
                transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])

    title = f"Session cost — {_model_display_name(usage.model or 'unknown')}"
    ax.set_title(title, color=_FG, fontsize=14, weight="bold", loc="left", pad=12)
    ax.set_ylabel("USD (cumulative)", color=_MUTED, fontsize=11)
    ax.tick_params(colors=_MUTED, labelsize=10)
    for spine in ax.spines.values():
        spine.set_color("#3a4654")
    ax.grid(True, color="#1a2230", linewidth=0.8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(),
                bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    return buf.getvalue()
