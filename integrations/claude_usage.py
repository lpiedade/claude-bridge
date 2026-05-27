"""Parse a Claude CLI session transcript into a usage summary + timeline.

A transcript is the JSONL file at ``~/.claude/projects/<encoded-cwd>/<sid>.jsonl``
written by the CLI. Each assistant turn carries ``message.usage`` with
per-call token counts; we walk those, attribute cost via
:mod:`integrations.claude_pricing`, and emit:

* :class:`SessionUsage` — aggregate totals (used as caption fields).
* :class:`UsagePoint` list — cumulative cost/tokens per turn for charting.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from integrations.claude_pricing import cost_from_usage


@dataclass
class UsagePoint:
    timestamp: datetime
    cumulative_cost: float
    cumulative_tokens: int


@dataclass
class SessionUsage:
    session_id: str
    model: str | None
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_cost_usd: float = 0.0
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    timeline: list[UsagePoint] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


def parse_session_usage(path: Path, session_id: str) -> SessionUsage:
    """Walk a transcript and return the aggregate + per-turn timeline."""
    usage = SessionUsage(session_id=session_id, model=None)
    if not path.exists():
        return usage

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            u = msg.get("usage")
            if not isinstance(u, dict):
                continue

            model = msg.get("model")
            if model and not usage.model:
                usage.model = model

            usage.turns += 1
            usage.input_tokens += _i(u.get("input_tokens"))
            usage.output_tokens += _i(u.get("output_tokens"))
            usage.cache_read_tokens += _i(u.get("cache_read_input_tokens"))
            usage.cache_write_tokens += _i(u.get("cache_creation_input_tokens"))
            usage.total_cost_usd += cost_from_usage(u, model)

            ts = _parse_ts(obj.get("timestamp"))
            if ts is not None:
                if usage.first_ts is None:
                    usage.first_ts = ts
                usage.last_ts = ts
                usage.timeline.append(UsagePoint(
                    timestamp=ts,
                    cumulative_cost=usage.total_cost_usd,
                    cumulative_tokens=usage.total_tokens,
                ))
    return usage


def _i(v) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _parse_ts(s) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
