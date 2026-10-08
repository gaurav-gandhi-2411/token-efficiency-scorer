from __future__ import annotations

"""Incremental ledger test: unchanged sessions must not be re-scored."""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.store import open_db
from tes.watcher import WatcherConfig, _scan_once


def _make_minimal_record(session_id: str = "test-session-001") -> dict:
    """Minimal adapted record that score_session can process."""
    # Load a real record from the pool to ensure it's valid
    pool_path = Path(__file__).parent.parent / "data" / "corpus_pool" / "pool_adapted.jsonl"
    record = json.loads(pool_path.read_text(encoding="utf-8").splitlines()[0])
    record["session_id"] = session_id
    return record


def test_incremental_unchanged_not_rescored(tmp_path: Path) -> None:
    """After a session is scored, a second scan with the same file does not re-score it."""
    conn = open_db(tmp_path / "tes.db")
    baselines = load_baselines(BUNDLED_BASELINES_PATH)

    # Write a fake session JSONL to a tmp cc_path dir
    cc_dir = tmp_path / "projects" / "proj-abc"
    cc_dir.mkdir(parents=True)
    session_id = "test-session-001"
    jsonl_path = cc_dir / f"{session_id}.jsonl"
    jsonl_path.write_bytes(b'{"type":"test"}')

    record = _make_minimal_record(session_id)
    config = WatcherConfig(
        cc_path=tmp_path / "projects",
        stability_window=0,  # no stability delay in tests
        db_path=tmp_path / "tes.db",
    )

    # First scan: should score the session
    with patch("tes.watcher.adapt_session", return_value=record):
        count_1 = _scan_once(config, conn, baselines, _now=time.time() + 999)
    assert count_1 == 1, f"Expected 1 scored on first scan, got {count_1}"

    # Second scan: same file, same hash — should NOT re-score
    with patch("tes.watcher.adapt_session", return_value=record) as mock_adapt:
        count_2 = _scan_once(config, conn, baselines, _now=time.time() + 999)
    assert count_2 == 0, f"Expected 0 re-scored on second scan, got {count_2}"
    # adapt_session must NOT have been called (file was skipped before adapt)
    mock_adapt.assert_not_called()


def test_incremental_rescores_on_change(tmp_path: Path) -> None:
    """After a file changes (new hash), the session IS re-scored."""
    conn = open_db(tmp_path / "tes.db")
    baselines = load_baselines(BUNDLED_BASELINES_PATH)

    cc_dir = tmp_path / "projects" / "proj-abc"
    cc_dir.mkdir(parents=True)
    session_id = "test-session-002"
    jsonl_path = cc_dir / f"{session_id}.jsonl"
    jsonl_path.write_bytes(b'{"type":"v1"}')

    record = _make_minimal_record(session_id)
    config = WatcherConfig(
        cc_path=tmp_path / "projects",
        stability_window=0,
        db_path=tmp_path / "tes.db",
    )

    with patch("tes.watcher.adapt_session", return_value=record):
        count_1 = _scan_once(config, conn, baselines, _now=time.time() + 999)
    assert count_1 == 1

    # Modify the file (simulates session growing)
    jsonl_path.write_bytes(b'{"type":"v1"}\n{"type":"v2"}')

    with patch("tes.watcher.adapt_session", return_value=record):
        count_2 = _scan_once(config, conn, baselines, _now=time.time() + 999)
    assert count_2 == 1, f"Expected 1 re-scored after file change, got {count_2}"


@pytest.mark.parametrize("reverse", [False, True], ids=["alphabetical-scan", "reversed-scan"])
def test_failure_isolation_continues_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reverse: bool
) -> None:
    """A corrupt session raises inside adapt_session but does not abort the scan cycle.

    sess-A and sess-C are scored successfully; sess-B raises → skipped.
    _scan_once must return 2 and must not propagate the exception.

    adapt_session is faked BY FILE NAME, never by call position, and the scan order is forced both
    ways. The original version returned records from a positional side_effect list, which only
    lines up where rglob() happens to list files alphabetically (Windows/NTFS). On Linux the
    directory order is arbitrary (observed: C, A, B): the record carrying session_id "sess-A" was
    returned for file sess-C and stored under "sess-A", then needs_scoring("sess-A", same hash)
    skipped the real sess-A file, so only 1 session was scored. That cannot happen with the real
    adapt_session, which always returns session_id == the file's stem.
    """
    conn = open_db(tmp_path / "tes.db")
    baselines = load_baselines(BUNDLED_BASELINES_PATH)

    cc_dir = tmp_path / "projects" / "proj-iso"
    cc_dir.mkdir(parents=True)

    # Three identical JSONL files; content is arbitrary — adapt_session is faked.
    for stem in ("sess-A", "sess-B", "sess-C"):
        (cc_dir / f"{stem}.jsonl").write_bytes(b'{"type":"test"}')

    records = {"sess-A": _make_minimal_record("sess-A"), "sess-C": _make_minimal_record("sess-C")}

    def fake_adapt_session(path: Path) -> dict:
        if path.stem == "sess-B":
            raise Exception("corrupt")
        return records[path.stem]

    real_rglob = Path.rglob

    def ordered_rglob(self: Path, pattern: str):
        return iter(sorted(real_rglob(self, pattern), reverse=reverse))

    monkeypatch.setattr(Path, "rglob", ordered_rglob)

    config = WatcherConfig(
        cc_path=tmp_path / "projects",
        stability_window=0,
        db_path=tmp_path / "tes.db",
    )

    with patch("tes.watcher.adapt_session", side_effect=fake_adapt_session):
        # Must not raise
        scored = _scan_once(config, conn, baselines, _now=time.time() + 999)

    assert scored == 2, f"Expected 2 scored (A and C), got {scored}"
    stored = {row[0] for row in conn.execute("SELECT session_id FROM sessions")}
    assert stored == {"sess-A", "sess-C"}


def test_stability_window_skips_recent_files(tmp_path: Path) -> None:
    """Files modified within the stability window are not scored."""
    conn = open_db(tmp_path / "tes.db")
    baselines = load_baselines(BUNDLED_BASELINES_PATH)

    cc_dir = tmp_path / "projects" / "proj-abc"
    cc_dir.mkdir(parents=True)
    jsonl_path = cc_dir / "recent-session.jsonl"
    jsonl_path.write_bytes(b'{"type":"test"}')

    config = WatcherConfig(
        cc_path=tmp_path / "projects",
        stability_window=300,  # 5-minute window
        db_path=tmp_path / "tes.db",
    )

    # Pass _now = current mtime (file was just written → age = 0 < 300s)
    mtime = jsonl_path.stat().st_mtime
    with patch("tes.watcher.adapt_session") as mock_adapt:
        count = _scan_once(config, conn, baselines, _now=mtime + 10)
    assert count == 0
    mock_adapt.assert_not_called()
