"""Wrapper around the local `claude` CLI."""
from __future__ import annotations

import json


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
