"""Tests for the /context CLI-output parser and PNG renderer."""
from __future__ import annotations

from integrations.claude_context import (
    CategoryRow,
    ContextUsage,
    _parse_pct,
    _parse_token_count,
    parse_context_markdown,
)
from integrations.claude_context_render import _model_display_name, render_context_png

SAMPLE = """## Context Usage

**Model:** claude-opus-4-7
**Tokens:** 62k / 1m (6.2%)

### Estimated usage by category

| Category | Tokens | Percentage |
|----------|--------|------------|
| System prompt | 9k | 0.9% |
| System tools | 14.5k | 1.5% |
| Memory files | 545 | 0.1% |
| Skills | 1.3k | 0.1% |
| Messages | 38.6k | 3.9% |
| Free space | 903k | 90.3% |
| Autocompact buffer | 33k | 3.3% |

### Memory Files

| Type | Path | Tokens |
|------|------|--------|
| User | ~/.claude/CLAUDE.md | 376 |
"""


def test_parse_token_count_handles_suffixes():
    assert _parse_token_count("62k") == 62_000
    assert _parse_token_count("1m") == 1_000_000
    assert _parse_token_count("14.5k") == 14_500
    assert _parse_token_count("545") == 545
    assert _parse_token_count("< 20") == 20
    assert _parse_token_count("~100") == 100
    assert _parse_token_count("") == 0


def test_parse_pct():
    assert _parse_pct("3.3%") == 3.3
    assert _parse_pct("0.1 %") == 0.1
    assert _parse_pct("garbage") == 0.0


def test_parse_context_markdown_extracts_model_and_totals():
    usage = parse_context_markdown(SAMPLE)
    assert usage.model == "claude-opus-4-7"
    assert usage.used == 62_000
    assert usage.total == 1_000_000
    assert usage.used_pct == 6.2


def test_parse_context_markdown_extracts_categories_only():
    usage = parse_context_markdown(SAMPLE)
    names = [c.name for c in usage.categories]
    assert names == [
        "System prompt", "System tools", "Memory files", "Skills",
        "Messages", "Free space", "Autocompact buffer",
    ]
    by_name = {c.name: c for c in usage.categories}
    assert by_name["Memory files"].tokens == 545
    assert by_name["Free space"].pct == 90.3
    assert by_name["Autocompact buffer"].tokens == 33_000


def test_parse_context_markdown_ignores_secondary_tables():
    usage = parse_context_markdown(SAMPLE)
    assert all("CLAUDE.md" not in c.name for c in usage.categories)


def test_model_display_name_friendly_mapping():
    assert _model_display_name("claude-haiku-4-5-20251001") == "Haiku 4.5"
    assert _model_display_name("claude-opus-4-7") == "Opus 4.7"
    assert _model_display_name("claude-sonnet-4-6") == "Sonnet 4.6"
    assert _model_display_name("some-future-model") == "some-future-model"


def test_render_context_png_produces_non_empty_png():
    usage = ContextUsage(
        model="claude-haiku-4-5-20251001",
        used=36_300, total=200_000, used_pct=18.1,
        categories=[
            CategoryRow("System prompt", 6_600, 3.3),
            CategoryRow("System tools", 21_900, 10.9),
            CategoryRow("MCP tools", 289, 0.1),
            CategoryRow("Memory files", 382, 0.2),
            CategoryRow("Skills", 986, 0.5),
            CategoryRow("Messages", 6_200, 3.1),
            CategoryRow("Free space", 130_700, 65.4),
            CategoryRow("Autocompact buffer", 33_000, 16.5),
        ],
    )
    png = render_context_png(usage)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1_000
