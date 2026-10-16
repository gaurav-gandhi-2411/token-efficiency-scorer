from __future__ import annotations

"""Deterministic synthetic transcripts for the cache-churn tests (no real session content).

``clean_chain`` builds a chain whose cache is always read back (no event); ``inject`` plants
known TTL gaps, model switches and compactions at given positions. Everything is numbers and
ids, generated from a seeded ``random.Random``.
"""

from typing import Any

T0 = 1_760_000_000.0  # fixed epoch origin so timestamps are reproducible
MODEL_A = "claude-sonnet-5-5"
MODEL_B = "claude-opus-5-5"


def iso(epoch: float) -> str:
    """Epoch seconds -> the ISO-8601 'Z' form Claude Code writes."""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")


def user_rec(ts: float | None) -> dict[str, Any]:
    rec: dict[str, Any] = {"type": "user", "message": {"role": "user", "content": "x"}}
    if ts is not None:
        rec["timestamp"] = iso(ts)
    return rec


def asst_rec(
    mid: str,
    model: str,
    *,
    cr: int,
    cc: int,
    cc_1h: int = 0,
    inp: int = 3,
    ts: float | None = None,
    split: bool = True,
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "input_tokens": inp,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
        "output_tokens": 50,
    }
    if split:
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": cc - cc_1h,
            "ephemeral_1h_input_tokens": cc_1h,
        }
    rec: dict[str, Any] = {
        "type": "assistant",
        "message": {"id": mid, "role": "assistant", "model": model, "content": [], "usage": usage},
    }
    if ts is not None:
        rec["timestamp"] = iso(ts)
    return rec


def compact_boundary(ts: float | None = None) -> dict[str, Any]:
    rec: dict[str, Any] = {"type": "system", "subtype": "compact_boundary"}
    if ts is not None:
        rec["timestamp"] = iso(ts)
    return rec


def summary_rec(ts: float | None = None) -> dict[str, Any]:
    rec = user_rec(ts)
    rec["isCompactSummary"] = True
    return rec
