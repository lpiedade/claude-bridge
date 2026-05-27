"""Render a `ContextUsage` as a PNG image mirroring Claude CLI's /context output."""
from __future__ import annotations

import io
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt

from integrations.claude_context import ContextUsage

# Color per category. Free space and Autocompact get special rendering.
_CATEGORY_COLORS: dict[str, str] = {
    "System prompt": "#6e6e6e",
    "System tools": "#9a9a9a",
    "System tools (deferred)": "#b8b8b8",
    "MCP tools": "#3aa6c2",
    "MCP tools (deferred)": "#5fbcd2",
    "Memory files": "#c87654",
    "Skills": "#d9b94a",
    "Messages": "#7a78c1",
}
_FREE_FACE = "#1a2230"          # background for empty cells
_FREE_EDGE = "#3a4654"
_AUTOCOMPACT_FACE = "#1a2230"
_AUTOCOMPACT_EDGE = "#5a6674"
_GRID_BG = "#0d141d"


def _model_display_name(model_id: str) -> str:
    """Map a CLI model id to a friendly display name."""
    m = model_id.lower()
    if "opus-4-7" in m:
        return "Opus 4.7"
    if "opus-4-6" in m:
        return "Opus 4.6"
    if "sonnet-4-6" in m:
        return "Sonnet 4.6"
    if "haiku-4-5" in m:
        return "Haiku 4.5"
    return model_id


