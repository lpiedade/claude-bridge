"""Parse a Claude CLI transcript into ordered user↔assistant turn pairs.

Used by both `/history` (compact preview in Telegram) and `/export`
(full markdown attachment). Walks the same JSONL the `/usage` parser does,
but emits :class:`Turn` instead of token aggregates.

A "turn" here is the operator-visible round-trip: the user's typed prompt
plus every assistant text block that lands before the next user prompt.
Tool calls / tool results / thinking blocks are not exposed — they are
intermediate state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Turn:
    prompt_id: str
    timestamp: datetime | None
    user_text: str
    assistant_text: str = ""
    assistant_blocks: list[str] = field(default_factory=list)

    def finalize(self) -> None:
        self.assistant_text = "\n\n".join(b for b in self.assistant_blocks if b.strip())


def parse_session_turns(path: Path) -> list[Turn]:
    """Return turns in chronological order; empty list if the file is missing."""
    if not path.exists():
        return []

    turns: list[Turn] = []
    current: Turn | None = None
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            t = obj.get("type")
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else None
            if msg is None:
                continue

            if t == "user" and obj.get("promptId") and "toolUseResult" not in obj:
                text = _extract_user_text(msg)
                if text is None:
                    continue
                if current is not None:
                    current.finalize()
                current = Turn(
                    prompt_id=obj["promptId"],
                    timestamp=_parse_ts(obj.get("timestamp")),
                    user_text=text,
                )
                turns.append(current)
                continue

            if t == "assistant" and current is not None:
                block = _extract_assistant_text(msg)
                if block:
                    current.assistant_blocks.append(block)

    if current is not None:
        current.finalize()
    for t in turns:
        if not t.assistant_text:
            t.finalize()
    return turns


def _extract_user_text(msg: dict) -> str | None:
    """User content can be a plain string or a list of content blocks.

    Returns None if the message is a synthetic tool-result re-entry (those
    arrive as `type=user` with a structured content list but never an
    operator-typed string).
    """
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts) if parts else None
    return None


def _extract_assistant_text(msg: dict) -> str:
    content = msg.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            t = block.get("text")
            if isinstance(t, str):
                parts.append(t)
    return "\n".join(parts)


def _parse_ts(s) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
