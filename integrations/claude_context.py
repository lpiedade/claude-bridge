"""Invoke ``claude /context`` and parse its output into structured data.

The Claude CLI exposes ``/context`` as a slash command; when invoked via
``claude --resume <sid> -p "/context" --output-format json`` it runs
synthetically — ``num_turns=0`` and ``total_cost_usd=0`` — so we can poll it
cheaply from the bot.

The result text is markdown with a header line for total tokens plus a
"Estimated usage by category" table. We parse both into a `ContextUsage`.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field

from core.config import CLAUDE_BIN, TIMEOUT_SECONDS

# Display labels we recognize in the category table (in canonical render order).
KNOWN_CATEGORIES: tuple[str, ...] = (
    "System prompt",
    "System tools",
    "System tools (deferred)",
    "MCP tools",
    "MCP tools (deferred)",
    "Memory files",
    "Skills",
    "Messages",
    "Free space",
    "Autocompact buffer",
)


@dataclass
class CategoryRow:
    name: str
    tokens: int
    pct: float


@dataclass
class ContextUsage:
    model: str
    used: int
    total: int
    used_pct: float
    categories: list[CategoryRow] = field(default_factory=list)


_NUM_RE = re.compile(r"^([\d.]+)\s*([kKmM]?)$")
_TILDE_RE = re.compile(r"^~?\s*([\d.]+)\s*([kKmM<>]*)\s*$")


def _parse_token_count(s: str) -> int:
    """Parse '62k', '1m', '545', '< 20', '~100' into an int."""
    s = s.strip()
    if not s:
        return 0
    s = s.replace("< ", "").replace(">", "").replace("~", "").strip()
    m = _NUM_RE.match(s)
    if not m:
        return 0
    value = float(m.group(1))
    suffix = m.group(2).lower()
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    return int(round(value))


def _parse_pct(s: str) -> float:
    s = s.strip().rstrip("%").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


_TOKENS_LINE_RE = re.compile(
    r"\*\*Tokens:\*\*\s*([\d.kKmM]+)\s*/\s*([\d.kKmM]+)\s*\(([\d.]+)%\)"
)
_MODEL_LINE_RE = re.compile(r"\*\*Model:\*\*\s*([^\s].*?)\s*$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")


def parse_context_markdown(text: str) -> ContextUsage:
    """Extract model, totals, and the category table from `/context` markdown."""
    model_match = _MODEL_LINE_RE.search(text)
    model = model_match.group(1).strip() if model_match else "unknown"

    tokens_match = _TOKENS_LINE_RE.search(text)
    if tokens_match:
        used = _parse_token_count(tokens_match.group(1))
        total = _parse_token_count(tokens_match.group(2))
        used_pct = float(tokens_match.group(3))
    else:
        used = total = 0
        used_pct = 0.0

    categories: list[CategoryRow] = []
    in_category_table = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if "Estimated usage by category" in line:
            in_category_table = True
            continue
        if in_category_table and line.startswith("### ") and "category" not in line.lower():
            break
        if not in_category_table:
            continue
        m = _TABLE_ROW_RE.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        if name.lower().startswith("category") or set(name) <= {"-", " "}:
            continue
        tokens = _parse_token_count(m.group(2))
        pct = _parse_pct(m.group(3))
        categories.append(CategoryRow(name=name, tokens=tokens, pct=pct))

    return ContextUsage(
        model=model, used=used, total=total, used_pct=used_pct, categories=categories,
    )


def fetch_context(
    session_id: str,
    cwd: str,
    *,
    model: str | None = None,
    timeout: int = TIMEOUT_SECONDS,
) -> ContextUsage:
    """Run ``claude --resume <sid> -p '/context'`` and return parsed usage."""
    cmd = [
        CLAUDE_BIN,
        "--resume", session_id,
        "-p", "/context",
        "--output-format", "json",
    ]
    if model:
        cmd += ["--model", model]
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude /context failed rc={proc.returncode}: "
            f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
        )
    payload = json.loads(proc.stdout)
    result_text = ""
    if isinstance(payload, list):
        for evt in reversed(payload):
            if isinstance(evt, dict) and evt.get("type") == "result":
                result_text = evt.get("result", "") or ""
                break
    elif isinstance(payload, dict):
        result_text = payload.get("result", "") or ""
    if not result_text:
        raise RuntimeError("claude /context returned no result text")
    return parse_context_markdown(result_text)
