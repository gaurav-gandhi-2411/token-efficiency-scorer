from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path  # noqa: F401 — available for callers that import from here
from typing import Any

from token_efficiency.layer1_features import LayerOneFeatures

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SNIPPET_MAX_CHARS: int = 300
_TASK_DESC_MAX_CHARS: int = 800

# Matches the first fenced code block in a string. Group 1 = block body.
_CODE_BLOCK_RE: re.Pattern[str] = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

_SWE_CMD_MAP: dict[str, str] = {
    "create": "file_create",
    "edit": "file_edit",
    "open": "file_open",
    "goto": "nav_goto",
    "scroll_down": "nav_scroll",
    "scroll_up": "nav_scroll",
    "search_file": "file_search",
    "search_dir": "file_search",
    "grep": "file_search",
    "find": "file_search",
    "cat": "file_read",
    "head": "file_read",
    "tail": "file_read",
    "python": "run_python",
    "python3": "run_python",
    "pip": "run_pip",
    "pip3": "run_pip",
    "pytest": "run_pytest",
    "make": "run_make",
    "ls": "nav_ls",
    "dir": "nav_ls",
    "cd": "nav_cd",
    "submit": "submit",
    "bash": "run_bash",
    "sh": "run_bash",
    "zsh": "run_bash",
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class TurnDigest:
    """Compact representation of one conversation turn."""

    turn_index: int
    role: str  # "ai" | "user" | "system"
    tool_names: list[str]  # names of tools called in this turn
    content_snippet: str  # first 300 chars of content_text, stripped
    token_count_input: int
    token_count_output: int
    cache_read: int
    h2_duplicate: bool  # True if annotation flagged this turn as llm_h2_duplicate_message


@dataclass
class SessionDigest:
    """Human- and judge-consumable digest of a full session."""

    session_id: str
    domain: str
    resolved: bool
    total_tokens: int
    turn_count: int
    h2_duplicate_count: int
    cache_hit_rate: float
    p25_token_ratio: float
    output_tokens_available: bool  # True when per-turn output tokens are recorded (openhands); False for swe_agent
    task_description: str  # first user turn content, first 800 chars
    turns: list[TurnDigest]  # all turns, ordered by turn_index


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _classify_swe_command(first_line: str) -> str:
    """Map the first token of a swe_agent command line to a readable category."""
    first_word = first_line.strip().split()[0].lower() if first_line.strip() else ""
    return _SWE_CMD_MAP.get(first_word, first_word or "unknown")


def _extract_tools_from_content(content_text: str) -> list[str]:
    """Extract tool action from swe_agent content_text by parsing the first code block.

    Returns a list with one element (the classified command) if a code block is found,
    otherwise an empty list.
    """
    match = _CODE_BLOCK_RE.search(content_text)
    if not match:
        return []
    block_body = match.group(1)
    first_line = block_body.split("\n")[0]
    cmd = _classify_swe_command(first_line)
    return [cmd] if cmd else []


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def build_digest(
    session_id: str,
    features: LayerOneFeatures,
    trace: dict[str, Any] | None,
    annotation: dict[str, Any] | None,
) -> SessionDigest:
    """Construct a ``SessionDigest`` deterministically from raw data.

    No LLM calls are made; this is a pure deterministic transformation of
    the structured inputs into a compact digest form.

    Args:
        session_id: Canonical session identifier.
        features:   Pre-computed Layer 1 features for this session.
        trace:      Parsed trace JSON (turns + outcome), or None.
        annotation: Parsed annotation JSON (per_turn_labels), or None.

    Returns:
        A fully-populated ``SessionDigest``.
    """
    # Build a lookup from turn_index → h2_duplicate flag from the annotation.
    h2_lookup: dict[int, bool] = {}
    if annotation is not None:
        for label in annotation.get("per_turn_labels", []):
            idx: int = int(label.get("turn_index", -1))
            h2_lookup[idx] = bool(label.get("llm_h2_duplicate_message", False))

    task_description: str = "N/A"
    turn_digests: list[TurnDigest] = []

    if trace is not None:
        raw_turns: list[dict[str, Any]] = sorted(
            trace.get("turns", []), key=lambda t: int(t.get("turn_index", 0))
        )

        # task_description: content_text of first user-role turn.
        for raw_turn in raw_turns:
            if raw_turn.get("role") == "user":
                text: str = (raw_turn.get("content_text") or "").strip()
                task_description = text[:_TASK_DESC_MAX_CHARS]
                break

        for raw_turn in raw_turns:
            tidx: int = int(raw_turn.get("turn_index", 0))
            role: str = raw_turn.get("role", "")
            content_text: str = (raw_turn.get("content_text") or "").strip()
            snippet: str = content_text[:_SNIPPET_MAX_CHARS]

            raw_tool_uses = raw_turn.get("tool_uses", [])
            if raw_tool_uses:
                # openhands: structured tool_uses with tool_name field
                tool_names: list[str] = [
                    str(tool.get("tool_name") or tool.get("name", ""))
                    for tool in raw_tool_uses
                    if (tool.get("tool_name") or tool.get("name"))
                ]
            else:
                # swe_agent or any scaffold without structured tool_uses:
                # extract command from backtick code block in content_text
                if role in ("ai", "assistant") and content_text:
                    tool_names = _extract_tools_from_content(content_text)
                else:
                    tool_names = []

            tc: dict[str, Any] = raw_turn.get("token_counts", {})
            token_count_input: int = int(tc.get("input", 0))
            token_count_output: int = int(tc.get("output", 0))
            cache_read: int = int(tc.get("cache_read", 0))

            h2_dup: bool = h2_lookup.get(tidx, False)

            turn_digests.append(
                TurnDigest(
                    turn_index=tidx,
                    role=role,
                    tool_names=tool_names,
                    content_snippet=snippet,
                    token_count_input=token_count_input,
                    token_count_output=token_count_output,
                    cache_read=cache_read,
                    h2_duplicate=h2_dup,
                )
            )

    return SessionDigest(
        session_id=session_id,
        domain=features.domain_id,
        resolved=features.test_outcome,
        total_tokens=features.total_tokens,
        turn_count=features.turn_count,
        h2_duplicate_count=features.h2_duplicate_count,
        cache_hit_rate=features.cache_hit_rate,
        p25_token_ratio=features.p25_token_ratio,
        output_tokens_available=features.output_tokens_available,
        task_description=task_description,
        turns=turn_digests,
    )


def digest_to_text(digest: SessionDigest, *, show_stats: bool = True) -> str:
    """Render a ``SessionDigest`` as human-readable text for the rating interface.

    System turns are omitted from the TRAJECTORY section because their
    content_text is always empty in the normalized traces.

    Args:
        digest:     The session digest to render.
        show_stats: When True (default), include P25 Ratio, Cache Hit, H2 Duplicates,
                    and H2 duplicate markers in trajectory turns — full stats view.
                    When False, omit those formula-derived stats so raters anchor on
                    agent behavior rather than token math (rater-safe view).

    Returns:
        A multi-line string formatted for human raters.
    """
    if show_stats:
        header_summary = (
            f"Domain: {digest.domain} | Resolved: {digest.resolved} | "
            f"Tokens: {digest.total_tokens} | Turns: {digest.turn_count}"
        )
        stats_line: str | None = (
            f"P25 Ratio: {digest.p25_token_ratio:.2f} | "
            f"Cache Hit: {digest.cache_hit_rate:.1%} | "
            f"H2 Duplicates: {digest.h2_duplicate_count} | "
            f"Output Tokens: {'available' if digest.output_tokens_available else 'unavailable (swe_agent)'}"
        )
    else:
        header_summary = (
            f"Domain: {digest.domain} | Resolved: {digest.resolved} | "
            f"Turns: {digest.turn_count} | "
            f"Output Tokens: {'available' if digest.output_tokens_available else 'unavailable (swe_agent)'}"
        )
        stats_line = None

    lines: list[str] = [f"=== SESSION {digest.session_id} ===", header_summary]
    if stats_line is not None:
        lines.append(stats_line)
    lines += ["", f"TASK: {digest.task_description}", "", "TRAJECTORY:"]

    for turn in digest.turns:
        # Skip system turns — they are always empty in normalized traces.
        if turn.role == "system":
            continue

        tool_str: str = ", ".join(turn.tool_names) if turn.tool_names else "none"
        # ENV_RESULT is the display label for openhands environment-response turns (role="tool").
        # These are tool results returned by the execution environment, not agent actions.
        role_upper: str = "ENV_RESULT" if turn.role == "tool" else turn.role.upper()
        lines.append(
            f"[T{turn.turn_index}] {role_upper} — tools: {tool_str} — "
            f"in: {turn.token_count_input} / out: {turn.token_count_output}"
        )
        lines.append(f"  {turn.content_snippet}")
        if show_stats and turn.h2_duplicate:
            lines.append("   *** H2 DUPLICATE ***")

    return "\n".join(lines)


def digest_to_dict(digest: SessionDigest) -> dict[str, Any]:
    """Serialise a ``SessionDigest`` to a plain dict suitable for JSON output.

    Args:
        digest: The session digest to serialise.

    Returns:
        A plain dict (all nested dataclasses are recursively converted).
    """
    return dataclasses.asdict(digest)
