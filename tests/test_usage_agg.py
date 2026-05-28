"""Tests for the cross-session aggregator (/usage day, /usage week)."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from integrations.claude_usage_agg import (
    FAMILIES,
    _family,
    aggregate_daily,
    aggregate_weekly,
)


def _assistant_row(model: str, usage: dict, ts: datetime) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "message": {"model": model, "usage": usage},
    }


def _write_session(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows))


def test_family_classification():
    assert _family("claude-opus-4-7") == "Opus"
    assert _family("claude-sonnet-4-6") == "Sonnet"
    assert _family("claude-haiku-4-5-20251001") == "Haiku"
    assert _family("claude-future-99") == "Other"
    assert _family(None) == "Other"
    assert all(_family(m) in FAMILIES for m in ("claude-opus-4-7", None, "x"))


def test_aggregate_daily_bins_by_local_date(tmp_path: Path):
    today = date(2026, 5, 27)
    ts_today = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    ts_yday = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)

    _write_session(
        tmp_path / "proj/abc.jsonl",
        [
            _assistant_row("claude-haiku-4-5", {"output_tokens": 1_000_000}, ts_yday),
            _assistant_row("claude-haiku-4-5", {"output_tokens": 2_000_000}, ts_today),
        ],
    )

    buckets = aggregate_daily(days=3, projects_dir=tmp_path, today=today)
    assert len(buckets) == 3
    assert buckets[0].total == 0.0  # day before yesterday
    assert buckets[1].cost_by_family["Haiku"] == pytest.approx(5.0)  # yesterday: 1M out × $5
    assert buckets[2].cost_by_family["Haiku"] == pytest.approx(10.0)  # today: 2M out × $5


def test_aggregate_daily_filters_outside_window(tmp_path: Path):
    today = date(2026, 5, 27)
    old = datetime(2025, 1, 1, tzinfo=UTC)
    _write_session(
        tmp_path / "proj/abc.jsonl",
        [_assistant_row("claude-haiku-4-5", {"output_tokens": 1_000_000}, old)],
    )
    buckets = aggregate_daily(days=14, projects_dir=tmp_path, today=today)
    assert all(b.total == 0.0 for b in buckets)


def test_aggregate_daily_walks_multiple_session_dirs(tmp_path: Path):
    today = date(2026, 5, 27)
    ts = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    _write_session(
        tmp_path / "proj-a/s1.jsonl",
        [_assistant_row("claude-opus-4-7", {"output_tokens": 1_000_000}, ts)],
    )
    _write_session(
        tmp_path / "proj-b/s2.jsonl",
        [_assistant_row("claude-haiku-4-5", {"output_tokens": 1_000_000}, ts)],
    )
    buckets = aggregate_daily(days=2, projects_dir=tmp_path, today=today)
    today_bucket = buckets[-1]
    assert today_bucket.cost_by_family["Opus"] == pytest.approx(75.0)  # 1M out × $75
    assert today_bucket.cost_by_family["Haiku"] == pytest.approx(5.0)


def test_aggregate_daily_missing_projects_dir_returns_empty_buckets(tmp_path: Path):
    today = date(2026, 5, 27)
    buckets = aggregate_daily(days=5, projects_dir=tmp_path / "does-not-exist", today=today)
    assert len(buckets) == 5
    assert all(b.total == 0.0 for b in buckets)


def test_aggregate_weekly_groups_seven_days_per_bucket(tmp_path: Path):
    # Anchor on a known Wednesday so "this week's Monday" is unambiguous.
    today = date(2026, 5, 27)  # Wednesday
    this_monday = date(2026, 5, 25)

    ts_a = datetime(2026, 5, 25, 10, tzinfo=UTC)  # this week
    ts_b = datetime(2026, 5, 26, 10, tzinfo=UTC)  # this week
    ts_c = datetime(2026, 5, 19, 10, tzinfo=UTC)  # previous week

    _write_session(
        tmp_path / "proj/abc.jsonl",
        [
            _assistant_row("claude-haiku-4-5", {"output_tokens": 1_000_000}, ts_a),
            _assistant_row("claude-haiku-4-5", {"output_tokens": 1_000_000}, ts_b),
            _assistant_row("claude-haiku-4-5", {"output_tokens": 1_000_000}, ts_c),
        ],
    )

    weeks = aggregate_weekly(weeks=2, projects_dir=tmp_path, today=today)
    assert weeks[-1].week_start == this_monday
    assert weeks[-1].total == pytest.approx(10.0)  # 2 rows × $5
    assert weeks[-2].total == pytest.approx(5.0)


def test_aggregate_skips_malformed_lines(tmp_path: Path):
    today = date(2026, 5, 27)
    ts = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    p = tmp_path / "proj/abc.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(
        "not-json\n"
        + json.dumps(_assistant_row("claude-haiku-4-5", {"output_tokens": 1_000_000}, ts))
        + "\n"
        + "{broken\n"
    )
    buckets = aggregate_daily(days=2, projects_dir=tmp_path, today=today)
    assert buckets[-1].cost_by_family["Haiku"] == pytest.approx(5.0)


def test_render_renders_empty_window_without_error():
    # No transcripts → buckets are populated with empty dicts. Renderer should
    # still produce a valid PNG (with the "no spend" placeholder).
    from integrations.claude_usage_agg import DailyBucket, WeeklyBucket
    from integrations.claude_usage_agg_render import (
        render_daily_bars_png,
        render_weekly_bars_png,
    )

    empty_days = [DailyBucket(day=date(2026, 5, 27), cost_by_family={})]
    empty_weeks = [WeeklyBucket(week_start=date(2026, 5, 25), cost_by_family={})]
    assert len(render_daily_bars_png(empty_days)) > 1000
    assert len(render_weekly_bars_png(empty_weeks)) > 1000