def render_context_png(usage: ContextUsage, *, grid_cols: int = 10, grid_rows: int = 20) -> bytes:
    """Return PNG bytes for the given context usage."""
    total_cells = grid_cols * grid_rows

    # Build a flat list of cells, one entry per category in declared order.
    ordered: list[tuple[str, float, int]] = []
    seen = set()
    # Preserve original order from the parsed table.
    for row in usage.categories:
        ordered.append((row.name, row.pct, row.tokens))
        seen.add(row.name)

    # Assign each category an integer count of cells proportional to its pct.
    raw = [(name, max(0.0, pct) * total_cells / 100, tokens) for name, pct, tokens in ordered]
    cells_per_cat = [
        (name, int(math.floor(c)), pct, tokens)
        for (name, c, tokens), pct in zip(
            [(n, v, t) for n, v, t in raw],
            [p for _, p, _ in ordered],
            strict=True,
        )
    ]
    # Distribute leftover cells to the largest remainders so totals match `total_cells`.
    used_cells = sum(c for _, c, _, _ in cells_per_cat)
    leftover = total_cells - used_cells
    if leftover > 0:
        remainders = sorted(
            range(len(cells_per_cat)),
            key=lambda i: (raw[i][1] - cells_per_cat[i][1]),
            reverse=True,
        )
        for idx in remainders[:leftover]:
            name, c, pct, tokens = cells_per_cat[idx]
            cells_per_cat[idx] = (name, c + 1, pct, tokens)
    elif leftover < 0:
        # Over-counted (rare); strip from smallest fractions.
        remainders = sorted(
            range(len(cells_per_cat)),
            key=lambda i: (raw[i][1] - cells_per_cat[i][1]),
        )
        to_remove = -leftover
        for idx in remainders:
            if to_remove <= 0:
                break
            name, c, pct, tokens = cells_per_cat[idx]
            if c > 0:
                cells_per_cat[idx] = (name, c - 1, pct, tokens)
                to_remove -= 1

    # Expand into a per-cell category list.
    cell_assignment: list[str] = []
    for name, c, _, _ in cells_per_cat:
        cell_assignment.extend([name] * c)
    # Pad with "Free space" if anything's missing.
    while len(cell_assignment) < total_cells:
        cell_assignment.append("Free space")
    cell_assignment = cell_assignment[:total_cells]

    # ---- Figure layout ----
    fig = plt.figure(figsize=(12, 6.2), dpi=110)
    fig.patch.set_facecolor(_GRID_BG)
    gs = fig.add_gridspec(1, 2, width_ratios=[0.42, 0.58], wspace=0.04)
    ax_grid = fig.add_subplot(gs[0, 0])
    ax_text = fig.add_subplot(gs[0, 1])
    for ax in (ax_grid, ax_text):
        ax.set_facecolor(_GRID_BG)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

    # Draw the grid of cells.
    ax_grid.set_xlim(-0.6, grid_cols)
    ax_grid.set_ylim(-0.6, grid_rows + 0.4)
    ax_grid.invert_yaxis()
    cell_size = 0.78
    for idx, cat in enumerate(cell_assignment):
        col = idx % grid_cols
        row = idx // grid_cols
        x = col + (1 - cell_size) / 2
        y = row + (1 - cell_size) / 2

        if cat == "Free space":
            rect = patches.Rectangle(
                (x, y), cell_size, cell_size,
                linewidth=1.2, edgecolor=_FREE_EDGE, facecolor=_FREE_FACE,
            )
            ax_grid.add_patch(rect)
        elif cat == "Autocompact buffer":
            rect = patches.Rectangle(
                (x, y), cell_size, cell_size,
                linewidth=1.2, edgecolor=_AUTOCOMPACT_EDGE, facecolor=_AUTOCOMPACT_FACE,
            )
            ax_grid.add_patch(rect)
            ax_grid.plot(
                [x, x + cell_size], [y, y + cell_size],
                color=_AUTOCOMPACT_EDGE, linewidth=1.2,
            )
            ax_grid.plot(
                [x, x + cell_size], [y + cell_size, y],
                color=_AUTOCOMPACT_EDGE, linewidth=1.2,
            )
        else:
            color = _CATEGORY_COLORS.get(cat, "#888888")
            rect = patches.FancyBboxPatch(
                (x, y), cell_size, cell_size,
                boxstyle="round,pad=0.02,rounding_size=0.15",
                linewidth=0, facecolor=color,
            )
            ax_grid.add_patch(rect)

    ax_grid.text(
        -0.4, -0.35, "Context Usage",
        color="#e8eef5", fontsize=15, weight="bold", va="bottom",
    )
    ax_grid.set_aspect("equal", adjustable="box")

    # ---- Text panel ----
    # Use a normal (non-inverted) y axis with y growing downward via row index.
    n_rows = 4 + len(usage.categories)  # header(3) + section title + cats
    ax_text.set_xlim(0, 10)
    ax_text.set_ylim(n_rows + 1, -1)  # invert: row 0 at top
    row_h = 1.0

    display_name = _model_display_name(usage.model)
    pct_str = f"{usage.used_pct:.1f}%"
    used_str = _fmt(usage.used)
    total_str = _fmt(usage.total)

    ax_text.text(0.0, 0.4, display_name, color="#e8eef5", fontsize=18,
                 weight="bold", fontfamily="monospace", va="center")
    ax_text.text(0.0, 1.4, usage.model, color="#aebac8", fontsize=12,
                 fontfamily="monospace", va="center")
    ax_text.text(0.0, 2.3, f"{used_str}/{total_str} tokens ({pct_str})",
                 color="#aebac8", fontsize=12, fontfamily="monospace", va="center")
    ax_text.text(0.0, 3.7, "Estimated usage by category",
                 color="#aebac8", fontsize=12, style="italic", va="center")

    icon_w = 0.35
    icon_h = 0.55
    for i, cat in enumerate(usage.categories):
        cy = 4.8 + i * row_h
        icon_y = cy - icon_h / 2
        if cat.name == "Free space":
            ax_text.add_patch(patches.Rectangle(
                (0.0, icon_y), icon_w, icon_h,
                linewidth=1.2, edgecolor=_FREE_EDGE, facecolor=_FREE_FACE,
            ))
        elif cat.name == "Autocompact buffer":
            ax_text.add_patch(patches.Rectangle(
                (0.0, icon_y), icon_w, icon_h,
                linewidth=1.2, edgecolor=_AUTOCOMPACT_EDGE,
                facecolor=_AUTOCOMPACT_FACE,
            ))
            ax_text.plot([0.0, icon_w], [icon_y, icon_y + icon_h],
                         color=_AUTOCOMPACT_EDGE, linewidth=1)
            ax_text.plot([0.0, icon_w], [icon_y + icon_h, icon_y],
                         color=_AUTOCOMPACT_EDGE, linewidth=1)
        else:
            color = _CATEGORY_COLORS.get(cat.name, "#888888")
            ax_text.add_patch(patches.FancyBboxPatch(
                (0.0, icon_y), icon_w, icon_h,
                boxstyle="round,pad=0.0,rounding_size=0.08",
                linewidth=0, facecolor=color,
            ))

        tok_part = _fmt(cat.tokens)
        line = f"{cat.name}: {tok_part} tokens ({cat.pct}%)"
        ax_text.text(0.55, cy, line, color="#dde5ee",
                     fontsize=12, fontfamily="monospace", va="center")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(),
                bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return buf.getvalue()


def _fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}m".rstrip("0").rstrip(".") + "m" if False else f"{n / 1_000_000:.1f}m"
    if n >= 10_000:
        return f"{n // 1000}k"
    if n >= 1_000:
        return f"{n / 1000:.1f}k"
    return str(n)
