"""Parser + rendering tests for /history and /export."""
from __future__ import annotations

import json
from pathlib import Path

from integrations.claude_history import parse_session_turns


def _row(t: str, **kw) -> dict:
    base = {"type": t}
    base.update(kw)
    return base


def _user(prompt_id: str, text: str, ts: str = "2026-05-26T10:00:00Z") -> dict:
    return _row(
        "user",
        promptId=prompt_id,
        timestamp=ts,
        message={"role": "user", "content": text},
    )


def _tool_result_user(text: str, ts: str = "2026-05-26T10:00:30Z") -> dict:
    # Synthetic tool-result re-entry — no promptId, has toolUseResult.
    return _row(
        "user",
        timestamp=ts,
        toolUseResult={"output": text},
        message={"role": "user", "content": [{"type": "tool_result", "content": text}]},
    )


def _assistant(blocks: list[dict], ts: str = "2026-05-26T10:00:05Z") -> dict:
    return _row(
        "assistant",
        timestamp=ts,
        message={"model": "claude-haiku-4-5", "content": blocks},
    )


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows))


def test_parser_groups_assistant_blocks_under_their_prompt(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _user("p1", "hello"),
        _assistant([{"type": "thinking", "thinking": "..."}]),
        _assistant([{"type": "text", "text": "Hi there!"}]),
        _user("p2", "what is 2+2?", ts="2026-05-26T10:01:00Z"),
        _assistant([{"type": "text", "text": "4"}], ts="2026-05-26T10:01:05Z"),
    ])
    turns = parse_session_turns(p)
    assert len(turns) == 2
    assert turns[0].user_text == "hello"
    assert turns[0].assistant_text == "Hi there!"
    assert turns[1].user_text == "what is 2+2?"
    assert turns[1].assistant_text == "4"


def test_parser_ignores_tool_result_reentries(tmp_path: Path):
    """type=user rows that are tool_result echoes must not open a new turn."""
    p = tmp_path / "s.jsonl"
    _write(p, [
        _user("p1", "list files"),
        _assistant([{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]),
        _tool_result_user("a.txt\nb.txt"),
        _assistant([{"type": "text", "text": "Two files."}]),
    ])
    turns = parse_session_turns(p)
    assert len(turns) == 1
    assert turns[0].user_text == "list files"
    assert turns[0].assistant_text == "Two files."


def test_parser_handles_user_content_as_list(tmp_path: Path):
    """The CLI sometimes wraps user text inside a content[] block."""
    p = tmp_path / "s.jsonl"
    rows = [{
        "type": "user", "promptId": "p1", "timestamp": "2026-05-26T10:00:00Z",
        "message": {"role": "user", "content": [{"type": "text", "text": "wrapped prompt"}]},
    }, _assistant([{"type": "text", "text": "ok"}])]
    _write(p, rows)
    turns = parse_session_turns(p)
    assert len(turns) == 1
    assert turns[0].user_text == "wrapped prompt"


def test_parser_concatenates_multiple_assistant_text_blocks(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    _write(p, [
        _user("p1", "do two things"),
        _assistant([{"type": "text", "text": "first"}]),
        _assistant([
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "text", "text": "second"},
        ]),
    ])
    turns = parse_session_turns(p)
    assert len(turns) == 1
    assert "first" in turns[0].assistant_text
    assert "second" in turns[0].assistant_text


def test_parser_missing_file_returns_empty(tmp_path: Path):
    assert parse_session_turns(tmp_path / "nope.jsonl") == []


def test_parser_skips_malformed_json_lines(tmp_path: Path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join([
        "not-json",
        json.dumps(_user("p1", "hi")),
        "{broken",
        json.dumps(_assistant([{"type": "text", "text": "hello"}])),
    ]))
    turns = parse_session_turns(p)
    assert len(turns) == 1
    assert turns[0].assistant_text == "hello"


def test_history_format_truncates_long_bodies():
    from integrations.claude_history import Turn
    from service.handlers.history import SNIPPET_CHARS, _format_turn
    # Spaces in the body prevent the redactor's hex pattern (≥32 alnum) from
    # eating the test input.
    long_user = ("word " * ((SNIPPET_CHARS + 100) // 5 + 1))[: SNIPPET_CHARS + 100]
    turn = Turn(
        prompt_id="p1",
        timestamp=None,
        user_text=long_user,
        assistant_text="ok",
    )
    out = _format_turn(turn, 1)
    # `_truncate` may `rstrip()` a trailing space so the reported overflow can
    # be ≥99 instead of exactly 100. Tolerant assertion.
    import re
    m = re.search(r"\[\+(\d+) chars\]", out)
    assert m is not None, out
    assert 95 <= int(m.group(1)) <= 105
    assert out.startswith("#1 · ⏱ ?")


def test_export_markdown_includes_required_sections(tmp_path: Path):
    from integrations.claude_history import Turn
    from integrations.claude_usage import SessionUsage
    from service.handlers.export import _render_markdown

    turns = [
        Turn(prompt_id="p1", timestamp=None, user_text="hi", assistant_text="hello"),
    ]
    usage = SessionUsage(
        session_id="s", model="claude-haiku-4-5",
        turns=3, input_tokens=10, output_tokens=20,
        cache_read_tokens=0, cache_write_tokens=0, total_cost_usd=0.0012,
    )
    md = _render_markdown("abc123", "claude-haiku-4-5", turns, usage)
    assert "# Claude session `abc123`" in md
    assert "Operator turns: 1" in md
    assert "CLI turns (incl. tool calls): 3" in md
    assert "$0.0012" in md
    assert "**You:**" in md
    assert "hi" in md
    assert "**Claude:**" in md
    assert "hello" in md
