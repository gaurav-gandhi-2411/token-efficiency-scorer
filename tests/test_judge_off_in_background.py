from __future__ import annotations

"""Judge-off-in-background test.

The background watcher must NOT call score_trajectory unless background_judge=True.
This enforces discipline 3 (judge footgun guard): a 30B model must not run on
every CC session automatically.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.store import open_db
from tes.watcher import WatcherConfig, _scan_once, score_session_file


def _first_adapted_record() -> dict:
    pool_path = Path(__file__).parent.parent / "data" / "corpus_pool" / "pool_adapted.jsonl"
    return json.loads(pool_path.read_text(encoding="utf-8").splitlines()[0])


def test_judge_not_called_by_default(tmp_path: Path) -> None:
    """Default WatcherConfig: score_trajectory is never imported or called."""
    record = _first_adapted_record()
    baselines = load_baselines(BUNDLED_BASELINES_PATH)

    fake_path = tmp_path / "session.jsonl"
    fake_path.write_bytes(b"{}")

    with (
        patch("tes.watcher.adapt_session", return_value=record),
        patch("tes.judge.score_trajectory") as mock_judge,
    ):
        result = score_session_file(fake_path, baselines, use_judge=False)

    assert result is not None
    mock_judge.assert_not_called()
    assert result.judge_verdict is None


def test_judge_called_when_opted_in(tmp_path: Path) -> None:
    """With use_judge=True, score_trajectory IS called (opt-in path works)."""
    record = _first_adapted_record()
    baselines = load_baselines(BUNDLED_BASELINES_PATH)

    fake_path = tmp_path / "session.jsonl"
    fake_path.write_bytes(b"{}")

    fake_judge_entry = {
        "session_id": record.get("session_id", "x"),
        "verdict": "MUCH_BETTER",
        "score": 0.9,
        "reasoning": "Efficient.",
    }

    with (
        patch("tes.watcher.adapt_session", return_value=record),
        patch("tes.judge.score_trajectory", return_value=fake_judge_entry) as mock_judge,
    ):
        result = score_session_file(fake_path, baselines, use_judge=True)

    mock_judge.assert_called_once()
    # judge_entry was passed; score should reflect it
    assert result is not None


def test_scan_does_not_call_judge_in_background(tmp_path: Path) -> None:
    """_scan_once with default WatcherConfig (background_judge=False) never calls score_trajectory."""
    conn = open_db(tmp_path / "tes.db")
    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    record = _first_adapted_record()
    session_id = record.get("session_id", "bg-test")
    record["session_id"] = session_id

    cc_dir = tmp_path / "projects" / "p"
    cc_dir.mkdir(parents=True)
    (cc_dir / f"{session_id}.jsonl").write_bytes(b'{"x":1}')

    config = WatcherConfig(
        cc_path=tmp_path / "projects",
        stability_window=0,
        db_path=tmp_path / "tes.db",
        background_judge=False,
    )

    with (
        patch("tes.watcher.adapt_session", return_value=record),
        patch("tes.judge.score_trajectory") as mock_judge,
    ):
        count = _scan_once(config, conn, baselines, _now=time.time() + 999)

    assert count == 1
    mock_judge.assert_not_called()
