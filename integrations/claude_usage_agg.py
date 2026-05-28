"""Cross-session cost aggregation over every Claude transcript on disk.

Where `claude_usage.parse_session_usage` totals one JSONL, this module walks
all JSONLs under `CLAUDE_PROJECTS_DIR` and buckets cost by **local date** and
**model family** (Opus / Sonnet / Haiku / Other). Output is the input for the
`/usage day` and `/usage week` chart renderers.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from core.config import CLAUDE_PROJECTS_DIR
from integrations.claude_pricing import cost_from_usage

# Model-family buckets the renderers know how to colour. Keys must match
# the substrings matched in claude_pricing.rates_for so the family attribution
# stays consistent with the cost computation itself.
FAMILIES: tuple[str, ...] = ("Opus", "Sonnet", "Haiku", "Other")


def _family(model: str | None) -> str:
    if not model:
        return "Other"
    m = model.lower()
    if "opus" in m:
        return "Opus"
    if "sonnet" in m:
        return "Sonnet"
    if "haiku" in m:
        return "Haiku"
    return "Other"


@dataclass
class DailyBucket:
    day: date
    cost_by_family: dict[str, float]

    @property
    def total(self) -> float:
        return sum(self.cost_by_family.values())


@dataclass
class WeeklyBucket:
    week_start: date  # Monday
    cost_by_family: dict[str, float]

    @property
    def total(self) -> float:
        return sum(self.cost_by_family.values())


def aggregate_daily(
    days: int = 14,
    *,
    projects_dir: Path | None = None,
    today: date | None = None,
) -> list[DailyBucket]:
    """Return one bucket per day for the last `days` days (oldest first).

    Days with no spend still appear with an empty family dict — the chart
    needs a flat baseline. ``today`` defaults to `date.today()` (local TZ).
    """
    projects_dir = projects_dir or CLAUDE_PROJECTS_DIR
    today = today or date.today()
    first = today - timedelta(days=days - 1)

    raw: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    if projects_dir.exists():
        for transcript in projects_dir.glob("*/*.jsonl"):
            _walk_transcript(transcript, first, today, raw)

    return [
        DailyBucket(
            day=first + timedelta(days=i),
            cost_by_family=dict(raw.get(first + timedelta(days=i), {})),
        )
        for i in range(days)
    ]


def aggregate_weekly(
    weeks: int = 4,
    *,
    projects_dir: Path | None = None,
    today: date | None = None,
) -> list[WeeklyBucket]:
    """Return one bucket per ISO week (Monday-anchored) for the last `weeks` weeks."""
    projects_dir = projects_dir or CLAUDE_PROJECTS_DIR
    today = today or date.today()
    # Anchor on this week's Monday.
    this_monday = today - timedelta(days=today.weekday())
    starts = [this_monday - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]

    earliest = starts[0]
    latest = starts[-1] + timedelta(days=6)

    raw: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    if projects_dir.exists():
        for transcript in projects_dir.glob("*/*.jsonl"):
            _walk_transcript(transcript, earliest, latest, raw)

    out: list[WeeklyBucket] = []
    for monday in starts:
        agg: dict[str, float] = defaultdict(float)
        for offset in range(7):
            d = monday + timedelta(days=offset)
            for fam, cost in raw.get(d, {}).items():
                agg[fam] += cost
        out.append(WeeklyBucket(week_start=monday, cost_by_family=dict(agg)))
    return out


def _walk_transcript(
    path: Path,
    earliest: date,
    latest: date,
    raw: dict[date, dict[str, float]],
) -> None:
    try:
        fh = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
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
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            ts = _parse_local_date(obj.get("timestamp"))
            if ts is None or ts < earliest or ts > latest:
                continue
            model = msg.get("model")
            cost = cost_from_usage(usage, model)
            if cost <= 0:
                continue
            raw[ts][_family(model)] += cost


def _parse_local_date(s) -> date | None:
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone().date()
