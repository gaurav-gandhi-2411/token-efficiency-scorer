from __future__ import annotations

"""tests/test_adapt_edit_operations.py — XX2.2: adapt_session extracts
edit operations from real tool_use.input payloads in the same single pass
that builds the rest of the record (RR1 lesson -- this data is only
readable while the source transcript exists).

Same synthetic-JSONL fixture pattern as test_server_tool_warning.py.
"""

import json
from pathlib import Path

from tes.adapt import adapt_session


def _write_session_jsonl(path: Path, assistant_content: list[dict]) -> None:
    lines = [
        {
            "type": "user",
            "isSidechain": False,
            "message": {"role": "user", "content": "Fix the bug in foo.py."},
        },
        {
            "type": "assistant",
            "isSidechain": False,
            "message": {
                "model": "claude-sonnet-4-6",
                "content": assistant_content,
                "usage": {
                    "input_tokens": 100, "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0, "output_tokens": 50,
                },
            },
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def test_adapt_session_extracts_edit_tool_call(tmp_path: Path) -> None:
    session_path = tmp_path / "edit-session.jsonl"
    _write_session_jsonl(session_path, [
        {"type": "text", "text": "Fixing it."},
        {
            "type": "tool_use", "name": "Edit", "id": "t1",
            "input": {"file_path": "/repo/foo.py", "old_string": "bug\n", "new_string": "fix\nfix2\n"},
        },
    ])

    record = adapt_session(session_path)

    assert "edit_operations" in record
    ops = record["edit_operations"]
    assert len(ops) == 1
    assert ops[0]["path"] == "/repo/foo.py"
    assert ops[0]["additions"] == 2
    assert ops[0]["deletions"] == 1
    assert ops[0]["prior_content_unknown"] is False


def test_adapt_session_extracts_write_tool_call(tmp_path: Path) -> None:
    session_path = tmp_path / "write-session.jsonl"
    _write_session_jsonl(session_path, [
        {
            "type": "tool_use", "name": "Write", "id": "t1",
            "input": {"file_path": "/repo/new.py", "content": "a\nb\nc\n"},
        },
    ])

    record = adapt_session(session_path)
    ops = record["edit_operations"]
    assert len(ops) == 1
    assert ops[0]["additions"] == 3
    assert ops[0]["prior_content_unknown"] is True


def test_adapt_session_with_no_edit_tools_returns_empty_list_not_none(tmp_path: Path) -> None:
    """Distinguishes 'ran, made no edits' ([]) from 'never ran this
    extraction at all' (a missing key or None) -- see tes.score's docstring
    on edit_operations for why this distinction matters for legacy rows."""
    session_path = tmp_path / "read-only-session.jsonl"
    _write_session_jsonl(session_path, [
        {"type": "tool_use", "name": "Read", "id": "t1", "input": {"file_path": "/repo/foo.py"}},
    ])

    record = adapt_session(session_path)
    assert record["edit_operations"] == []


def test_adapt_session_multiple_edits_across_turns_all_captured(tmp_path: Path) -> None:
    session_path = tmp_path / "multi-turn.jsonl"
    lines = [
        {
            "type": "assistant", "isSidechain": False,
            "message": {
                "model": "claude-sonnet-4-6",
                "content": [{
                    "type": "tool_use", "name": "Edit", "id": "t1",
                    "input": {"file_path": "/repo/a.py", "old_string": "x", "new_string": "y"},
                }],
                "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 0, "output_tokens": 5},
            },
        },
        {
            "type": "assistant", "isSidechain": False,
            "message": {
                "model": "claude-sonnet-4-6",
                "content": [{
                    "type": "tool_use", "name": "Edit", "id": "t2",
                    "input": {"file_path": "/repo/b.py", "old_string": "p", "new_string": "q\nr"},
                }],
                "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 0, "output_tokens": 5},
            },
        },
    ]
    session_path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    record = adapt_session(session_path)
    ops = record["edit_operations"]
    assert len(ops) == 2
    assert {op["path"] for op in ops} == {"/repo/a.py", "/repo/b.py"}
