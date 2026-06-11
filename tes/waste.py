from __future__ import annotations

"""tes/waste.py — Waste detector entry point for the installed SDK.

The detection logic lives verbatim in tes._waste_detectors (byte-identical
copy of the frozen B4/P3 scripts/waste_detectors.py). This module re-exports
the public API and adds build_waste_entry, a convenience aggregator that did
not exist in the original research script.
"""

from typing import Any

from tes._waste_detectors import (  # noqa: F401
    WasteEvent,
    detect_redundant_read,
    detect_repeated_failed_retry,
)


def build_waste_entry(session_id: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Run both waste detectors and return a waste_entry dict for score_session."""
    rfr = detect_repeated_failed_retry(session_id, turns)
    rr = detect_redundant_read(session_id, turns)
    return {
        "session_id": session_id,
        "waste_events": [
            {
                "detector": e.detector,
                "session_id": e.session_id,
                "turns": e.turns,
                "repeat_count": e.repeat_count,
                "evidence": e.evidence,
            }
            for e in rfr + rr
        ],
    }


def annotate_waste_costs(
    waste_events: list[dict[str, Any]],
    per_turn_cost: dict[int, float],
) -> list[dict[str, Any]]:
    """Embed wasted_cost_usd into each waste event dict in-place; returns the same list.

    Definition: cost of the REDUNDANT turns only — proof_turns[2:] — skipping the
    first legitimate call+result pair.  AI turns carry their measured per-turn cost;
    tool/user turns are absent from per_turn_cost so contribute 0 (they don't generate
    output tokens directly; their context cost is amortised into the next AI turn's
    cache_read charge, which IS included for AI call turns).

    RR-A events have only two proof turns ([call, result]), so redundant = [] → cost 0.
    This is conservative: PATH-A is currently silent on the live session population.
    """
    for event in waste_events:
        proof_turns: list[int] = event.get("turns", [])
        redundant: list[int] = proof_turns[2:]
        event["wasted_cost_usd"] = sum(per_turn_cost.get(ti, 0.0) for ti in redundant)
    return waste_events


__all__ = [
    "WasteEvent",
    "detect_redundant_read",
    "detect_repeated_failed_retry",
    "build_waste_entry",
    "annotate_waste_costs",
]
