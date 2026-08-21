from __future__ import annotations

"""tests/test_live_cost_estimated.py — Live cost/context is always labeled estimated/in-progress.

Covers: score_live_session's honesty labeling, find_active_session's mtime-based
active-file detection, tolerant behavior on partial/turn-less sessions, and a
regression check that completed-session reporting was not accidentally touched.
"""

import os
import time
from pathlib import Path
from unittest.mock import patch

from tes.live_monitor import find_active_session, score_live_session


def _fake_record(turns: list[dict], session_id: str = "live-test") -> dict:
    return {
        "session_id": session_id,
        "digest": {
            "session_id": session_id,
            "domain": "unknown",
            "resolved": False,
            "total_tokens": sum(t["token_count_input"] + t["token_count_output"] for t in turns),
            "turn_count": len(turns),
            "h2_duplicate_count": 0,
            "cache_hit_rate": 0.0,
            "p25_token_ratio": 1.0,
            "output_tokens_available": True,
            "task_description": "synthetic live session",
            "turns": turns,
        },
    }


def _ai_turn(
    idx: int,
    token_count_input: int = 1000,
    token_count_output: int = 200,
    cache_read: int = 800,
    cache_creation: int = 100,
) -> dict:
    return {
        "turn_index": idx,
        "role": "ai",
        "tool_names": [],
        "content_snippet": "ai turn",
        "token_count_input": token_count_input,
        "token_count_output": token_count_output,
        "cache_read": cache_read,
        "h2_duplicate": False,
        "cache_creation": cache_creation,
        "model": "claude-sonnet-4-6",
    }


# ---------------------------------------------------------------------------
# find_active_session — mtime-based "in progress" detection
# ---------------------------------------------------------------------------


def test_find_active_session_picks_the_recently_modified_file(tmp_path: Path) -> None:
    cc_dir = tmp_path / "projects" / "proj"
    cc_dir.mkdir(parents=True)
    old_file = cc_dir / "old.jsonl"
    new_file = cc_dir / "new.jsonl"
    old_file.write_text("{}", encoding="utf-8")
    new_file.write_text("{}", encoding="utf-8")

    now = time.time()
    os.utime(old_file, (now - 10_000, now - 10_000))  # long finished
    os.utime(new_file, (now - 5, now - 5))  # actively being written

    active = find_active_session(cc_dir, stability_window=300, _now=now)
    assert active == new_file


def test_find_active_session_none_when_all_files_are_finished(tmp_path: Path) -> None:
    cc_dir = tmp_path / "projects" / "proj"
    cc_dir.mkdir(parents=True)
    finished = cc_dir / "finished.jsonl"
    finished.write_text("{}", encoding="utf-8")

    now = time.time()
    os.utime(finished, (now - 10_000, now - 10_000))

    assert find_active_session(cc_dir, stability_window=300, _now=now) is None


def test_find_active_session_none_when_cc_path_missing(tmp_path: Path) -> None:
    assert find_active_session(tmp_path / "does-not-exist", stability_window=300) is None


# ---------------------------------------------------------------------------
# score_live_session — always labeled estimated/in-progress, never crashes on partial data
# ---------------------------------------------------------------------------


def test_score_live_session_labels_are_never_final(tmp_path: Path) -> None:
    turns = [_ai_turn(0), _ai_turn(1)]
    record = _fake_record(turns)
    fake_path = tmp_path / "live.jsonl"
    fake_path.write_text("{}", encoding="utf-8")

    with (
        patch("tes.live_monitor.adapt_session", return_value=record),
        patch("tes.live_monitor.classify_session", return_value="infra-deploy"),
    ):
        state = score_live_session(fake_path)

    assert state is not None
    assert "IN-PROGRESS" in state.domain_of_validity
    assert "provisional" in state.domain_of_validity
    assert "Never a final or billed figure" in state.domain_of_validity
    assert state.live_cost_usd > 0
    assert state.live_context_tokens > 0


def test_score_live_session_none_when_no_turns(tmp_path: Path) -> None:
    record = _fake_record([])
    fake_path = tmp_path / "empty.jsonl"
    fake_path.write_text("{}", encoding="utf-8")

    with patch("tes.live_monitor.adapt_session", return_value=record):
        assert score_live_session(fake_path) is None


def test_score_live_session_tolerates_adapt_failure(tmp_path: Path) -> None:
    fake_path = tmp_path / "corrupt.jsonl"
    fake_path.write_text("not json at all {{{", encoding="utf-8")

    with patch("tes.live_monitor.adapt_session", side_effect=Exception("mid-write partial JSON")):
        assert score_live_session(fake_path) is None


def test_resend_dominant_flag_matches_measured_attribution(tmp_path: Path) -> None:
    """A heavily-cached-read session must be flagged context_resend_dominant."""
    turns = [
        _ai_turn(
            0, token_count_input=10_000, cache_read=9_000, cache_creation=0, token_count_output=100
        ),
        _ai_turn(
            1, token_count_input=10_000, cache_read=9_000, cache_creation=0, token_count_output=100
        ),
    ]
    record = _fake_record(turns)
    fake_path = tmp_path / "resend.jsonl"
    fake_path.write_text("{}", encoding="utf-8")

    with (
        patch("tes.live_monitor.adapt_session", return_value=record),
        patch("tes.live_monitor.classify_session", return_value="infra-deploy"),
    ):
        state = score_live_session(fake_path)

    assert state is not None
    assert state.context_resend_dominant is True
    assert state.live_resend_ratio > 0.5


# ---------------------------------------------------------------------------
# Regression: completed-session reporting (report.py) untouched by this phase
# ---------------------------------------------------------------------------


def test_completed_session_report_never_uses_live_estimate_wording() -> None:
    """report.py (completed sessions) must never carry the live-monitor's
    'estimated, in progress' phrasing — that label is exclusive to live_monitor.py.
    """
    report_src = (
        Path(__file__).resolve().parents[1].joinpath("tes", "report.py").read_text(encoding="utf-8")
    )
    assert "estimated, in progress" not in report_src
