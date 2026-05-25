"""Deprecated.

The first iteration of `/context` read token usage from the session JSONL
transcript. The current implementation queries the live ``claude /context``
slash command (see :mod:`integrations.claude_context`), which yields the
per-category breakdown the transcript can't expose. This module is kept as a
thin stub so old imports don't break; nothing here is used at runtime.
"""
