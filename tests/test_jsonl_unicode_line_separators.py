from __future__ import annotations

"""A raw U+2028/U+0085 inside a JSON string must not split (and so drop) a JSONL record.

JSONL lines end only at ``\n``. ``str.splitlines()`` also splits on U+2028, U+2029, U+0085,
``\x0b``, ``\x0c`` and ``\x1c``-``\x1e``, which silently lost any record containing one raw.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from tes._digest import reconstruct_digest
from tes.adapt import _read_jsonl, adapt_session
from tes.store import _count_turns_from_jsonl

SEPARATORS = ["\u2028", "\u2029", "\u0085"]  # \x0b-\x1e are escaped by json.dumps


def _assistant(n: int, text: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "isSidechain": False,
        "uuid": f"u{n}",
        "requestId": f"req-{n}",
        "message": {
            "id": f"msg_{n}",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": text}],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    }


def _session(tmp_path: Path, sep: str) -> Path:
    rows = [_assistant(1, "plain"), _assistant(2, f"a{sep}b"), _assistant(3, "plain")]
    path = tmp_path / "s.jsonl"
    # ensure_ascii=False keeps the separator raw, as Claude Code writes it.
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    return path


@pytest.mark.parametrize("sep", SEPARATORS)
def test_read_jsonl_keeps_record_with_raw_separator(tmp_path: Path, sep: str) -> None:
    rows = _read_jsonl(_session(tmp_path, sep))
    assert [r["uuid"] for r in rows] == ["u1", "u2", "u3"]


@pytest.mark.parametrize("sep", ["\u2028", "\u0085"])
def test_adapt_session_counts_usage_of_record_with_raw_separator(tmp_path: Path, sep: str) -> None:
    record = adapt_session(_session(tmp_path, sep))
    digest = reconstruct_digest(record["digest"])
    assert sum(t.token_count_output for t in digest.turns) == 60
    assert _count_turns_from_jsonl(str(_session(tmp_path, sep))) == 3


def test_crlf_blank_lines_and_malformed_lines_still_tolerated(tmp_path: Path) -> None:
    good = json.dumps(_assistant(1, "x"), ensure_ascii=False)
    path = tmp_path / "t.jsonl"
    path.write_bytes((good + "\r\n\r\n{not json\r\n" + good + "\r\n").encode("utf-8"))
    assert len(_read_jsonl(path)) == 2
