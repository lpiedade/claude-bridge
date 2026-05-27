"""Wrapper around the local `claude` CLI."""
from __future__ import annotations

import json
import subprocess

from core.config import CLAUDE_BIN, PERMISSION_MODE, TIMEOUT_SECONDS


def build_command(
    prompt: str,
    session_id: str,
    *,
    effort: str | None,
    model: str | None,
    started: bool,
    allowed_tools: list[str] | None = None,
) -> list[str]:
    cmd = [
        CLAUDE_BIN,
        "-p", prompt,
        "--permission-mode", PERMISSION_MODE,
        "--output-format", "json",
    ]
    if effort:
        cmd += ["--effort", effort]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", *allowed_tools]
    if started:
        cmd += ["--resume", session_id]
    else:
        cmd += ["--session-id", session_id]
    return cmd


def run_claude(
    prompt: str,
    session_id: str,
    cwd: str,
    *,
    effort: str | None,
    model: str | None,
    started: bool,
    timeout: int = TIMEOUT_SECONDS,
    allowed_tools: list[str] | None = None,
) -> subprocess.CompletedProcess:
    cmd = build_command(
        prompt, session_id,
        effort=effort, model=model, started=started,
        allowed_tools=allowed_tools,
    )
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def extract_result_text(stdout: str) -> str:
    """Pull the `result` field from `claude --output-format json` stdout.

    Accepts both shapes the CLI may emit (single object, or a list of events)
    and falls back to the raw stdout when no `result` is found.
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if isinstance(payload, dict):
        return payload.get("result", stdout)
    if isinstance(payload, list):
        for item in reversed(payload):
            if isinstance(item, dict) and "result" in item:
                return item["result"]
    return stdout


def extract_permission_denials(stdout: str) -> list[dict]:
    """Return any `permission_denials` entries reported by the CLI.

    The CLI surfaces denied tool calls in the final `result` event under
    `permission_denials: [{tool_name, tool_use_id, tool_input}]`. Accepts both
    the single-object and list-of-events stdout shapes; returns [] if absent
    or malformed.
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    candidates: list[dict] = []
    if isinstance(payload, dict):
        candidates.append(payload)
    elif isinstance(payload, list):
        candidates.extend(p for p in payload if isinstance(p, dict))
    for item in reversed(candidates):
        denials = item.get("permission_denials")
        if isinstance(denials, list):
            return [d for d in denials if isinstance(d, dict)]
    return []
