from __future__ import annotations

"""tes/waste.py — Thin re-export of the deterministic waste detectors.

The actual detector logic lives in scripts/waste_detectors.py.
As of P1: PATH-B dual-format regex (tab and arrow) — works on both
pre-v2.1.38 and v2.1.38+ Claude Code output.
"""

import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from waste_detectors import (  # noqa: E402
    WasteEvent,
    detect_redundant_read,
    detect_repeated_failed_retry,
)

from typing import Any


def build_waste_entry(session_id: str, turns: list[dict]) -> dict[str, Any]:
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


__all__ = ["WasteEvent", "detect_redundant_read", "detect_repeated_failed_retry", "build_waste_entry"]
