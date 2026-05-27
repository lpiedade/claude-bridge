"""Tests for pricing + usage parser + cost_alert fallback."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.claude_pricing import cost_from_usage, rates_for
from integrations.claude_usage import parse_session_usage


def test_rates_match_by_substring():
    assert rates_for("claude-opus-4-7").output == 75.0
    assert rates_for("claude-sonnet-4-6").output == 15.0
    assert rates_for("claude-haiku-4-5-20251001").output == 5.0


def test_rates_unknown_model_falls_back_to_haiku():
    assert rates_for("claude-future-7").output == 5.0
    assert rates_for(None).output == 5.0


def test_cost_from_usage_splits_5m_and_1h_buckets():
    # 1M output tokens at Opus = $75; 1M 1h cache write = $30
    usage = {
        "output_tokens": 1_000_000,
        "cache_creation_input_tokens": 1_000_000,
        "cache_creation": {
            "ephemeral_5m_input_tokens": 0,
            "ephemeral_1h_input_tokens": 1_000_000,
        },
    }
    assert cost_from_usage(usage, "claude-opus-4-7") == pytest.approx(105.0)


def test_cost_from_usage_without_breakdown_uses_5m_rate():
    usage = {"cache_creation_input_tokens": 1_000_000}
    # Opus 5m write rate is $18.75/M
    assert cost_from_usage(usage, "claude-opus-4-7") == pytest.approx(18.75)


def test_cost_from_usage_handles_non_dict():
    assert cost_from_usage(None, "claude-opus-4-7") == 0.0


def _assistant_row(model: str, usage: dict, ts: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {"model": model, "usage": usage},
    }


def test_parse_session_usage_aggregates_and_builds_timeline(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    rows = [
        _assistant_row("claude-haiku-4-5", {"input_tokens": 1_000_000, "output_tokens": 0}, "2026-05-26T10:00:00Z"),
        _assistant_row("claude-haiku-4-5", {"input_tokens": 0, "output_tokens": 1_000_000}, "2026-05-26T10:01:00Z"),
        {"type": "user", "message": {}, "timestamp": "x"},  # ignored
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))

    u = parse_session_usage(p, "s")
    assert u.turns == 2
    assert u.input_tokens == 1_000_000
    assert u.output_tokens == 1_000_000
    assert u.total_cost_usd == pytest.approx(6.0)  # 1*$1 + 1*$5 at Haiku
    assert u.model == "claude-haiku-4-5"
    assert len(u.timeline) == 2
    assert u.timeline[-1].cumulative_cost == pytest.approx(6.0)


def test_parse_session_usage_missing_file(tmp_path: Path):
    u = parse_session_usage(tmp_path / "nope.jsonl", "s")
    assert u.turns == 0
    assert u.timeline == []


def test_cost_alert_falls_back_to_computed_cost(tmp_path: Path):
    """When transcript has no costUSD/total_cost_usd, sum tokens × pricing."""
    from scripts import cost_alert

    p = tmp_path / "s.jsonl"
    rows = [
        _assistant_row("claude-haiku-4-5", {"output_tokens": 1_000_000}, "2026-05-26T10:00:00Z"),
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    assert cost_alert.cost_from_transcript(p) == pytest.approx(5.0)


def test_cost_alert_prefers_legacy_costUSD_when_present(tmp_path: Path):
    """Legacy field still wins to preserve the historical behavior."""
    from scripts import cost_alert

    p = tmp_path / "s.jsonl"
    rows = [
        {"costUSD": 2.5},
        _assistant_row("claude-haiku-4-5", {"output_tokens": 1_000_000}, "2026-05-26T10:00:00Z"),
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    # legacy=2.5, computed=5.0 — legacy wins because >0
    assert cost_alert.cost_from_transcript(p) == pytest.approx(2.5)
