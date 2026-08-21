from __future__ import annotations

"""waste_detectors.py — Deterministic waste event detectors over session trace digests.

No LLM inference. No model dependency. Each detector fires only on behavior that is
waste under any reasonable definition: conservative (under-detect defensible waste),
uncontestable (any evaluator agrees), auditable (proof turns attached to every event).
"""

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Shared output type
# ---------------------------------------------------------------------------


@dataclass
class WasteEvent:
    """A detected waste event with auditable evidence turns."""

    detector: str
    session_id: str
    turns: list[int]  # turn_index values (from digest) that prove the event
    repeat_count: int = 1  # number of consecutive failures in REPEATED-FAILED-RETRY
    evidence: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# REPEATED-FAILED-RETRY detector
# ---------------------------------------------------------------------------
# Fires when a shell tool produces an identical error ≥ 2 times consecutively,
# with no state-changing operation between any pair of identical failures.
#
# Design constraints (from B4 spec credibility rule):
#   CONSERVATIVE  — under-detect defensible waste, not over-detect arguable waste.
#   UNCONTESTABLE — the specific failing resource is named in both error messages;
#                   exact full-snippet match is required (not prefix).
#   AUDITABLE     — every event carries the specific turns that prove it.
#
# Key limitation (documented):
#   The digest content_snippet captures only 300 chars. Two failures identical in
#   their first 300 chars but differing after would match — an acceptable
#   over-fire risk given the conservative posture everywhere else.
# ---------------------------------------------------------------------------

_SHELL_TOOLS: frozenset[str] = frozenset({"Bash", "PowerShell"})
_WRITE_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "NotebookEdit"})

# Transient availability errors and CI-polling status codes: retry or re-poll is
# correct behaviour, not waste. Excluded unconditionally so the rule never fires.
_TRANSIENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ZONE_RESOURCE_POOL_EXHAUSTED",
        r"RESOURCE_POOL_EXHAUSTED",
        r"QUOTA_EXCEEDED",
        r"rateLimitExceeded",
        r"rate.?limit.?exceeded",
        r"quota.?exceeded",
        r"429 Too Many Requests",
        r"503 Service Unavailable",
        # gh CLI CI-polling status codes — not fixable failures; polling is transient.
        # `gh pr checks` returns exit code 8 + tabular "pending" when checks are running.
        # `gh pr checks` returns exit code 1 + "no checks reported" when CI hasn't started.
        # Both are CI-polling, not agent-fixable failures.
        r"\tpending\t",
        r"no checks reported on the ",
    ]
]

# Signals in tool-result snippets that indicate Bash-driven state mutation.
# If any appears between two identical failures, the state DID change and
# the second failure is not an uncontested repeat — don't fire.
_STATE_MUTATION_PATTERNS: list[re.Pattern[str]] = [
    # Package installs
    re.compile(r"successfully installed", re.IGNORECASE),
    re.compile(r"added \d+ package", re.IGNORECASE),
    re.compile(r"packages installed successfully", re.IGNORECASE),
    re.compile(r"\bnpm\b.{0,30}\badded\b", re.IGNORECASE),
    re.compile(r"\bpip\b.{0,30}\binstalled\b", re.IGNORECASE),
    # git state changes
    re.compile(r"HEAD is now at", re.IGNORECASE),
    re.compile(r"\bfast.?forward\b", re.IGNORECASE),
    re.compile(r"\bupdated branch\b", re.IGNORECASE),
    # File write confirmations (CC tool messages)
    re.compile(
        r"The file .{0,120} has been (?:updated|created) successfully",
        re.IGNORECASE,
    ),
    re.compile(r"written successfully", re.IGNORECASE),
]


def _is_shell_call(turn: dict[str, Any]) -> bool:
    return turn.get("role") == "ai" and bool(set(turn.get("tool_names", [])) & _SHELL_TOOLS)


def _is_write_call(turn: dict[str, Any]) -> bool:
    return turn.get("role") == "ai" and bool(set(turn.get("tool_names", [])) & _WRITE_TOOLS)


def _is_error_result(snippet: str) -> bool:
    """Return True if snippet carries a recognisable non-empty failure signal."""
    if len(snippet.strip()) < 20:
        return False
    s_lower = snippet.lower()
    if re.search(r"exit code [1-9]", s_lower):
        return True
    if snippet.startswith("fatal:"):
        return True
    # grep "No such file" without an exit-code prefix (short but unambiguous)
    if snippet.startswith("grep: ") and "no such file" in s_lower:
        return True
    return False


def _is_transient(snippet: str) -> bool:
    return any(p.search(snippet) for p in _TRANSIENT_PATTERNS)


def _has_state_mutation(snippet: str) -> bool:
    return any(p.search(snippet) for p in _STATE_MUTATION_PATTERNS)


