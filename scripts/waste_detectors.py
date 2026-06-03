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
    turns: list[int]        # turn_index values (from digest) that prove the event
    repeat_count: int = 1   # number of consecutive failures in REPEATED-FAILED-RETRY
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

# Transient availability errors: retry-with-different-resource is correct behaviour,
# not waste. Excluded unconditionally so the rule never fires on them.
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
    return turn.get("role") == "ai" and bool(
        set(turn.get("tool_names", [])) & _SHELL_TOOLS
    )


def _is_write_call(turn: dict[str, Any]) -> bool:
    return turn.get("role") == "ai" and bool(
        set(turn.get("tool_names", [])) & _WRITE_TOOLS
    )


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
