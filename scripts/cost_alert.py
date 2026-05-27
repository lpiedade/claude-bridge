#!/usr/bin/env python3
"""Alert when any active Claude session exceeds a USD cost threshold.

Invoked hourly by launchd. Reads tracked sessions from
``~/.claude-bridge/state.json``, sums per-session cost by parsing the
matching JSONL transcripts in ``~/.claude/projects/<encoded-cwd>/<sid>.jsonl``,
and emails alerts via Mail.app (osascript). Hourly dedupe keyed by
``YYYY-MM-DD-HH`` UTC prevents repeat notifications inside the same window.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import (
    CLAUDE_PROJECTS_DIR,
    COST_ALERT_ENABLED,
    COST_ALERT_RECIPIENT,
    COST_ALERT_THRESHOLD_USD,
)
from integrations.claude_pricing import cost_from_usage
from repositories.session_repository import STATE_FILE

ALERT_STATE_FILE = Path.home() / ".claude-bridge" / "cost-alert-state.json"


def list_tracked_sessions(state_file: Path = STATE_FILE) -> list[dict]:
    """Return [{chat_id, session_id, cwd, model, started}, ...] from state.json."""
    if not state_file.exists():
        return []
    try:
        state = json.loads(state_file.read_text())
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for key, entry in state.items():
        if key.startswith("_"):
            continue
        if not isinstance(entry, dict) or not entry.get("session_id"):
            continue
        out.append(
            {
                "chat_id": key,
                "session_id": entry["session_id"],
                "cwd": entry.get("cwd", ""),
                "model": entry.get("model", ""),
                "started": bool(entry.get("started", False)),
            }
        )
    return out


def find_transcript(session_id: str, projects_dir: Path = CLAUDE_PROJECTS_DIR) -> Path | None:
    """Locate ``<projects_dir>/*/<session_id>.jsonl`` (returns first hit)."""
    if not projects_dir.exists():
        return None
    for candidate in projects_dir.glob(f"*/{session_id}.jsonl"):
        return candidate
    return None


def slug_from_transcript(path: Path) -> str | None:
    """Return the last non-empty ``slug`` field found in the transcript.

    The Claude CLI tags user/assistant rows with a short kebab-case slug
    (e.g. ``review-docs-all-documentation-mellow-barto``). We take the
    last occurrence so renames or refinements over a long session win.
    """
    found: str | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                slug = obj.get("slug") if isinstance(obj, dict) else None
                if isinstance(slug, str) and slug:
                    found = slug
    except FileNotFoundError:
        return None
    return found


def cost_from_transcript(path: Path) -> float:
    """Return total USD cost for a transcript.

    Older transcripts carry ``costUSD`` (incremental) and/or
    ``total_cost_usd`` (cumulative on ``result`` events); newer ones only emit
    ``message.usage`` token counts. We sum incrementals, take the max against
    any cumulative, and — if both are zero — compute from tokens × model
    pricing (see :mod:`integrations.claude_pricing`).
    """
    incremental = 0.0
    cumulative_max = 0.0
    computed = 0.0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                inc = _extract_float(obj, ("costUSD", "cost_usd"))
                if inc is not None:
                    incremental += inc
                cum = _extract_float(obj, ("total_cost_usd",))
                if cum is not None and cum > cumulative_max:
                    cumulative_max = cum
                msg = obj.get("message") if isinstance(obj, dict) else None
                if isinstance(msg, dict):
                    usage = msg.get("usage")
                    if isinstance(usage, dict):
                        computed += cost_from_usage(usage, msg.get("model"))
    except FileNotFoundError:
        return 0.0
    legacy = max(incremental, cumulative_max)
    return legacy if legacy > 0 else computed


def _extract_float(obj, keys: tuple[str, ...]) -> float | None:
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj:
            try:
                return float(obj[k])
            except (TypeError, ValueError):
                return None
    for v in obj.values():
        if isinstance(v, dict):
            r = _extract_float(v, keys)
            if r is not None:
                return r
    return None


