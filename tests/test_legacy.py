from __future__ import annotations

"""LEGACY rows (pre-dedupe adapter or pre-1h-cache-pricing): how they are told apart and counted."""

import sqlite3
from pathlib import Path

from tes.adapt import ADAPTER_VERSION, COST_VERSION
from tes.legacy import (
    census,
    count_legacy,
    current_clause,
    is_legacy_row,
    legacy_clause,
    legacy_sources,
    source_readable,
)
from tes.store import open_db

from tests.legacy_store import build_mixed_store


def test_is_legacy_row_covers_null_older_and_current() -> None:
    assert is_legacy_row({"adapter_version": None, "cost_version": None})
    assert is_legacy_row({"adapter_version": ADAPTER_VERSION, "cost_version": None})
    assert is_legacy_row({"adapter_version": 1, "cost_version": COST_VERSION})
    assert is_legacy_row({"adapter_version": ADAPTER_VERSION, "cost_version": COST_VERSION - 1})
    assert is_legacy_row({})
    assert not is_legacy_row({"adapter_version": ADAPTER_VERSION, "cost_version": COST_VERSION})


def test_sql_and_python_predicates_agree_and_partition_the_rows(tmp_path: Path) -> None:
    s = build_mixed_store(tmp_path)
    conn = open_db(s.db)
    legacy_sql, lp = legacy_clause(conn)
    current_sql, cp = current_clause(conn)
    legacy_ids = {
        r[0] for r in conn.execute(f"SELECT session_id FROM sessions WHERE {legacy_sql}", lp)
    }
    current_ids = {
        r[0] for r in conn.execute(f"SELECT session_id FROM sessions WHERE {current_sql}", cp)
    }
    conn.row_factory = sqlite3.Row
    py_legacy = {
        r["session_id"] for r in conn.execute("SELECT * FROM sessions") if is_legacy_row(dict(r))
    }
    assert (
        legacy_ids
        == py_legacy
        == {
            s.rescorable_pre_dedupe,
            s.rescorable_cost_only,
            s.unrecoverable,
            s.unparseable,
        }
    )
    assert current_ids == set(s.current)
    assert not legacy_ids & current_ids


def test_store_without_version_columns_is_entirely_legacy(tmp_path: Path) -> None:
    db = tmp_path / "v014.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sessions (session_id TEXT, source_path TEXT, source_mtime REAL)")
    conn.executemany("INSERT INTO sessions VALUES (?, ?, ?)", [("a", "x", 1.0), ("b", "y", 2.0)])
    assert count_legacy(conn) == 2
    assert [sid for sid, _ in legacy_sources(conn)] == ["b", "a"]
    assert conn.execute(
        f"SELECT COUNT(*) FROM sessions WHERE {current_clause(conn)[0]}"
    ).fetchone() == (0,)


def test_census_separates_rescorable_from_unrecoverable(tmp_path: Path) -> None:
    s = build_mixed_store(tmp_path)
    conn = open_db(s.db)
    c = census(conn)
    assert (c.total, c.legacy) == (6, 4)
    # two rows point at the real transcript, one at a readable-but-garbage file (still on disk,
    # so rescorable in principle; rescore reports it as failed), one at a vanished path
    assert (c.rescorable, c.unrecoverable) == (3, 1)
    assert census(conn, check_sources=False).legacy == 4


def test_source_readable_rejects_missing_empty_and_directories(tmp_path: Path) -> None:
    assert not source_readable(None)
    assert not source_readable("")
    assert not source_readable(str(tmp_path / "nope.jsonl"))
    assert not source_readable(str(tmp_path))
    f = tmp_path / "x.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    assert source_readable(str(f))
