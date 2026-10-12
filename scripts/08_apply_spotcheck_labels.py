# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

"""Write manual Sonnet spot-check labels to the sample file."""

from __future__ import annotations

import json
import pathlib

SAMPLE_PATH = pathlib.Path("data/validation-corpus/annotations/spotcheck_sample.json")

# Manual labels by claude-sonnet-4-6, applied by reading each turn's full context.
# Format: keyed by (session_id, turn_index). The labeler's per-turn free-text notes are not kept:
# they quoted fragments of the third-party trajectories (see DATA_SOURCES.md).
LABELS: dict[tuple[str, int], dict] = {
    ("6a573854825819fd", 132): {
        "is_retry": False,
        "is_backtrack": False,
        "tool_result_used": True,
        "redundant_read": False,
    },
    ("ac4e96238d14d4a0", 126): {
        "is_retry": False,
        "is_backtrack": False,
        "tool_result_used": True,
        "redundant_read": False,
    },
    ("6c1a9f8d5157ee22", 104): {
        "is_retry": False,
        "is_backtrack": False,
        "tool_result_used": True,
        "redundant_read": False,
    },
    ("8fe3ae31394917df", 100): {
        "is_retry": False,
        "is_backtrack": False,
        "tool_result_used": True,
        "redundant_read": False,
    },
    ("b7d0e2e71d5abf03", 6): {
        "is_retry": False,
        "is_backtrack": False,
        "tool_result_used": True,
        "redundant_read": False,
    },
    ("34449e9f89dcc992", 4): {
        "is_retry": False,
        "is_backtrack": False,
        "tool_result_used": True,
        "redundant_read": False,
    },
    ("efcfd7ab6dba01e6", 36): {
        "is_retry": False,
        "is_backtrack": False,
        "tool_result_used": True,
        "redundant_read": False,
    },
    ("1bfb853597e0fec2", 42): {
        "is_retry": True,
        "is_backtrack": False,
        "tool_result_used": True,
        "redundant_read": False,
    },
    ("0d6ab8c934f0de65", 26): {
        "is_retry": False,
        "is_backtrack": False,
        "tool_result_used": True,
        "redundant_read": False,
    },
    ("225c0fb6b5bfcee6", 2): {
        "is_retry": False,
        "is_backtrack": False,
        "tool_result_used": True,
        "redundant_read": False,
    },
    ("4ca31e6363afe7f8", 186): {
        "is_retry": False,
        "is_backtrack": True,
        "tool_result_used": True,
        "redundant_read": False,
    },
}


def main() -> None:
    samples = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    applied = 0
    for s in samples:
        key = (s["session_id"], s["turn_index"])
        if key in LABELS:
            s["spotcheck_labels"] = {
                **LABELS[key],
                "labeler": "claude-sonnet-4-6",
            }
            applied += 1

    SAMPLE_PATH.write_text(json.dumps(samples, indent=2), encoding="utf-8")
    print(f"Applied {applied}/{len(samples)} labels.")

    # Summary statistics
    pos_retry = sum(1 for s in samples if s["spotcheck_labels"]["is_retry"])
    pos_bt = sum(1 for s in samples if s["spotcheck_labels"]["is_backtrack"])
    pos_ru = sum(1 for s in samples if s["spotcheck_labels"]["tool_result_used"])
    pos_rr = sum(1 for s in samples if s["spotcheck_labels"]["redundant_read"])
    n = len(samples)
    print(f"\nSpot-check label distribution (n={n}):")
    print(f"  is_retry:          {pos_retry}/{n} positive ({100 * pos_retry // n}%)")
    print(f"  is_backtrack:      {pos_bt}/{n} positive ({100 * pos_bt // n}%)")
    print(f"  tool_result_used:  {pos_ru}/{n} positive ({100 * pos_ru // n}%)")
    print(f"  redundant_read:    {pos_rr}/{n} positive ({100 * pos_rr // n}%)")


if __name__ == "__main__":
    main()
