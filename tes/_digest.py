# Copyright 2026 Gaurav Gandhi
#
# Dual-licensed. You may use this file under the terms of either:
#   - the GNU Affero General Public License v3.0 only (AGPL-3.0-only), or
#   - the Apache License, Version 2.0 (Apache-2.0),
# at your option.
#
# SPDX-License-Identifier: AGPL-3.0-only OR Apache-2.0
#
# AGPL-3.0-only text: see LICENSE in the repository root.
# Apache-2.0 text: see LICENSE-APACHE in the repository root.

from __future__ import annotations

"""tes/_digest.py — Shared digest dataclasses used by tes.adapt and tes.judge.

Self-contained (no src/ or scripts/ imports). These are a direct copy of the
dataclasses in src/token_efficiency/trace_digest.py, kept here so the installed
wheel does not depend on the repo's src/ tree.

These are internal to the tes package — not part of the public API.
"""

from dataclasses import dataclass


@dataclass
class TurnDigest:
    """Compact representation of one conversation turn."""

    turn_index: int
    role: str                   # "ai" | "user" | "tool" | "system"
    tool_names: list[str]       # names of tools called in this turn
    content_snippet: str        # first 300 chars of content_text, stripped
    token_count_input: int
    token_count_output: int
    cache_read: int
    h2_duplicate: bool          # True if annotation flagged this turn as llm_h2_duplicate_message
    cache_creation: int = 0    # cache_creation_input_tokens for this turn (cost use only)
    model: str = ""            # model string for this turn, e.g. "claude-sonnet-4-6"
    # Added 0.10.2 (S1 fix): raw usage.server_tool_use counts from the Claude
    # API response (e.g. {"web_search_requests": 2}), when present and
    # non-empty. None when the field was absent/empty on this turn's raw
    # usage dict -- distinct from an empty dict, though tes.adapt never
    # actually produces an empty dict (see adapt.py's _parse_usage). Cost
    # computation (tes.cost) reads this to warn that server-side tool
    # billing is not reflected in total_usd, never to price it.
    server_tool_use: dict[str, int] | None = None


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
    output_tokens_available: bool  # True when per-turn output tokens are recorded
    task_description: str          # first user turn content, first 800 chars
    turns: list[TurnDigest]        # all turns, ordered by turn_index


def reconstruct_digest(d: dict) -> SessionDigest:
    """Reconstruct a SessionDigest from the plain dict stored in adapted records.

    Handles records generated before output_tokens_available was added by
    defaulting the field to False when absent (safe: swe_agent sessions lack it).
    """
    turns = [TurnDigest(**t) for t in d["turns"]]
    fields = {k: v for k, v in d.items() if k != "turns"}
    fields.setdefault("output_tokens_available", False)
    return SessionDigest(**fields, turns=turns)


def digest_to_text(digest: SessionDigest) -> str:
    """Render a SessionDigest as judge-readable text (show_stats=False mode).

    Omits formula-derived stats so the judge anchors on agent behaviour
    rather than token math.
    """
    header_summary = (
        f"Domain: {digest.domain} | Resolved: {digest.resolved} | "
        f"Turns: {digest.turn_count} | "
        f"Output Tokens: {'available' if digest.output_tokens_available else 'unavailable (swe_agent)'}"
    )
    lines: list[str] = [f"=== SESSION {digest.session_id} ===", header_summary]
    lines += ["", f"TASK: {digest.task_description}", "", "TRAJECTORY:"]

    for turn in digest.turns:
        if turn.role == "system":
            continue
        tool_str: str = ", ".join(turn.tool_names) if turn.tool_names else "none"
        # ENV_RESULT is the display label for openhands environment-response turns (role="tool").
        role_upper: str = "ENV_RESULT" if turn.role == "tool" else turn.role.upper()
        lines.append(
            f"[T{turn.turn_index}] {role_upper} — tools: {tool_str} — "
            f"in: {turn.token_count_input} / out: {turn.token_count_output}"
        )
        lines.append(f"  {turn.content_snippet}")

    return "\n".join(lines)


__all__ = ["TurnDigest", "SessionDigest", "reconstruct_digest", "digest_to_text"]
