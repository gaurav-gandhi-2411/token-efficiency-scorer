from __future__ import annotations

"""tests/test_public_adapter.py — Tests for public_trace_adapter.py.

All tests use synthetic SWE-chat-format fixtures (lists of dicts mimicking
conversations.parquet rows). No dataset download required.

Fixture column names match the inferred schema from the SWE-chat paper
(arXiv:2604.20779, Section 2.1, Appendix C.1):
  - session_id: str
  - turn_id: int          (ordering within session)
  - role: str             ("user" | "assistant" | "tool_use" | "tool_result" | "metadata")
  - turn_type: str | None ("assistant_thinking" marks thinking traces to skip)
  - tool_name: str | None (populated only for role == "tool_use")
  - content: str          (text content)

If the actual downloaded schema differs, update these column names here and in
the adapter constants — the detector logic itself is unaffected.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from public_trace_adapter import (
    _NON_CC_TOOL_MAP,
    adapt_swechat_session,
)
from waste_detectors import detect_redundant_read, detect_repeated_failed_retry

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

SESSION_ID = "test-session-001"

_ERR_SNIP = "exit code 1: command not found: npx playwright"
_TRANSIENT_SNIP = "exit code 1: rate limit exceeded: 429 Too Many Requests"

# Minimum line-numbered content for PATH B: >=80 chars, starts with \d+\t.
_LINE_NUMBERED = (
    "1\tdef authenticate(user, password):\n"
    "2\t    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())\n"
    "3\t    return db.users.find_one({'hash': hashed})\n"
)
assert len(_LINE_NUMBERED.strip()) >= 80, "Fixture must be >=80 chars for PATH B"

# Raw file content WITHOUT the \d+\t prefix — simulates non-CC Read tool output.
_RAW_CONTENT = (
    "def authenticate(user, password):\n"
    "    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt())\n"
    "    return db.users.find_one({'hash': hashed})\n"
    "    # end of function body for line-count padding\n"
)
assert not _RAW_CONTENT.lstrip().startswith("1\t"), "Raw content must not have \\d+\\t prefix"
assert len(_RAW_CONTENT.strip()) >= 80, "Non-CC fixture must be >=80 chars to test PATH B miss"


def _row(
    *,
    session_id: str = SESSION_ID,
    turn_id: int,
    role: str,
    content: str = "",
    tool_name: str | None = None,
    turn_type: str | None = None,
) -> dict:
    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "role": role,
        "content": content,
        "tool_name": tool_name,
        "turn_type": turn_type,
    }


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _adapt_cc(rows: list[dict]) -> list[dict]:
    """Adapt rows as a CC session (no tool name mapping)."""
    record = adapt_swechat_session(SESSION_ID, _df(rows), "claude_code", None)
    return record["digest"]["turns"]


def _adapt_noncc(rows: list[dict]) -> list[dict]:
    """Adapt rows as a non-CC session (with _NON_CC_TOOL_MAP applied)."""
    record = adapt_swechat_session(SESSION_ID, _df(rows), "noncc_agent", _NON_CC_TOOL_MAP)
    return record["digest"]["turns"]


# ---------------------------------------------------------------------------
# Test 1: REPEATED-FAILED-RETRY fires on identical shell errors
# ---------------------------------------------------------------------------


def test_rfr_fires_on_cc_session() -> None:
    """Two consecutive identical Bash errors with no barrier → 1 RFR event."""
    rows = [
        _row(turn_id=0, role="tool_use", tool_name="Bash"),
        _row(turn_id=1, role="tool_result", content=_ERR_SNIP),
        _row(turn_id=2, role="tool_use", tool_name="Bash"),
        _row(turn_id=3, role="tool_result", content=_ERR_SNIP),
    ]
    turns = _adapt_cc(rows)
    events = detect_repeated_failed_retry(SESSION_ID, turns)
    assert len(events) == 1
    assert events[0].detector == "REPEATED-FAILED-RETRY"
    assert events[0].repeat_count == 2


# ---------------------------------------------------------------------------
# Test 2: Write barrier stops REPEATED-FAILED-RETRY run
# ---------------------------------------------------------------------------


def test_rfr_stopped_by_write_barrier() -> None:
    """Bash error, then Write tool call, then same Bash error → 0 events (write barrier)."""
    rows = [
        _row(turn_id=0, role="tool_use", tool_name="Bash"),
        _row(turn_id=1, role="tool_result", content=_ERR_SNIP),
        _row(turn_id=2, role="tool_use", tool_name="Write"),
        _row(turn_id=3, role="tool_use", tool_name="Bash"),
        _row(turn_id=4, role="tool_result", content=_ERR_SNIP),
    ]
    turns = _adapt_cc(rows)
    events = detect_repeated_failed_retry(SESSION_ID, turns)
    assert len(events) == 0


# ---------------------------------------------------------------------------
# Test 3: PATH B fires on native CC line-numbered Read output
# ---------------------------------------------------------------------------


def test_rr_path_b_fires_on_native_cc_read_format() -> None:
    r"""Two CC Read results with identical \d+\t-prefixed content within <=5 gap -> PATH B event."""
    rows = [
        _row(turn_id=0, role="tool_use", tool_name="Read"),
        _row(turn_id=1, role="tool_result", content=_LINE_NUMBERED),
        _row(turn_id=2, role="tool_use", tool_name="Read"),
        _row(turn_id=3, role="tool_result", content=_LINE_NUMBERED),
    ]
    turns = _adapt_cc(rows)
    events = detect_redundant_read(SESSION_ID, turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert len(path_b) >= 1, f"Expected PATH B event; got events: {events}"
    # Gap = call_2.turn_index - result_1.turn_index = 2 - 1 = 1 ≤ 5
    assert path_b[0].evidence["gap"] <= 5


# ---------------------------------------------------------------------------
# Test 4: PATH B unavailable on non-CC raw content (no \d+\t prefix)
# ---------------------------------------------------------------------------


def test_rr_path_b_unavailable_on_noncc_format() -> None:
    r"""Non-CC Read results with raw content (no \d+\t prefix) -> 0 PATH B events.

    The _is_line_numbered_content check in the detector requires ^\d+\t matching.
    Raw file content from non-CC Read tools does not match, so PATH B does not fire.
    This is a research finding: PATH B is CC-specific.
    """
    rows = [
        _row(turn_id=0, role="tool_use", tool_name="read_file"),
        _row(turn_id=1, role="tool_result", content=_RAW_CONTENT),
        _row(turn_id=2, role="tool_use", tool_name="read_file"),
        _row(turn_id=3, role="tool_result", content=_RAW_CONTENT),
    ]
    turns = _adapt_noncc(rows)
    # After mapping: read_file → Read, so _is_read_call fires (role="ai", "Read" in tool_names)
    # but the content_snippet lacks \d+\t prefix → _is_line_numbered_content → False → no PATH B
    events = detect_redundant_read(SESSION_ID, turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert len(path_b) == 0, f"PATH B must not fire on non-CC raw content; got: {path_b}"


# ---------------------------------------------------------------------------
# Test 5: PATH A fires on native CC "File unchanged" hint
# ---------------------------------------------------------------------------


def test_rr_path_a_fires_on_native_cc_content() -> None:
    """CC Read result starting with 'File unchanged since last read' → PATH A event.

    Validates that CC Read results pass through content_snippet verbatim so that
    PATH A (tool-authoritative verdict) works natively on the adapted data.
    """
    unchanged_snip = "File unchanged since last read (/src/auth.py)"
    rows = [
        _row(turn_id=0, role="tool_use", tool_name="Read"),
        _row(turn_id=1, role="tool_result", content=unchanged_snip),
    ]
    turns = _adapt_cc(rows)
    events = detect_redundant_read(SESSION_ID, turns)
    path_a = [e for e in events if e.evidence.get("path") == "A"]
    assert len(path_a) == 1, f"Expected 1 PATH A event; got events: {events}"
    assert path_a[0].evidence["content_snippet"].startswith("File unchanged since last read")


# ---------------------------------------------------------------------------
# Test 6: Transient errors are excluded from REPEATED-FAILED-RETRY
# ---------------------------------------------------------------------------


def test_transient_error_excluded() -> None:
    """Two identical rate-limit errors → 0 events (transient pattern excluded unconditionally)."""
    rows = [
        _row(turn_id=0, role="tool_use", tool_name="Bash"),
        _row(turn_id=1, role="tool_result", content=_TRANSIENT_SNIP),
        _row(turn_id=2, role="tool_use", tool_name="Bash"),
        _row(turn_id=3, role="tool_result", content=_TRANSIENT_SNIP),
    ]
    turns = _adapt_cc(rows)
    events = detect_repeated_failed_retry(SESSION_ID, turns)
    assert len(events) == 0, f"Transient error must not fire RFR; got: {events}"
