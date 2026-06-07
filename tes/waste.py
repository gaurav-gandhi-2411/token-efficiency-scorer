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


__all__ = [
    "WasteEvent",
    "detect_redundant_read",
    "detect_repeated_failed_retry",
    "build_waste_entry",
]
