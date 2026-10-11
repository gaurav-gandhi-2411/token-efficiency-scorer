from __future__ import annotations

"""Subagent transcripts are not sessions: every discovery site must skip them (D6),
while an explicit path to one must still be honoured."""

import argparse
import time
from pathlib import Path
from unittest.mock import patch

from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.cli import _newest_session, _recent_sessions, _resolve_score_targets
from tes.discovery import is_subagent_path, iter_session_files
from tes.live_monitor import find_active_session
from tes.store import open_db
from tes.watcher import WatcherConfig, _scan_once


def _layout(root: Path) -> tuple[Path, Path]:
    """<root>/proj/<sid>.jsonl (main) + <root>/proj/<sid>/subagents/agent-x.jsonl (newer)."""
    proj = root / "proj"
    sub_dir = proj / "sid-1" / "subagents"
    sub_dir.mkdir(parents=True)
    main = proj / "sid-1.jsonl"
    main.write_text('{"type":"user"}\n', encoding="utf-8")
    sub = sub_dir / "agent-abc.jsonl"
    sub.write_text('{"type":"user","isSidechain":true}\n', encoding="utf-8")
    # Subagent is the newest file: a discovery that did not filter would pick it.
    import os

    now = time.time()
    os.utime(main, (now - 100, now - 100))
    os.utime(sub, (now, now))
    return main, sub


def test_is_subagent_path() -> None:
    assert is_subagent_path(Path("p/s/subagents/agent-1.jsonl"))
    assert not is_subagent_path(Path("p/s.jsonl"))
    # a *file* named subagents.jsonl is not a subagents directory
    assert not is_subagent_path(Path("p/subagents.jsonl"))


def test_iter_session_files_skips_subagents(tmp_path: Path) -> None:
    main, _ = _layout(tmp_path)
    assert list(iter_session_files(tmp_path)) == [main]


def test_cc_path_under_a_dir_named_subagents_is_not_mis_filtered(tmp_path: Path) -> None:
    root = tmp_path / "subagents" / "projects"
    main, _ = _layout(root)
    assert list(iter_session_files(root)) == [main]


def test_recent_sessions_pick_and_newest_skip_subagents(tmp_path: Path) -> None:
    main, _ = _layout(tmp_path)
    assert [p for p, _ in _recent_sessions(tmp_path)] == [main]
    assert _newest_session(tmp_path) == main


def test_find_active_session_skips_subagents(tmp_path: Path) -> None:
    main, _ = _layout(tmp_path)
    import os

    now = time.time()
    os.utime(main, (now, now))
    # Both files are inside the stability window; only the main session may be returned.
    assert find_active_session(tmp_path, stability_window=300, _now=now + 1) == main


def test_watcher_scan_skips_subagents(tmp_path: Path) -> None:
    _layout(tmp_path)
    conn = open_db(tmp_path / "tes.db")
    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    config = WatcherConfig(cc_path=tmp_path, stability_window=0, db_path=tmp_path / "tes.db")
    with patch("tes.watcher.score_session_file", return_value=None) as scorer:
        _scan_once(config, conn, baselines, _now=time.time() + 999)
    scored = [call.args[0].name for call in scorer.call_args_list]
    assert scored == ["sid-1.jsonl"]


def test_explicit_subagent_path_still_resolves(tmp_path: Path) -> None:
    _, sub = _layout(tmp_path)
    args = argparse.Namespace(path=str(sub), pick=False, cc_path=None)
    assert _resolve_score_targets(args) == [sub.resolve()]
