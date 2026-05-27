"""Tests for scripts.cost_alert: parser, dedupe, threshold boundary."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import cost_alert


def _write_transcript(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows))


# ---------- parser ----------

def test_cost_from_transcript_sums_costUSD(tmp_path):
    p = tmp_path / "abc.jsonl"
    _write_transcript(p, [{"costUSD": 1.5}, {"costUSD": 2.25}, {"other": "x"}])
    assert cost_alert.cost_from_transcript(p) == pytest.approx(3.75)


def test_cost_from_transcript_falls_back_to_total_cost_usd(tmp_path):
    p = tmp_path / "abc.jsonl"
    _write_transcript(p, [{"type": "result", "total_cost_usd": 9.5}])
    assert cost_alert.cost_from_transcript(p) == pytest.approx(9.5)


def test_cost_from_transcript_takes_max_of_incremental_and_cumulative(tmp_path):
    p = tmp_path / "abc.jsonl"
    _write_transcript(p, [
        {"costUSD": 1.0},
        {"costUSD": 2.0},
        {"type": "result", "total_cost_usd": 10.0},
    ])
    # sum of incremental = 3.0; cumulative = 10.0 → max wins
    assert cost_alert.cost_from_transcript(p) == pytest.approx(10.0)


def test_cost_from_transcript_skips_malformed_lines(tmp_path):
    p = tmp_path / "abc.jsonl"
    p.write_text('{"costUSD": 1.0}\nnot-json\n{"costUSD": 2.0}\n')
    assert cost_alert.cost_from_transcript(p) == pytest.approx(3.0)


def test_cost_from_transcript_missing_returns_zero(tmp_path):
    assert cost_alert.cost_from_transcript(tmp_path / "missing.jsonl") == 0.0


def test_cost_from_transcript_finds_nested_usage_dict(tmp_path):
    p = tmp_path / "abc.jsonl"
    _write_transcript(p, [{"message": {"usage": {"costUSD": 4.2}}}])
    assert cost_alert.cost_from_transcript(p) == pytest.approx(4.2)


# ---------- slug / title ----------

def test_slug_from_transcript_returns_last(tmp_path):
    p = tmp_path / "abc.jsonl"
    _write_transcript(p, [
        {"type": "user", "slug": "early-slug"},
        {"type": "assistant", "slug": "later-slug"},
    ])
    assert cost_alert.slug_from_transcript(p) == "later-slug"


def test_slug_from_transcript_returns_none_when_absent(tmp_path):
    p = tmp_path / "abc.jsonl"
    _write_transcript(p, [{"type": "user"}, {"costUSD": 1.0}])
    assert cost_alert.slug_from_transcript(p) is None


def test_slug_from_transcript_missing_file(tmp_path):
    assert cost_alert.slug_from_transcript(tmp_path / "nope.jsonl") is None


def test_slug_from_transcript_skips_empty_strings(tmp_path):
    p = tmp_path / "abc.jsonl"
    _write_transcript(p, [{"slug": "real-slug"}, {"slug": ""}])
    assert cost_alert.slug_from_transcript(p) == "real-slug"


# ---------- session listing ----------

def test_list_tracked_sessions_filters_meta_and_empty(tmp_path):
    sf = tmp_path / "state.json"
    sf.write_text(json.dumps({
        "111": {"session_id": "sid-1", "cwd": "/x", "model": "haiku", "started": True},
        "_meta": {"last_processed_update_id": 5},
        "222": {"cwd": "/y"},  # no session_id → skipped
    }))
    out = cost_alert.list_tracked_sessions(sf)
    assert len(out) == 1
    assert out[0]["session_id"] == "sid-1"


def test_list_tracked_sessions_returns_empty_when_missing(tmp_path):
    assert cost_alert.list_tracked_sessions(tmp_path / "nope.json") == []


# ---------- transcript discovery ----------

def test_find_transcript_globs_subdirs(tmp_path):
    (tmp_path / "proj-a").mkdir()
    (tmp_path / "proj-b").mkdir()
    target = tmp_path / "proj-b" / "sid-xyz.jsonl"
    target.write_text("{}")
    assert cost_alert.find_transcript("sid-xyz", tmp_path) == target


def test_find_transcript_returns_none_when_missing(tmp_path):
    assert cost_alert.find_transcript("nope", tmp_path) is None


# ---------- dedupe ----------

def test_should_notify_first_time(tmp_path):
    assert cost_alert.should_notify("sid-1", {}) is True


def test_should_notify_blocks_same_hour():
    now = datetime(2026, 5, 25, 14, 30, tzinfo=UTC)
    state = {"sid-1": cost_alert.hour_key(now)}
    assert cost_alert.should_notify("sid-1", state, now) is False


def test_should_notify_allows_next_hour():
    earlier = datetime(2026, 5, 25, 14, 30, tzinfo=UTC)
    later = datetime(2026, 5, 25, 15, 5, tzinfo=UTC)
    state = {"sid-1": cost_alert.hour_key(earlier)}
    assert cost_alert.should_notify("sid-1", state, later) is True


def test_alert_state_roundtrip(tmp_path):
    p = tmp_path / "cost-alert-state.json"
    cost_alert.save_alert_state({"sid-1": "2026-05-25-14"}, p)
    assert cost_alert.load_alert_state(p) == {"sid-1": "2026-05-25-14"}


def test_load_alert_state_handles_missing(tmp_path):
    assert cost_alert.load_alert_state(tmp_path / "nope.json") == {}


def test_load_alert_state_handles_corrupt(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json")
    assert cost_alert.load_alert_state(p) == {}


# ---------- run() integration ----------

def _fake_mailer():
    sent: list[tuple[str, str, str]] = []

    def mailer(subject: str, body: str, recipient: str) -> None:
        sent.append((subject, body, recipient))
    return sent, mailer


def _setup(tmp_path, cost_value: float, session_id: str = "sid-1", slug: str | None = None):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "111": {"session_id": session_id, "cwd": "/x", "model": "haiku", "started": True}
    }))
    projects_dir = tmp_path / "projects"
    transcript = projects_dir / "proj-a" / f"{session_id}.jsonl"
    rows: list[dict] = [{"costUSD": cost_value}]
    if slug is not None:
        rows.append({"type": "user", "slug": slug})
    _write_transcript(transcript, rows)
    alert_state_file = tmp_path / "cost-alert-state.json"
    return state_file, projects_dir, alert_state_file


def test_run_disabled_sends_nothing(tmp_path):
    state_file, projects_dir, alert_state_file = _setup(tmp_path, 50.0)
    sent, mailer = _fake_mailer()
    n = cost_alert.run(
        enabled=False, threshold=10.0, recipient="x@y",
        state_file=state_file, projects_dir=projects_dir,
        alert_state_file=alert_state_file, mailer=mailer,
    )
    assert n == 0
    assert sent == []


def test_run_below_threshold_sends_nothing(tmp_path):
    state_file, projects_dir, alert_state_file = _setup(tmp_path, 9.99)
    sent, mailer = _fake_mailer()
    n = cost_alert.run(
        enabled=True, threshold=10.0, recipient="x@y",
        state_file=state_file, projects_dir=projects_dir,
        alert_state_file=alert_state_file, mailer=mailer,
    )
    assert n == 0
    assert sent == []


def test_run_above_threshold_sends_email(tmp_path):
    state_file, projects_dir, alert_state_file = _setup(tmp_path, 10.01)
    sent, mailer = _fake_mailer()
    n = cost_alert.run(
        enabled=True, threshold=10.0, recipient="x@y",
        state_file=state_file, projects_dir=projects_dir,
        alert_state_file=alert_state_file, mailer=mailer,
    )
    assert n == 1
    assert len(sent) == 1
    subject, body, recipient = sent[0]
    assert "10.01" in body
    assert "sid-1" in body
    assert recipient == "x@y"


def test_run_dedupes_within_same_hour(tmp_path):
    state_file, projects_dir, alert_state_file = _setup(tmp_path, 50.0)
    sent, mailer = _fake_mailer()
    now = datetime(2026, 5, 25, 14, 30, tzinfo=UTC)
    n1 = cost_alert.run(
        enabled=True, threshold=10.0, recipient="x@y",
        state_file=state_file, projects_dir=projects_dir,
        alert_state_file=alert_state_file, mailer=mailer, now=now,
    )
    n2 = cost_alert.run(
        enabled=True, threshold=10.0, recipient="x@y",
        state_file=state_file, projects_dir=projects_dir,
        alert_state_file=alert_state_file, mailer=mailer, now=now,
    )
    assert n1 == 1
    assert n2 == 0
    assert len(sent) == 1


def test_run_resends_next_hour(tmp_path):
    state_file, projects_dir, alert_state_file = _setup(tmp_path, 50.0)
    sent, mailer = _fake_mailer()
    t1 = datetime(2026, 5, 25, 14, 30, tzinfo=UTC)
    t2 = datetime(2026, 5, 25, 15, 5, tzinfo=UTC)
    cost_alert.run(
        enabled=True, threshold=10.0, recipient="x@y",
        state_file=state_file, projects_dir=projects_dir,
        alert_state_file=alert_state_file, mailer=mailer, now=t1,
    )
    n2 = cost_alert.run(
        enabled=True, threshold=10.0, recipient="x@y",
        state_file=state_file, projects_dir=projects_dir,
        alert_state_file=alert_state_file, mailer=mailer, now=t2,
    )
    assert n2 == 1
    assert len(sent) == 2


def test_run_includes_slug_title_in_email_body(tmp_path):
    state_file, projects_dir, alert_state_file = _setup(
        tmp_path, 50.0, slug="review-docs-mellow-barto"
    )
    sent, mailer = _fake_mailer()
    cost_alert.run(
        enabled=True, threshold=10.0, recipient="x@y",
        state_file=state_file, projects_dir=projects_dir,
        alert_state_file=alert_state_file, mailer=mailer,
    )
    _, body, _ = sent[0]
    assert "title: review-docs-mellow-barto" in body


def test_run_uses_placeholder_when_slug_absent(tmp_path):
    state_file, projects_dir, alert_state_file = _setup(tmp_path, 50.0)
    sent, mailer = _fake_mailer()
    cost_alert.run(
        enabled=True, threshold=10.0, recipient="x@y",
        state_file=state_file, projects_dir=projects_dir,
        alert_state_file=alert_state_file, mailer=mailer,
    )
    _, body, _ = sent[0]
    assert "title: (untitled)" in body


def test_run_exact_threshold_does_not_fire(tmp_path):
    state_file, projects_dir, alert_state_file = _setup(tmp_path, 10.0)
    sent, mailer = _fake_mailer()
    n = cost_alert.run(
        enabled=True, threshold=10.0, recipient="x@y",
        state_file=state_file, projects_dir=projects_dir,
        alert_state_file=alert_state_file, mailer=mailer,
    )
    assert n == 0
    assert sent == []