def _next_tool_pos(turns: list[dict[str, Any]], from_pos: int) -> int | None:
    """First 'tool' turn position after from_pos, or None if a 'user' turn intervenes."""
    for k in range(from_pos + 1, len(turns)):
        role = turns[k].get("role")
        if role == "tool":
            return k
        if role == "user":
            return None  # human message intervenes; can't attribute result to this call
    return None


def detect_repeated_failed_retry(
    session_id: str,
    turns: list[dict[str, Any]],
) -> list[WasteEvent]:
    """Detect runs of consecutive identical shell failures with no state change between.

    A run of N consecutive identical failures emits ONE WasteEvent with repeat_count=N
    and all proof turns in the ``turns`` field.  A single failure (N=1) is not an event.

    State-change barriers that end a run (conservative — any sign of change stops the run):
    - An "ai" turn calling Write, Edit, or NotebookEdit
    - A "tool" turn whose snippet matches any _STATE_MUTATION_PATTERNS entry
    - A "user" (human) turn (new instructions = new context)

    Transient errors (zone exhaustion, rate limits, quotas) are excluded unconditionally.
    """
    if not turns:
        return []

    events: list[WasteEvent] = []
    n = len(turns)
    idx_to_pos: dict[int, int] = {t["turn_index"]: pos for pos, t in enumerate(turns)}

    i = 0
    while i < n:
        if not _is_shell_call(turns[i]):
            i += 1
            continue

        result_pos = _next_tool_pos(turns, i)
        if result_pos is None:
            i += 1
            continue

        snippet = turns[result_pos].get("content_snippet", "")
        if not _is_error_result(snippet) or _is_transient(snippet):
            i = result_pos + 1
            continue

        # Qualifying error — try to extend into a run
        run_call_idxs: list[int] = [turns[i]["turn_index"]]
        run_result_idxs: list[int] = [turns[result_pos]["turn_index"]]
        target_snip = snippet

        k = result_pos + 1
        while k < n:
            t = turns[k]
            role = t.get("role")

            if role == "user":
                break  # human turn: barrier

            if role == "ai":
                if _is_write_call(t):
                    break  # file-write barrier

                if _is_shell_call(t):
                    nrp = _next_tool_pos(turns, k)
                    if nrp is None:
                        break
                    next_snip = turns[nrp].get("content_snippet", "")
                    if next_snip == target_snip:
                        run_call_idxs.append(t["turn_index"])
                        run_result_idxs.append(turns[nrp]["turn_index"])
                        k = nrp + 1
                        continue
                    else:
                        break  # different result: run ended

                # Non-write, non-shell ai turn (text/reasoning/read-only tool): OK
                k += 1
                continue

            if role == "tool":
                if _has_state_mutation(t.get("content_snippet", "")):
                    break  # Bash-driven state change
                k += 1
                continue

            k += 1  # any other role: skip

        if len(run_call_idxs) >= 2:
            proof_turns: list[int] = []
            for call_idx, res_idx in zip(run_call_idxs, run_result_idxs):
                proof_turns.append(call_idx)
                proof_turns.append(res_idx)

            events.append(
                WasteEvent(
                    detector="REPEATED-FAILED-RETRY",
                    session_id=session_id,
                    turns=proof_turns,
                    repeat_count=len(run_call_idxs),
                    evidence={
                        "error_snippet": target_snip,
                        "first_call_turn": run_call_idxs[0],
                        "last_result_turn": run_result_idxs[-1],
                        "turns_gap": run_result_idxs[-1] - run_call_idxs[0],
                    },
                )
            )
            last_result_list_pos = idx_to_pos[run_result_idxs[-1]]
            i = last_result_list_pos + 1
        else:
            i = result_pos + 1

    return events


# ---------------------------------------------------------------------------
# REDUNDANT-READ detector
# ---------------------------------------------------------------------------
# Fires when the agent re-reads an unchanged file: same content fetched again
# with no Write/Edit/NotebookEdit or context-reset (user turn) between reads.
#
# Two detection paths, separately labeled and reported:
#
#   PATH A — CC Read tool returns "File unchanged since last read"
#     The CC tool itself detected the redundancy and said so. This is the
#     tool's verdict, not an inference — maximally uncontestable. The hint
#     appears when the file is already in the conversation context unchanged.
#
#   PATH B — Identical content_snippet from two Read results (conservative)
#     Same line-numbered file content (≥80 chars, starts with \d+\t (pre-v2.1.38) or \s+\d+→ (v2.1.38+)) appears
#     in two Read results within a ≤10-turn window with no state change between.
#     10 turns is a conservative cap: re-orientation reads after long work are
#     excluded. Report the gap distribution before locking this threshold.
#
# Design principle: content-matching is MORE robust here than command-matching
# (which we can't do — the digest doesn't capture file paths). If the same
# file content appears in two Read results, the same portion of the same file
# was fetched twice. File changes between reads produce different content and
# naturally don't fire.
#
# Documented limitation: 300-char truncation. Two reads of the same file where
# an edit changed only content beyond char 300 would match. Conservative posture
# means we accept this rare over-fire rather than miss clear redundant reads.
# ---------------------------------------------------------------------------

