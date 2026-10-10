from __future__ import annotations

"""Stored rows carry the adapter version; the self-baseline ignores pre-dedupe rows and
`backfill_waste` re-scores them from the source transcript (idempotently)."""

import json
import shutil
import sqlite3
from pathlib import Path

from tes.adapt import ADAPTER_VERSION
from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.self_baseline import compute_self_baselines
from tes.store import backfill_waste, open_db

FIXTURE = Path(__file__).parent / "fixtures" / "usage_dedupe" / "multi_block_session.jsonl"
EXPECTED_REAL = (2 + 5000 + 600) + (3 + 200)  # see test_usage_dedupe.py

_INSERT = (
    "INSERT INTO sessions (session_id, task_type, source_path, source_mtime, source_hash, "
    "scored_at, axes_scored, real_tokens, scope_status, baseline_available, band_verdict, "
    "interpretation, token_domain_of_validity, trajectory_domain_of_validity, "
    "waste_event_count, waste_events, waste_domain_of_validity, turn_count, adapter_version) "
    "VALUES (?, 'ml-eval', ?, 0, 'h', 't', '[]', ?, 'in_scope', 1, 'within_band', 'i', 'd', "
    "'d', 0, '[]', 'd', 80, ?)"
)


def _row(conn: sqlite3.Connection, sid: str, src: str, tokens: int, version: int | None) -> None:
    conn.execute(_INSERT, (sid, src, tokens, version))


def test_self_baseline_ignores_pre_dedupe_rows(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = open_db(db)
    for i in range(30):
        _row(conn, f"cur{i}", "x", 100_000 + 1_000 * i, ADAPTER_VERSION)
        _row(conn, f"old{i}", "x", 1_000_000 + 1_000 * i, None)  # ~10x: pre-dedupe inflation
    conn.commit()
    conn.close()

    state = compute_self_baselines(db, load_baselines(BUNDLED_BASELINES_PATH))
    tb = state.by_type["ml-eval"]
    assert tb.source == "self"
    assert tb.p75 is not None and tb.p75 < 200_000  # stale rows would push this past 1M
    assert tb.stale_n == 30
    assert "pre-dedupe" in tb.domain_of_validity


def test_db_without_adapter_column_has_no_self_baseline(tmp_path: Path) -> None:
    """A DB predating the column fails closed: every row is stale, nothing is trusted."""
    db = tmp_path / "legacy.db"
    conn = open_db(db)
    for i in range(30):
        _row(conn, f"s{i}", "x", 100_000 + i, ADAPTER_VERSION)
    conn.commit()
    conn.execute("ALTER TABLE sessions DROP COLUMN adapter_version")
    conn.commit()
    conn.close()
    tb = compute_self_baselines(db, load_baselines(BUNDLED_BASELINES_PATH)).by_type["ml-eval"]
    assert tb.source == "building" and tb.stale_n == 30


def test_open_db_migration_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    open_db(db).close()
    conn = open_db(db)  # second open must not fail on the already-added columns
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert {"adapter_version", "duplicate_usage_records"} <= cols


def test_backfill_refreshes_stale_row_once(tmp_path: Path) -> None:
    src = tmp_path / "proj" / "sess-1.jsonl"
    src.parent.mkdir()
    shutil.copy(FIXTURE, src)
    db = tmp_path / "b.db"
    conn = open_db(db)
    _row(conn, "sess-1", str(src), 999_999, None)
    conn.commit()
    conn.close()

    first = backfill_waste(db_path=db)
    assert first["refreshed"] == 1
    conn = open_db(db)
    row = conn.execute("SELECT * FROM sessions WHERE session_id = 'sess-1'").fetchone()
    assert row["real_tokens"] == EXPECTED_REAL
    assert row["adapter_version"] == ADAPTER_VERSION
    assert row["duplicate_usage_records"] == 3
    assert row["session_cost_usd"] is not None and row["session_cost_usd"] > 0
    assert json.loads(row["waste_events"]) == []
    conn.close()

    assert backfill_waste(db_path=db)["refreshed"] == 0  # already current: untouched


def test_upsert_persists_adapter_version(tmp_path: Path) -> None:
    from tes.adapt import adapt_session
    from tes.score import score_session
    from tes.store import upsert_session

    result = score_session(adapt_session(FIXTURE), load_baselines(BUNDLED_BASELINES_PATH))
    conn = open_db(tmp_path / "u.db")
    upsert_session(conn, result, str(FIXTURE), 0.0, "h", turn_count=7)
    row = conn.execute("SELECT adapter_version, duplicate_usage_records FROM sessions").fetchone()
    assert (row[0], row[1]) == (ADAPTER_VERSION, 3)
