from __future__ import annotations

"""open_db upgrades a store from any earlier shape: additive, idempotent, never rewrites a row,
and survives two processes (the watcher and a CLI command) migrating the same file at once."""

import sqlite3
import threading
from pathlib import Path

import pytest
from tes import store
from tes.store import open_db

_OLD_ROW = (
    "INSERT INTO sessions (session_id, task_type, source_path, source_mtime, source_hash, "
    "scored_at, axes_scored, real_tokens, scope_status, baseline_available, band_verdict, "
    "interpretation, token_domain_of_validity, trajectory_domain_of_validity, "
    "waste_event_count, waste_events, waste_domain_of_validity) "
    "VALUES ('old1', 'ml-eval', 'gone.jsonl', 5.0, 'h', 't', '[]', 123456, 'in_scope', 1, "
    "'above_p75', 'i', 'd', 'd', 0, '[]', 'd')"
)


def _make_v1_store(path: Path) -> None:
    """A store as the first release wrote it: base DDL only, one scored row."""
    conn = sqlite3.connect(path)
    conn.executescript(store._DDL)
    conn.execute("PRAGMA user_version = 1")
    conn.execute(_OLD_ROW)
    conn.commit()
    conn.close()


def _cols(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}


def test_fresh_store_has_every_column_and_the_meta_table(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "fresh.db")
    assert {"adapter_version", "cost_version", "dominant_model", "turn_count"} <= _cols(conn)
    conn.execute("INSERT INTO meta (key, value) VALUES ('k', 'v')")
    assert conn.execute("SELECT value FROM meta WHERE key = 'k'").fetchone()[0] == "v"


def test_old_schema_store_is_upgraded_without_touching_its_rows(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    _make_v1_store(db)
    before = sqlite3.connect(db).execute("SELECT * FROM sessions").fetchall()

    conn = open_db(db)
    assert {"adapter_version", "cost_version", "session_cost_usd"} <= _cols(conn)
    row = conn.execute(
        "SELECT real_tokens, band_verdict, adapter_version, cost_version FROM sessions"
    ).fetchone()
    assert (row[0], row[1]) == (123456, "above_p75")  # original numbers survive
    assert row[2] is None and row[3] is None  # and are not claimed by the current adapter
    after = conn.execute("SELECT * FROM sessions").fetchall()
    assert [tuple(r)[: len(before[0])] for r in after] == [tuple(r) for r in before]
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_already_migrated_store_reopens_unchanged(tmp_path: Path) -> None:
    db = tmp_path / "twice.db"
    open_db(db).close()
    first = open_db(db)
    cols_first = _cols(first)
    first.close()
    second = open_db(db)
    assert _cols(second) == cols_first
    second.close()


def test_duplicate_column_race_is_swallowed_other_errors_are_not(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "race.db")
    # The loser of a concurrent ALTER sees "duplicate column name": the column exists, done.
    store._add_column(conn, "ALTER TABLE sessions ADD COLUMN turn_count INTEGER")
    with pytest.raises(sqlite3.OperationalError):
        store._add_column(conn, "ALTER TABLE no_such_table ADD COLUMN x INTEGER")


def test_concurrent_open_of_an_old_store(tmp_path: Path) -> None:
    db = tmp_path / "conc.db"
    _make_v1_store(db)
    n = 6
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            open_db(db).close()
        except BaseException as exc:  # noqa: BLE001 -- the test asserts none escaped
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    conn = open_db(db)
    assert conn.execute("SELECT real_tokens FROM sessions").fetchone()[0] == 123456
    assert "cost_version" in _cols(conn)
