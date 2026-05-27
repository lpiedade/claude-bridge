"""In-memory store of pending Telegram approvals for denied tool calls.

When the Claude CLI denies a tool call under ``--permission-mode default``,
``message.on_message`` parks the original prompt + denial list here under a
short id, posts an inline keyboard, and returns. The :func:`cmd_approval`
callback handler looks the id up, retries with augmented ``--allowedTools``
on Approve, or discards on Reject.

Entries expire after ``APPROVAL_TTL_SECONDS`` so a forgotten denial doesn't
pin memory or leak prompts indefinitely.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

APPROVAL_TTL_SECONDS = 30 * 60


def _shell_quote(s: str) -> str:
    if not s:
        return "''"
    if all(c.isalnum() or c in "@%+=:,./-_" for c in s):
        return s
    return "'" + s.replace("'", "'\\''") + "'"


def allowed_tool_spec(denial: dict) -> str:
    """Build the narrowest ``--allowedTools`` token for a single denial.

    For Bash: ``Bash(<exact command>)`` — restricts approval to that command.
    For other tools: just the tool name (Edit/Write/etc are coarse-grained
    in the CLI anyway).
    """
    name = denial.get("tool_name") or ""
    if name == "Bash":
        cmd = (denial.get("tool_input") or {}).get("command", "")
        return f"Bash({_shell_quote(cmd)})"
    return name


@dataclass
class PendingApproval:
    chat_id: int
    prompt: str
    denials: list[dict]
    created_at: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.created_at) > APPROVAL_TTL_SECONDS

    def allowed_tools(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for d in self.denials:
            spec = allowed_tool_spec(d)
            if spec and spec not in seen:
                seen.add(spec)
                out.append(spec)
        return out


_pending: dict[str, PendingApproval] = {}


def register(chat_id: int, prompt: str, denials: list[dict]) -> str:
    _gc()
    token = secrets.token_urlsafe(9)
    _pending[token] = PendingApproval(chat_id=chat_id, prompt=prompt, denials=list(denials))
    return token


def claim(token: str) -> PendingApproval | None:
    """Pop the pending entry. Returns None if missing or expired."""
    _gc()
    entry = _pending.pop(token, None)
    if entry is None:
        return None
    if entry.expired:
        return None
    return entry


def _gc() -> None:
    expired_keys = [k for k, v in _pending.items() if v.expired]
    for k in expired_keys:
        _pending.pop(k, None)