_FILE_UNCHANGED_PREFIX = "File unchanged since last read"
_LINE_NUMBERED_RE = re.compile(r"^\d+\t|^\s+\d+→")
_REDUNDANT_READ_GAP_MAX = 5  # PATH B: gaps 7-9 are contestable (re-orientation after
# several intervening operations is plausibly legitimate); ≤5 stays uncontestable.


def _is_read_call(turn: dict[str, Any]) -> bool:
    return turn.get("role") == "ai" and "Read" in turn.get("tool_names", [])


def _is_line_numbered_content(snippet: str) -> bool:
    """Return True if snippet looks like genuine file content from the Read tool."""
    if len(snippet.strip()) < 80:
        return False
    if snippet.startswith(_FILE_UNCHANGED_PREFIX):
        return False  # PATH A territory
    if snippet.startswith("<"):
        return False  # system-reminder injections, error XML
    return bool(_LINE_NUMBERED_RE.match(snippet))


def _extract_path_from_hint(snippet: str) -> str | None:
    """Try to find a file path in a 'File unchanged' hint snippet."""
    m = re.search(r"(?:[A-Za-z]:\\|/)[^\s'\"<>]+\.\w+", snippet)
    return m.group() if m else None


def detect_redundant_read(
    session_id: str,
    turns: list[dict[str, Any]],
) -> list[WasteEvent]:
    """Detect redundant file reads: same file content fetched again with no change between.

    PATH A events: the CC Read tool itself reported "File unchanged since last read."
    PATH B events: two Read results carry identical line-numbered content within ≤10 turns
                   with no Write/Edit/NotebookEdit or user (context-reset) turn between.

    Every event carries path="A" or path="B" in evidence so callers can report
    fire rates per path separately (PATH A is tool-authoritative; PATH B is inferred).
    """
    if not turns:
        return []

    events: list[WasteEvent] = []
    n = len(turns)
    idx_to_pos: dict[int, int] = {t["turn_index"]: pos for pos, t in enumerate(turns)}

    # ---- PATH A scan -------------------------------------------------------
    for i, t in enumerate(turns):
        if not _is_read_call(t):
            continue
        result_pos = _next_tool_pos(turns, i)
        if result_pos is None:
            continue
        snip = turns[result_pos].get("content_snippet", "")
        if snip.startswith(_FILE_UNCHANGED_PREFIX):
            events.append(
                WasteEvent(
                    detector="REDUNDANT-READ",
                    session_id=session_id,
                    turns=[t["turn_index"], turns[result_pos]["turn_index"]],
                    evidence={
                        "path": "A",
                        "call_turn": t["turn_index"],
                        "result_turn": turns[result_pos]["turn_index"],
                        "content_snippet": snip[:120],
                        "file_path": _extract_path_from_hint(snip),
                        "gap": 0,
                    },
                )
            )

    # ---- PATH B scan -------------------------------------------------------
    # Collect all qualifying Read results: (call_idx, result_idx, list_pos, snippet)
    reads: list[tuple[int, int, int, str]] = []
    for i, t in enumerate(turns):
        if not _is_read_call(t):
            continue
        rp = _next_tool_pos(turns, i)
        if rp is None:
            continue
        snip = turns[rp].get("content_snippet", "")
        if _is_line_numbered_content(snip):
            reads.append((t["turn_index"], turns[rp]["turn_index"], rp, snip))

    # Track which call_1 indices have already fired to avoid duplicate events
    # from the same first read pairing with multiple later reads.
    fired_first: set[int] = set()

    for ia, (call_a, res_a, pos_a, snip_a) in enumerate(reads):
        if call_a in fired_first:
            continue
        for call_b, res_b, pos_b, snip_b in reads[ia + 1 :]:
            if snip_a != snip_b:
                continue
            gap = call_b - res_a
            if gap <= 0 or gap > _REDUNDANT_READ_GAP_MAX:
                continue

            # Check barriers in the range (pos_a+1 .. pos of call_b, exclusive)
            call_b_pos = idx_to_pos.get(call_b, pos_a + 1)
            has_barrier = any(
                (_is_write_call(turns[k]) or turns[k].get("role") == "user")
                for k in range(pos_a + 1, call_b_pos)
            )
            if has_barrier:
                continue

            events.append(
                WasteEvent(
                    detector="REDUNDANT-READ",
                    session_id=session_id,
                    turns=[call_a, res_a, call_b, res_b],
                    evidence={
                        "path": "B",
                        "call_1_turn": call_a,
                        "result_1_turn": res_a,
                        "call_2_turn": call_b,
                        "result_2_turn": res_b,
                        "content_snippet": snip_a[:120],
                        "gap": gap,
                    },
                )
            )
            fired_first.add(call_a)
            break  # one event per first-read; move to next ia

    return events
