from __future__ import annotations

"""tests/test_waste_detectors.py — Unit tests for waste_detectors.py.

All tests use crafted fixtures (fake turn sequences); no pool data, no LLM inference.
Each fixture is the minimal digest slice needed to prove one behavioural property.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from waste_detectors import WasteEvent, detect_redundant_read, detect_repeated_failed_retry

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

ERR = "Exit code 1\nERROR: cannot find module 'playwright-core'\nRequire stack: /workspace/[eval]"
ERR_TRANSIENT = "Exit code 1\nERROR: ZONE_RESOURCE_POOL_EXHAUSTED_WITH_DETAILS — no capacity in zone us-central1-a"
ERR_DIFFERENT = "Exit code 1\nERROR: cannot find module 'axios'\nRequire stack: /workspace/[eval]"
ERR_SHORT = "Exit code 1"                    # < 20 chars with strip → not a qualifying error
ERR_GREP = "grep: scraper/app.js: No such file or directory"
ERR_GIT = "fatal: expected 'acknowledgments'"

STATE_INSTALL = "Successfully installed playwright-core 1.60.0"
STATE_GIT = "HEAD is now at abc1234 revert to previous state"
STATE_FILE = "The file /workspace/index.html has been updated successfully"


def _t(idx: int, role: str, tools: list[str] | None = None, snippet: str = "") -> dict:
    """Build a minimal digest turn dict."""
    return {
        "turn_index": idx,
        "role": role,
        "tool_names": tools or [],
        "content_snippet": snippet,
    }


def _ai(idx: int, tools: list[str] | None = None, snippet: str = "") -> dict:
    return _t(idx, "ai", tools, snippet)


def _tool(idx: int, snippet: str = "") -> dict:
    return _t(idx, "tool", snippet=snippet)


def _user(idx: int, snippet: str = "do something else") -> dict:
    return _t(idx, "user", snippet=snippet)


# ---------------------------------------------------------------------------
# Fires: basic cases
# ---------------------------------------------------------------------------


def test_fires_on_two_identical_failures() -> None:
    """Two consecutive Bash calls produce identical errors with no barrier → one event."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, ["Bash"]),
        _tool(3, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert len(events) == 1
    e = events[0]
    assert e.detector == "REPEATED-FAILED-RETRY"
    assert e.repeat_count == 2
    assert e.turns == [0, 1, 2, 3]
    assert e.evidence["error_snippet"] == ERR
    assert e.evidence["turns_gap"] == 3


def test_fires_once_with_count_on_three_identical_failures() -> None:
    """Three consecutive identical failures emit ONE event with repeat_count=3, not two events."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, ["Bash"]),
        _tool(3, ERR),
        _ai(4, ["Bash"]),
        _tool(5, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert len(events) == 1
    e = events[0]
    assert e.repeat_count == 3
    assert e.turns == [0, 1, 2, 3, 4, 5]


def test_fires_with_reasoning_text_turns_between() -> None:
    """Non-tool, non-shell ai turns (reasoning text) between failures do not end a run."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, [], snippet="Hmm, that failed. Let me try again."),  # text-only turn
        _ai(3, ["Bash"]),
        _tool(4, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert len(events) == 1
    assert events[0].repeat_count == 2
    assert events[0].turns == [0, 1, 3, 4]


def test_fires_with_read_tool_between() -> None:
    """Read tool (read-only) between failures does not end a run."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, ["Read"]),         # read-only: not a barrier
        _tool(3, "file contents"),
        _ai(4, ["Bash"]),
        _tool(5, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert len(events) == 1
    assert events[0].repeat_count == 2


def test_fires_on_grep_no_such_file() -> None:
    """grep 'No such file' pattern (no 'exit code' prefix) triggers correctly."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR_GREP),
        _ai(2, ["Bash"]),
        _tool(3, ERR_GREP),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert len(events) == 1
    assert events[0].evidence["error_snippet"] == ERR_GREP


def test_fires_on_git_fatal() -> None:
    """git 'fatal:' errors trigger the detector."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR_GIT),
        _ai(2, ["Bash"]),
        _tool(3, ERR_GIT),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert len(events) == 1


def test_fires_on_powershell_tool() -> None:
    """PowerShell tool calls are treated the same as Bash."""
    turns = [
        _ai(0, ["PowerShell"]),
        _tool(1, ERR),
        _ai(2, ["PowerShell"]),
        _tool(3, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert len(events) == 1


def test_two_separate_runs_emit_two_events() -> None:
    """Two distinct retry loops in the same session emit separate events."""
    ERR_B = "Exit code 127\nBash: pip: command not found in this container"
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, ["Bash"]),
        _tool(3, ERR),
        # state change breaks first run
        _ai(4, ["Write"]),
        _tool(5, "written successfully"),
        # second independent run
        _ai(6, ["Bash"]),
        _tool(7, ERR_B),
        _ai(8, ["Bash"]),
        _tool(9, ERR_B),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert len(events) == 2
    assert events[0].evidence["error_snippet"] == ERR
    assert events[1].evidence["error_snippet"] == ERR_B


# ---------------------------------------------------------------------------
# Does NOT fire: state-change barriers
# ---------------------------------------------------------------------------


def test_no_fire_with_write_between() -> None:
    """Write tool between identical failures: state changed → no fire."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, ["Write"]),        # Write barrier
        _ai(3, ["Bash"]),
        _tool(4, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_with_edit_between() -> None:
    """Edit tool between identical failures: state changed → no fire."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, ["Edit"]),
        _ai(3, ["Bash"]),
        _tool(4, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_with_notebook_edit_between() -> None:
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, ["NotebookEdit"]),
        _ai(3, ["Bash"]),
        _tool(4, ERR),
    ]
    assert detect_repeated_failed_retry("s1", turns) == []


def test_no_fire_with_bash_install_success_between() -> None:
    """Tool result showing 'Successfully installed' = Bash state change → no fire."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, ["Bash"]),         # e.g. pip install playwright-core
        _tool(3, STATE_INSTALL),  # install succeeded (state mutated)
        _ai(4, ["Bash"]),
        _tool(5, ERR),            # still fails (bad install), but state DID change
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_with_git_state_change_between() -> None:
    """'HEAD is now at' in tool result signals git-based state mutation → no fire."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, ["Bash"]),
        _tool(3, STATE_GIT),
        _ai(4, ["Bash"]),
        _tool(5, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_with_file_write_confirmation_between() -> None:
    """CC file-write confirmation in tool result = state changed → no fire."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, ["Bash"]),
        _tool(3, STATE_FILE),
        _ai(4, ["Bash"]),
        _tool(5, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_with_user_turn_between() -> None:
    """Human (user) message between failures = new context, barrier → no fire."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _user(2),
        _ai(3, ["Bash"]),
        _tool(4, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


# ---------------------------------------------------------------------------
# Does NOT fire: error conditions
# ---------------------------------------------------------------------------


def test_no_fire_on_transient_zone_exhaustion() -> None:
    """ZONE_RESOURCE_POOL_EXHAUSTED is a transient error → no fire even if repeated."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR_TRANSIENT),
        _ai(2, ["Bash"]),
        _tool(3, ERR_TRANSIENT),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_on_rate_limit_error() -> None:
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, "Exit code 1\n429 Too Many Requests — rate limit exceeded for this endpoint"),
        _ai(2, ["Bash"]),
        _tool(3, "Exit code 1\n429 Too Many Requests — rate limit exceeded for this endpoint"),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_on_gh_pr_checks_pending() -> None:
    """gh pr checks returns exit code 8 when checks are pending (CI-polling, not a fixable failure)."""
    gh_pending = "Exit code 8\nAPI (Python 3.12)\tpending\t0\thttps://github.com/org/repo/actions/runs/123"
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, gh_pending),
        _ai(2, ["Bash"]),
        _tool(3, gh_pending),
        _ai(4, ["Bash"]),
        _tool(5, gh_pending),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_on_gh_pr_checks_no_checks_reported() -> None:
    """gh pr checks returns exit code 1 + 'no checks reported' when CI hasn't started yet (CI-polling)."""
    gh_no_checks = "Exit code 1\nno checks reported on the 'feat/my-feature' branch"
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, gh_no_checks),
        _ai(2, ["Bash"]),
        _tool(3, gh_no_checks),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_on_quota_exceeded() -> None:
    turns = [
        _ai(0, ["PowerShell"]),
        _tool(1, "Exit code 1\nERROR: QUOTA_EXCEEDED: Quota limit reached for resource compute"),
        _ai(2, ["PowerShell"]),
        _tool(3, "Exit code 1\nERROR: QUOTA_EXCEEDED: Quota limit reached for resource compute"),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_on_single_failure() -> None:
    """One failure, no repeat → not a waste event."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),
        _ai(2, ["Bash"]),
        _tool(3, "All tests passed"),  # different result
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_on_different_error_content() -> None:
    """Same error prefix but different full snippet (different module) → no fire.
    This tests the near-miss case where a naive prefix match would fire but exact
    match correctly does not (e.g. 'cannot find module playwright' vs 'cannot find module axios').
    """
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR),          # playwright-core
        _ai(2, ["Bash"]),
        _tool(3, ERR_DIFFERENT), # axios — different full snippet
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_on_trivially_short_error() -> None:
    """Error snippet shorter than 20 chars is not a qualifying error."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, ERR_SHORT),   # 'Exit code 1' = 11 chars
        _ai(2, ["Bash"]),
        _tool(3, ERR_SHORT),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


def test_no_fire_on_empty_turns() -> None:
    assert detect_repeated_failed_retry("s1", []) == []


def test_no_fire_on_non_shell_tool_failures() -> None:
    """Failures from non-shell tools (e.g. a hypothetical API tool) are not fired on."""
    turns = [
        _ai(0, ["SomeTool"]),
        _tool(1, ERR),
        _ai(2, ["SomeTool"]),
        _tool(3, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events == []


# ---------------------------------------------------------------------------
# Evidence integrity
# ---------------------------------------------------------------------------


def test_event_evidence_fields_present() -> None:
    """Every fired event must carry error_snippet, first_call_turn, last_result_turn, turns_gap."""
    turns = [
        _ai(10, ["Bash"]),
        _tool(11, ERR),
        _ai(12, ["Bash"]),
        _tool(13, ERR),
    ]
    events = detect_repeated_failed_retry("sess-xyz", turns)
    assert len(events) == 1
    e = events[0]
    assert e.session_id == "sess-xyz"
    assert "error_snippet" in e.evidence
    assert "first_call_turn" in e.evidence
    assert "last_result_turn" in e.evidence
    assert "turns_gap" in e.evidence
    assert e.evidence["first_call_turn"] == 10
    assert e.evidence["last_result_turn"] == 13
    assert e.evidence["turns_gap"] == 3
    # All turns in e.turns must be in the original turn set
    turn_idxs = {t["turn_index"] for t in turns}
    assert all(t in turn_idxs for t in e.turns)


def test_proof_turns_are_interleaved_call_result() -> None:
    """Proof turns list is [call_1, result_1, call_2, result_2, ...] not just results."""
    turns = [
        _ai(5, ["Bash"]),
        _tool(6, ERR),
        _ai(7, ["Bash"]),
        _tool(8, ERR),
        _ai(9, ["Bash"]),
        _tool(10, ERR),
    ]
    events = detect_repeated_failed_retry("s1", turns)
    assert events[0].turns == [5, 6, 7, 8, 9, 10]


# ===========================================================================
# REDUNDANT-READ detector tests
# ===========================================================================

# Realistic file-content snippets (line-numbered, ≥80 chars)
FILE_A = "1\timport type { NextRequest } from 'next/server';\n2\texport const runtime = 'nodejs';\n3\texport const dynamic = 'force-dynamic';\n4\tfunction sseHeaders(): HeadersInit {"
FILE_B = "1\t\"\"\"\n2\tGallery candidate generator — 6 capability categories.\n3\tSaves every candidate + picks nothing automatically.\n4\t\"\"\"\n5\timport pathlib\n6\timport json"
FILE_UNCHANGED_HINT = "File unchanged since last read. The content from the earlier Read tool_result in this conversation is still current — refer to that instead of re-reading."
FILE_UNCHANGED_WITH_PATH = "File unchanged since last read. The file C:\\repo\\src\\auth.py has not changed."
SHORT_CONTENT = "1\tshort"                       # < 80 chars → not qualifying
SYSTEM_REMINDER = "<system-reminder>Warning: the file exists but is shorter than the provided offset."
NOT_LINE_NUMBERED = "Loaded 200 sessions.\n\nBULK: openai/gpt-oss-120b..."  # no \d+\t prefix


# ---------------------------------------------------------------------------
# PATH A: CC "File unchanged" hint
# ---------------------------------------------------------------------------


def test_rr_path_a_fires_on_unchanged_hint() -> None:
    """PATH A: Read followed by 'File unchanged since last read' → fires."""
    turns = [
        _ai(0, ["Read"]),
        _tool(1, FILE_UNCHANGED_HINT),
    ]
    events = detect_redundant_read("s1", turns)
    assert len(events) == 1
    e = events[0]
    assert e.detector == "REDUNDANT-READ"
    assert e.evidence["path"] == "A"
    assert e.evidence["call_turn"] == 0
    assert e.evidence["result_turn"] == 1
    assert e.evidence["gap"] == 0
    assert 0 in e.turns and 1 in e.turns


def test_rr_path_a_extracts_file_path_when_present() -> None:
    """PATH A: if the hint contains a file path, it appears in evidence."""
    turns = [
        _ai(0, ["Read"]),
        _tool(1, FILE_UNCHANGED_WITH_PATH),
    ]
    events = detect_redundant_read("s1", turns)
    assert len(events) == 1
    assert events[0].evidence["file_path"] is not None
    assert "auth.py" in events[0].evidence["file_path"]


def test_rr_path_a_no_path_when_absent() -> None:
    turns = [
        _ai(0, ["Read"]),
        _tool(1, FILE_UNCHANGED_HINT),
    ]
    events = detect_redundant_read("s1", turns)
    assert events[0].evidence["file_path"] is None


def test_rr_path_a_does_not_fire_on_non_read_tool() -> None:
    """PATH A only fires when the call was a Read, not Bash or Write."""
    turns = [
        _ai(0, ["Bash"]),
        _tool(1, FILE_UNCHANGED_HINT),
    ]
    events = detect_redundant_read("s1", turns)
    assert events == []


def test_rr_path_a_multiple_unchanged_in_session() -> None:
    """Multiple PATH A events in one session all fire independently."""
    turns = [
        _ai(0, ["Read"]),
        _tool(1, FILE_UNCHANGED_HINT),
        _ai(2, ["Read"]),
        _tool(3, FILE_UNCHANGED_HINT),
    ]
    events = detect_redundant_read("s1", turns)
    path_a = [e for e in events if e.evidence["path"] == "A"]
    assert len(path_a) == 2


# ---------------------------------------------------------------------------
# PATH B: content-match fires
# ---------------------------------------------------------------------------


def test_rr_path_b_fires_on_identical_content_gap1() -> None:
    """PATH B: two Read results with identical content and gap=1 → fires."""
    turns = [
        _ai(0, ["Read"]),
        _tool(1, FILE_A),
        _ai(2, ["Read"]),
        _tool(3, FILE_A),
    ]
    events = detect_redundant_read("s1", turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert len(path_b) == 1
    e = path_b[0]
    assert e.evidence["gap"] == 1  # call_2(2) - result_1(1) = 1
    assert e.turns == [0, 1, 2, 3]


def test_rr_path_b_fires_with_non_read_ai_turns_between() -> None:
    """PATH B: reasoning/text ai turns between reads don't break the window."""
    turns = [
        _ai(0, ["Read"]),
        _tool(1, FILE_A),
        _ai(2, [], snippet="Let me check another file first."),
        _ai(3, ["Read"]),          # read a different file
        _tool(4, FILE_B),
        _ai(5, ["Read"]),          # now re-read FILE_A
        _tool(6, FILE_A),
    ]
    events = detect_redundant_read("s1", turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert len(path_b) == 1
    assert path_b[0].evidence["gap"] == 4  # call_2(5) - result_1(1) = 4


def test_rr_path_b_fires_at_max_gap() -> None:
    """PATH B fires at gap exactly equal to _REDUNDANT_READ_GAP_MAX (5).
    Gap 5 is the conservative edge: re-reading after a small number of
    intermediate operations with no state change is uncontestable.
    """
    # result_1 at turn_index 1, call_2 at turn_index 6 → gap = 5
    turns = [_ai(0, ["Read"]), _tool(1, FILE_A)]
    for i in range(2, 6):
        turns.append(_ai(i, [], snippet="work"))
    turns.append(_ai(6, ["Read"]))
    turns.append(_tool(7, FILE_A))
    events = detect_redundant_read("s1", turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert len(path_b) == 1
    assert path_b[0].evidence["gap"] == 5


# ---------------------------------------------------------------------------
# PATH B: does NOT fire
# ---------------------------------------------------------------------------


def test_rr_path_b_no_fire_with_edit_between() -> None:
    """Edit between reads: state changed → no PATH B fire (near-miss 1)."""
    turns = [
        _ai(0, ["Read"]),
        _tool(1, FILE_A),
        _ai(2, ["Edit"]),          # barrier
        _ai(3, ["Read"]),
        _tool(4, FILE_A),
    ]
    events = detect_redundant_read("s1", turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert path_b == []


def test_rr_path_b_no_fire_with_write_between() -> None:
    turns = [
        _ai(0, ["Read"]),
        _tool(1, FILE_A),
        _ai(2, ["Write"]),
        _ai(3, ["Read"]),
        _tool(4, FILE_A),
    ]
    events = detect_redundant_read("s1", turns)
    assert all(e.evidence.get("path") != "B" for e in events)


def test_rr_path_b_no_fire_with_user_turn_between() -> None:
    """User turn (context reset) between reads → no fire (near-miss 2)."""
    turns = [
        _ai(0, ["Read"]),
        _tool(1, FILE_A),
        _user(2, "This session is being continued from a previous conversation."),
        _ai(3, ["Read"]),
        _tool(4, FILE_A),
    ]
    events = detect_redundant_read("s1", turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert path_b == []


def test_rr_path_b_no_fire_at_gap_above_max() -> None:
    """Gap > 5 → outside conservative window (re-orientation becomes contestable).
    Gaps 7-9 are where 'several intervening operations' makes re-reading plausibly
    legitimate, so the conservative line excludes them.
    """
    # result_1 at turn_index 1, call_2 at turn_index 8 → gap = 7
    turns = [_ai(0, ["Read"]), _tool(1, FILE_A)]
    for i in range(2, 8):
        turns.append(_ai(i, [], snippet="work"))
    turns.append(_ai(8, ["Read"]))
    turns.append(_tool(9, FILE_A))
    events = detect_redundant_read("s1", turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert path_b == []


def test_rr_path_b_no_fire_on_short_content() -> None:
    """Content shorter than 80 chars → not qualifying file content."""
    turns = [
        _ai(0, ["Read"]),
        _tool(1, SHORT_CONTENT),
        _ai(2, ["Read"]),
        _tool(3, SHORT_CONTENT),
    ]
    events = detect_redundant_read("s1", turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert path_b == []


def test_rr_path_b_no_fire_on_non_line_numbered_content() -> None:
    """Content not starting with \\d+\\t → not genuine file content, no fire."""
    turns = [
        _ai(0, ["Read"]),
        _tool(1, NOT_LINE_NUMBERED),
        _ai(2, ["Read"]),
        _tool(3, NOT_LINE_NUMBERED),
    ]
    events = detect_redundant_read("s1", turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert path_b == []


def test_rr_path_b_no_fire_on_system_reminder_content() -> None:
    """<system-reminder> content (starts with '<') → excluded from PATH B."""
    turns = [
        _ai(0, ["Read"]),
        _tool(1, SYSTEM_REMINDER),
        _ai(2, ["Read"]),
        _tool(3, SYSTEM_REMINDER),
    ]
    events = detect_redundant_read("s1", turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert path_b == []


def test_rr_path_b_no_fire_on_different_content() -> None:
    """Different file content → no match, no fire."""
    turns = [
        _ai(0, ["Read"]),
        _tool(1, FILE_A),
        _ai(2, ["Read"]),
        _tool(3, FILE_B),
    ]
    events = detect_redundant_read("s1", turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert path_b == []


def test_rr_path_b_no_fire_on_empty_turns() -> None:
    assert detect_redundant_read("s1", []) == []


# ---------------------------------------------------------------------------
# PATH A + PATH B co-occurrence and evidence integrity
# ---------------------------------------------------------------------------


def test_rr_both_paths_can_fire_in_same_session() -> None:
    """A session can have PATH A and PATH B events simultaneously."""
    turns = [
        # PATH B: FILE_A read twice with gap=1
        _ai(0, ["Read"]),
        _tool(1, FILE_A),
        _ai(2, ["Read"]),
        _tool(3, FILE_A),
        # PATH A: a later read returns "File unchanged"
        _ai(4, ["Read"]),
        _tool(5, FILE_UNCHANGED_HINT),
    ]
    events = detect_redundant_read("s1", turns)
    paths = {e.evidence["path"] for e in events}
    assert "A" in paths
    assert "B" in paths


def test_rr_path_b_evidence_fields_complete() -> None:
    """PATH B events carry all required evidence fields with correct values."""
    turns = [
        _ai(10, ["Read"]),
        _tool(11, FILE_A),
        _ai(12, ["Read"]),
        _tool(13, FILE_A),
    ]
    events = detect_redundant_read("sess-rr", turns)
    path_b = [e for e in events if e.evidence.get("path") == "B"]
    assert len(path_b) == 1
    e = path_b[0]
    assert e.session_id == "sess-rr"
    assert e.evidence["call_1_turn"] == 10
    assert e.evidence["result_1_turn"] == 11
    assert e.evidence["call_2_turn"] == 12
    assert e.evidence["result_2_turn"] == 13
    assert e.evidence["gap"] == 1
    assert "content_snippet" in e.evidence
    turn_idxs = {t["turn_index"] for t in turns}
    assert all(t in turn_idxs for t in e.turns)
