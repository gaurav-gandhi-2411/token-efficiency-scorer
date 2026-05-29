from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path  # noqa: F401 — available for callers that import from here
from typing import Any

from token_efficiency.layer1_features import LayerOneFeatures

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SNIPPET_MAX_CHARS: int = 300
_TASK_DESC_MAX_CHARS: int = 800


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
    task_description: str  # first user turn content, first 800 chars
    turns: list[TurnDigest]  # all turns, ordered by turn_index


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

            tool_names: list[str] = [
                str(tool.get("name", ""))
                for tool in raw_turn.get("tool_uses", [])
                if tool.get("name")
            ]

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
        task_description=task_description,
        turns=turn_digests,
    )


def digest_to_text(digest: SessionDigest) -> str:
    """Render a ``SessionDigest`` as human-readable text for the rating interface.

    System turns are omitted from the TRAJECTORY section because their
    content_text is always empty in the normalized traces.

    Args:
        digest: The session digest to render.

    Returns:
        A multi-line string formatted for human raters.
    """
    lines: list[str] = [
        f"=== SESSION {digest.session_id} ===",
        (
            f"Domain: {digest.domain} | Resolved: {digest.resolved} | "
            f"Tokens: {digest.total_tokens} | Turns: {digest.turn_count}"
        ),
        (
            f"P25 Ratio: {digest.p25_token_ratio:.2f} | "
            f"Cache Hit: {digest.cache_hit_rate:.1%} | "
            f"H2 Duplicates: {digest.h2_duplicate_count}"
        ),
        "",
        f"TASK: {digest.task_description}",
        "",
        "TRAJECTORY:",
    ]

    for turn in digest.turns:
        # Skip system turns — they are always empty in normalized traces.
        if turn.role == "system":
            continue

        tool_str: str = ", ".join(turn.tool_names) if turn.tool_names else "none"
        role_upper: str = turn.role.upper()
        lines.append(
            f"[T{turn.turn_index}] {role_upper} — tools: {tool_str} — "
            f"tokens_in: {turn.token_count_input}"
        )
        lines.append(f"  {turn.content_snippet}")
        if turn.h2_duplicate:
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
