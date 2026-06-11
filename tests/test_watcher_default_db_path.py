from __future__ import annotations

"""Integration test: watcher scan cycle must not crash when db_path is None.

Regression guard for the P4 integration bug: run_watcher passed config.db_path
(None when tes serve runs without --db-path) directly to load_or_compute, which
called Path(None) → TypeError. open_db handled None correctly; load_or_compute
did not. The outer except caught the crash and logged "Scan cycle error", so no
sessions were ever scored.

The fix: resolve_db_path(config.db_path) is called once at the top of run_watcher
and the concrete Path is passed to both open_db and load_or_compute.

This test verifies that _scan_once is actually reached — proving load_or_compute
did not crash before it.
"""

import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from tes.watcher import WatcherConfig, run_watcher


def test_scan_cycle_reaches_scan_once_with_default_db_path(tmp_path: Path) -> None:
    """With db_path=None, run_watcher must reach _scan_once (not crash in load_or_compute).

    Uses TES_DB_PATH env var to redirect the default ~/.tes/tes.db to tmp_path so
    the test does not touch the user's real database.
    """
    cc_dir = tmp_path / "projects"
    cc_dir.mkdir()

    config = WatcherConfig(
        cc_path=cc_dir,
        stability_window=0,
        db_path=None,  # the default — exactly what `tes serve` uses
    )

    stop_event = threading.Event()
    stop_event.set()  # stop immediately after the first cycle

    with patch.dict(os.environ, {"TES_DB_PATH": str(tmp_path / "tes.db")}):
        with patch("tes.watcher._scan_once", return_value=0) as mock_scan:
            run_watcher(config, stop_event)

    # If load_or_compute crashed (the bug), mock_scan would never be called.
    mock_scan.assert_called_once()
