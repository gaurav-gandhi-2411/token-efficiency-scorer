from __future__ import annotations

"""tests/test_behavior_preservation.py — Golden-output regression test for the three-axis scorer.

These tests capture the output of the VALIDATED scripts (efficiency_score.py) on a
representative sample of pool sessions. During the P1 refactor into tes/, this test's
import line changes from scripts/ to tes/; the golden file stays unchanged. Any score
change during refactor is a regression, not a packaging step.

CURRENT IMPORT: scripts/efficiency_score.py (pre-refactor)
REFACTOR TARGET: tes/score.py (post-refactor — change the import line only)
"""

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Make scripts/ importable (same pattern as test_waste_detectors.py line 14)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# CURRENT IMPORT: pre-refactor scripts location
# During P1 refactor this line becomes:  from tes.score import score_session, load_baselines
from efficiency_score import load_baselines, score_session  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level fixtures — loaded once, shared across all parametrized tests
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]

GOLDEN: list[dict] = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "golden_scores.json").read_text(encoding="utf-8")
)

BASELINES: dict = load_baselines(REPO_ROOT / "data" / "cc_baselines.json")


def _load_pool_index() -> dict[str, dict]:
    """Load pool_adapted.jsonl once and return a dict keyed by session_id."""
    index: dict[str, dict] = {}
    pool_path = REPO_ROOT / "data" / "corpus_pool" / "pool_adapted.jsonl"
    with pool_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                record = json.loads(line)
                index[record["session_id"]] = record
    return index


POOL_INDEX: dict[str, dict] = _load_pool_index()


# ---------------------------------------------------------------------------
# Parametrized regression test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "golden_entry",
    GOLDEN,
    ids=[g["session_id"][:12] for g in GOLDEN],
)
def test_score_matches_golden(golden_entry: dict) -> None:
    """Assert that score_session output exactly matches the captured golden record.

    Reconstructs the original call using:
    - The pool record fetched from POOL_INDEX by session_id
    - The raw _input_judge_entry and _input_waste_entry stored in the golden fixture
    - The shared BASELINES loaded from cc_baselines.json

    Any deviation in any field is a regression.
    """
    session_id: str = golden_entry["session_id"]
    record: dict = POOL_INDEX[session_id]
    judge_entry: dict | None = golden_entry["_input_judge_entry"]
    waste_entry: dict | None = golden_entry["_input_waste_entry"]

    result = score_session(record, BASELINES, judge_entry=judge_entry, waste_entry=waste_entry)

    assert result.session_id == golden_entry["session_id"], (
        f"session_id mismatch: got {result.session_id!r}"
    )
    assert result.task_type == golden_entry["task_type"], (
        f"task_type mismatch: got {result.task_type!r}, expected {golden_entry['task_type']!r}"
    )
    assert result.real_tokens == golden_entry["real_tokens"], (
        f"real_tokens mismatch: got {result.real_tokens}, expected {golden_entry['real_tokens']}"
    )
    assert result.scope_status == golden_entry["scope_status"], (
        f"scope_status mismatch: got {result.scope_status!r}, "
        f"expected {golden_entry['scope_status']!r}"
    )
    assert result.baseline_available == golden_entry["baseline_available"], (
        f"baseline_available mismatch: got {result.baseline_available}, "
        f"expected {golden_entry['baseline_available']}"
    )
    assert result.p25 == golden_entry["p25"], (
        f"p25 mismatch: got {result.p25}, expected {golden_entry['p25']}"
    )
    assert result.p75 == golden_entry["p75"], (
        f"p75 mismatch: got {result.p75}, expected {golden_entry['p75']}"
    )
    assert result.median == golden_entry["median"], (
        f"median mismatch: got {result.median}, expected {golden_entry['median']}"
    )
    assert result.band_verdict == golden_entry["band_verdict"], (
        f"band_verdict mismatch: got {result.band_verdict!r}, "
        f"expected {golden_entry['band_verdict']!r}"
    )
    assert result.interpretation == golden_entry["interpretation"], (
        f"interpretation mismatch for session {session_id!r}:\n"
        f"  got:      {result.interpretation!r}\n"
        f"  expected: {golden_entry['interpretation']!r}"
    )
    assert result.judge_verdict == golden_entry["judge_verdict"], (
        f"judge_verdict mismatch: got {result.judge_verdict!r}, "
        f"expected {golden_entry['judge_verdict']!r}"
    )
    assert result.judge_score == golden_entry["judge_score"], (
        f"judge_score mismatch: got {result.judge_score}, expected {golden_entry['judge_score']}"
    )
    assert result.judge_reasoning == golden_entry["judge_reasoning"], (
        f"judge_reasoning mismatch for session {session_id!r}:\n"
        f"  got:      {result.judge_reasoning!r}\n"
        f"  expected: {golden_entry['judge_reasoning']!r}"
    )
    assert result.waste_event_count == golden_entry["waste_event_count"], (
        f"waste_event_count mismatch: got {result.waste_event_count}, "
        f"expected {golden_entry['waste_event_count']}"
    )
    assert result.waste_events == golden_entry["waste_events"], (
        f"waste_events mismatch for session {session_id!r}:\n"
        f"  got:      {result.waste_events!r}\n"
        f"  expected: {golden_entry['waste_events']!r}"
    )
