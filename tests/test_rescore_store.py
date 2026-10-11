from __future__ import annotations

"""`rescore` at the store level (backfill_waste(only_legacy=...)): re-scores only readable
legacy rows, never touches the rest, is idempotent, and a dry run leaves the store as it was."""

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tes.adapt import ADAPTER_VERSION, COST_VERSION
from tes.legacy import count_legacy
from tes.store import backfill_waste, open_db

from tests.legacy_store import MixedStore, build_mixed_store

EXPECTED_REAL = (2 + 5000 + 600) + (3 + 200)  # the fixture transcript, see test_usage_dedupe.py


def _dump(db: Path) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT * FROM sessions ORDER BY session_id").fetchall()
    conn.close()
    return rows


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def mixed(tmp_path: Path) -> MixedStore:
    return build_mixed_store(tmp_path)


def test_rescore_refreshes_only_readable_legacy_rows(mixed: MixedStore) -> None:
    before = {r[0]: r for r in _dump(mixed.db)}
    summary = backfill_waste(mixed.db, only_legacy=True)
    assert summary["legacy_rows"] == 4
    assert summary["refreshed"] == 2  # the two rows pointing at the real transcript
    assert summary["missing_source"] == 1  # the expired transcript
    assert summary["errors"] == 1  # the unparseable one
    after = {r[0]: r for r in _dump(mixed.db)}

    conn = open_db(mixed.db)
    for sid in (mixed.rescorable_pre_dedupe, mixed.rescorable_cost_only):
        row = conn.execute(
            "SELECT real_tokens, adapter_version, cost_version, session_cost_usd "
            "FROM sessions WHERE session_id = ?",
            (sid,),
        ).fetchone()
        assert row["real_tokens"] == EXPECTED_REAL  # corrected from the stored 999_999
        assert (row["adapter_version"], row["cost_version"]) == (ADAPTER_VERSION, COST_VERSION)
        assert row["session_cost_usd"] != mixed.legacy_cost_usd
    # untouched: the gone-source row, the failed row and both current rows are byte-identical
    for sid in (mixed.unrecoverable, mixed.unparseable, *mixed.current):
        assert after[sid] == before[sid], sid
    assert count_legacy(conn) == 2  # exactly the unrecoverable and the unparseable one


def test_second_rescore_changes_nothing(mixed: MixedStore) -> None:
    backfill_waste(mixed.db, only_legacy=True)
    first = _dump(mixed.db)
    summary = backfill_waste(mixed.db, only_legacy=True)
    assert summary["refreshed"] == 0
    assert _dump(mixed.db) == first


def test_dry_run_writes_nothing_and_matches_the_real_run(mixed: MixedStore, tmp_path: Path) -> None:
    files_before = sorted(p.name for p in tmp_path.iterdir())
    hash_before = _sha(mixed.db)
    dry = backfill_waste(mixed.db, only_legacy=True, dry_run=True)
    assert _sha(mixed.db) == hash_before
    assert sorted(p.name for p in tmp_path.iterdir()) == files_before  # no -wal/-shm left behind
    real = backfill_waste(mixed.db, only_legacy=True)
    for key in ("legacy_rows", "refreshed", "missing_source", "errors"):
        assert dry[key] == real[key], key


def test_dry_run_on_a_store_that_predates_every_new_column(tmp_path: Path) -> None:
    """A 0.14 store: dry-run must neither migrate it nor crash on the missing columns."""
    from tes import store

    src = tmp_path / "t.jsonl"
    src.write_bytes(
        Path(__file__)
        .parent.joinpath("fixtures", "usage_dedupe", "multi_block_session.jsonl")
        .read_bytes()
    )
    db = tmp_path / "v014.db"
    conn = sqlite3.connect(db)
    conn.executescript(store._DDL)
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO sessions (session_id, task_type, source_path, source_mtime, source_hash, "
        "scored_at, axes_scored, real_tokens, scope_status, baseline_available, band_verdict, "
        "interpretation, token_domain_of_validity, trajectory_domain_of_validity, "
        "waste_event_count, waste_events, waste_domain_of_validity) "
        "VALUES ('old', 'ml-eval', ?, 1.0, 'h', 't', '[]', 999999, 'in_scope', 1, 'above_p75', "
        "'i', 'd', 'd', 0, '[]', 'd')",
        (str(src),),
    )
    conn.commit()
    conn.close()
    hash_before = _sha(db)
    dry = backfill_waste(db, only_legacy=True, dry_run=True)
    assert (dry["legacy_rows"], dry["refreshed"], dry["errors"]) == (1, 1, 0)
    assert _sha(db) == hash_before
    real = backfill_waste(db, only_legacy=True)  # the real run migrates, then rescores
    assert real["refreshed"] == 1
    assert count_legacy(open_db(db)) == 0


def test_limit_stops_after_n_readable_rows(mixed: MixedStore) -> None:
    summary = backfill_waste(mixed.db, only_legacy=True, limit=1)
    assert summary["refreshed"] + summary["errors"] == 1
    assert summary["not_attempted"] == 2  # 3 readable rows (2 good, 1 garbage) minus 1 attempted
    assert summary["missing_source"] == 1


def test_a_cost_failure_leaves_the_row_legacy_instead_of_stamping_it_current(
    mixed: MixedStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("price table broke")

    monkeypatch.setattr("tes.cost.compute_session_cost", boom)
    summary = backfill_waste(mixed.db, only_legacy=True)
    assert summary["refreshed"] == 0 and summary["errors"] == 3
    assert count_legacy(open_db(mixed.db)) == 4


def test_backfill_waste_default_path_shares_the_zero_usage_guard(mixed: MixedStore) -> None:
    summary = backfill_waste(mixed.db)
    assert summary["legacy_rows"] == 0 and summary["not_attempted"] == 0
    # Same rule as rescore: the unparseable file is a failure and its row is left alone (it used
    # to be "refreshed" with zeros). The 2 current rows have no file at all (+ the expired one).
    assert summary["refreshed"] == 2 and summary["errors"] == 1 and summary["missing_source"] == 3


def test_dry_run_beside_a_live_writer_does_not_touch_the_store_file(mixed: MixedStore) -> None:
    """The watcher holds the store open (so a -wal exists): the dry run still changes nothing."""
    writer = open_db(mixed.db)
    writer.execute("UPDATE sessions SET scored_at = scored_at")  # keeps the WAL file alive
    writer.commit()
    assert Path(f"{mixed.db}-wal").exists()
    hash_before = _sha(mixed.db)
    dry = backfill_waste(mixed.db, only_legacy=True, dry_run=True)
    assert dry["legacy_rows"] == 4 and dry["refreshed"] == 2
    assert _sha(mixed.db) == hash_before
    writer.close()
