from __future__ import annotations

"""LEGACY rows stay out of corrected figures (self-baseline, alarm pool) and are never altered."""

from pathlib import Path

from tes.alarm_baseline import load_pool_rows
from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.self_baseline import compute_self_baselines
from tes.store import open_db

from tests.legacy_store import build_mixed_store, insert_row


def test_self_baseline_excludes_legacy_rows_of_either_kind(tmp_path: Path) -> None:
    db = tmp_path / "sb.db"
    conn = open_db(db)
    for i in range(30):
        insert_row(conn, f"cur{i}", tokens=100_000 + 1_000 * i)
        insert_row(conn, f"pre{i}", tokens=1_000_000 + i, adapter_version=None, cost_version=None)
        insert_row(conn, f"cost{i}", tokens=1_000_000 + i, cost_version=None)
    conn.close()
    tb = compute_self_baselines(db, load_baselines(BUNDLED_BASELINES_PATH)).by_type["ml-eval"]
    assert tb.source == "self"
    assert tb.p75 is not None and tb.p75 < 200_000
    assert tb.stale_n == 60


def test_alarm_pool_excludes_legacy_rows(tmp_path: Path) -> None:
    s = build_mixed_store(tmp_path)
    ids = {r.session_id for r in load_pool_rows(s.db)}
    assert ids == set(s.current)


def test_exclusion_never_alters_a_legacy_row(tmp_path: Path) -> None:
    s = build_mixed_store(tmp_path)
    conn = open_db(s.db)
    before = [tuple(r) for r in conn.execute("SELECT * FROM sessions ORDER BY session_id")]
    conn.close()
    compute_self_baselines(s.db, load_baselines(BUNDLED_BASELINES_PATH))
    load_pool_rows(s.db)
    conn = open_db(s.db)
    assert [tuple(r) for r in conn.execute("SELECT * FROM sessions ORDER BY session_id")] == before