def load_alert_state(path: Path = ALERT_STATE_FILE) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def save_alert_state(state: dict, path: Path = ALERT_STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def hour_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d-%H")


def should_notify(session_id: str, alert_state: dict, now: datetime | None = None) -> bool:
    return alert_state.get(session_id) != hour_key(now)


def mark_notified(session_id: str, alert_state: dict, now: datetime | None = None) -> None:
    alert_state[session_id] = hour_key(now)


def send_email(subject: str, body: str, recipient: str) -> None:
    """Send via macOS Mail.app using osascript."""
    script = (
        'tell application "Mail"\n'
        '  set newMessage to make new outgoing message with properties '
        '{{subject: "{subject}", content: "{body}", visible: false}}\n'
        '  tell newMessage\n'
        '    make new to recipient at end of to recipients with properties '
        '{{address: "{recipient}"}}\n'
        '    send\n'
        '  end tell\n'
        'end tell'
    ).format(
        subject=subject.replace('"', r'\"'),
        body=body.replace('"', r'\"').replace("\n", r"\n"),
        recipient=recipient,
    )
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True)


def format_alert(session: dict, cost: float, threshold: float) -> tuple[str, str]:
    subject = f"[claude-bridge] Session over ${threshold:.2f}: ${cost:.2f}"
    body = (
        f"Cost threshold exceeded.\n\n"
        f"title: {session.get('title') or '(untitled)'}\n"
        f"session_id: {session['session_id']}\n"
        f"chat_id: {session['chat_id']}\n"
        f"model: {session.get('model') or '(default)'}\n"
        f"cwd: {session.get('cwd') or '(unknown)'}\n"
        f"cost (USD): {cost:.4f}\n"
        f"threshold (USD): {threshold:.2f}\n"
        f"window: {hour_key()}\n"
    )
    return subject, body


def evaluate_sessions(
    threshold: float = COST_ALERT_THRESHOLD_USD,
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
    state_file: Path = STATE_FILE,
) -> list[dict]:
    """Return list of session dicts that exceed threshold (with cost attached)."""
    over: list[dict] = []
    for session in list_tracked_sessions(state_file):
        transcript = find_transcript(session["session_id"], projects_dir)
        if transcript is None:
            continue
        cost = cost_from_transcript(transcript)
        if cost > threshold:
            session = dict(session)
            session["cost"] = cost
            session["title"] = slug_from_transcript(transcript)
            over.append(session)
    return over


def run(
    threshold: float | None = None,
    recipient: str | None = None,
    enabled: bool | None = None,
    projects_dir: Path | None = None,
    state_file: Path | None = None,
    alert_state_file: Path | None = None,
    mailer=send_email,
    now: datetime | None = None,
) -> int:
    """Main entrypoint. Returns number of alerts sent."""
    if enabled is None:
        enabled = COST_ALERT_ENABLED
    if not enabled:
        return 0
    threshold = threshold if threshold is not None else COST_ALERT_THRESHOLD_USD
    recipient = recipient or COST_ALERT_RECIPIENT
    projects_dir = projects_dir or CLAUDE_PROJECTS_DIR
    state_file = state_file or STATE_FILE
    alert_state_file = alert_state_file or ALERT_STATE_FILE

    alert_state = load_alert_state(alert_state_file)
    over = evaluate_sessions(
        threshold=threshold, projects_dir=projects_dir, state_file=state_file
    )
    sent = 0
    for session in over:
        if not should_notify(session["session_id"], alert_state, now):
            continue
        subject, body = format_alert(session, session["cost"], threshold)
        mailer(subject, body, recipient)
        mark_notified(session["session_id"], alert_state, now)
        sent += 1
    if sent:
        save_alert_state(alert_state, alert_state_file)
    return sent


def main() -> int:
    try:
        sent = run()
    except Exception as e:
        print(f"cost_alert failed: {e}", file=sys.stderr)
        return 1
    print(f"cost_alert: {sent} alert(s) sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
