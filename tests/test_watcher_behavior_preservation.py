from __future__ import annotations

"""Watcher behavior-preservation test.

Verifies that score_session_file() produces a ThreeAxisResult identical to
the manual pipeline (adapt → waste → score_session) for the same session.
Same session → same ThreeAxisResult, regardless of trigger path.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.score import score_session
from tes.waste import build_waste_entry
from tes.watcher import score_session_file


def _load_first_adapted_record() -> dict:
    """Load the first session from pool_adapted.jsonl as a test fixture."""
    pool_path = Path(__file__).parent.parent / "data" / "corpus_pool" / "pool_adapted.jsonl"
    first_line = pool_path.read_text(encoding="utf-8").splitlines()[0]
    return json.loads(first_line)


def test_watcher_behavior_preservation(tmp_path: Path) -> None:
    """score_session_file() == manual pipeline on the same adapted record."""
    record = _load_first_adapted_record()
    baselines = load_baselines(BUNDLED_BASELINES_PATH)

    # Manual path (what `tes score` does)
    session_id: str = record.get("session_id", "test")
    turns: list[dict] = record.get("digest", {}).get("turns", [])
    waste_entry = build_waste_entry(session_id, turns)
    expected = score_session(record, baselines, judge_entry=None, waste_entry=waste_entry)

    # Watcher path: adapt_session is mocked to return the same record
    fake_path = tmp_path / f"{session_id}.jsonl"
    fake_path.write_bytes(b"{}")  # content irrelevant — adapt is mocked

    with patch("tes.watcher.adapt_session", return_value=record):
        actual = score_session_file(fake_path, baselines, use_judge=False)

    assert actual is not None, "score_session_file returned None unexpectedly"

    # Every scored field must match exactly.
    assert actual.session_id == expected.session_id
    assert actual.task_type == expected.task_type
    assert actual.real_tokens == expected.real_tokens
    assert actual.scope_status == expected.scope_status
    assert actual.baseline_available == expected.baseline_available
    assert actual.p25 == expected.p25
    assert actual.p75 == expected.p75
    assert actual.median == expected.median
    assert actual.band_verdict == expected.band_verdict
    assert actual.interpretation == expected.interpretation
    assert actual.token_domain_of_validity == expected.token_domain_of_validity
    # Trajectory: both should be UNAVAILABLE (no judge in either path)
    assert actual.judge_verdict is None
    assert expected.judge_verdict is None
    # Waste: identical events and proof-turns
    assert actual.waste_event_count == expected.waste_event_count
    assert actual.waste_events == expected.waste_events
    assert actual.waste_domain_of_validity == expected.waste_domain_of_validity
