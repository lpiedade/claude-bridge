"""Redact sensitive substrings from log/error output before user display."""
from __future__ import annotations

import re
from pathlib import Path

_HOME = str(Path.home())
_PATTERNS = [
    (re.compile(re.escape(_HOME)), "~"),
    (re.compile(r"/Users/[^/\s]+"), "/Users/<user>"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<email>"),
    (re.compile(r"\b[0-9a-f]{32,}\b"), "<hex>"),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "<api-key>"),
]


def redact(s: str) -> str:
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s
