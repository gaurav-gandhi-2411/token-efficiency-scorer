from __future__ import annotations

"""tests/test_store.py — Unit tests for tes/store.py SQLite persistence layer."""

import threading
from pathlib import Path

import pytest

from tes.score import (
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
    ThreeAxisResult,
)
from tes.store import (
    SCHEMA_VERSION,
    get_session,
    list_sessions,
    needs_scoring,
    open_db,
    upsert_session,
)


# ---------------------------------------------------------------------------
# Synthetic fixture
# ---------------------------------------------------------------------------


def _make_result(session_id: str = "sess-001", with_judge: bool = True) -> ThreeAxisResult:
    return ThreeAxisResult(
        session_id=session_id,
        task_type="debug-fix",
        real_tokens=520_000,
        scope_status="in_scope",
        baseline_available=True,
        p25=353_000,
        p75=654_000,
        median=524_000,
        band_verdict="within_band",
        interpretation="Within the debug-fix band.",
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        judge_verdict="MUCH_BETTER" if with_judge else None,
        judge_score=0.82 if with_judge else None,
        judge_reasoning="Efficient trajectory." if with_judge else None,
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        waste_event_count=1,
        waste_events=[
            {
                "detector": "REPEATED-FAILED-RETRY",
                "turns": [14, 15, 18, 19],
                "repeat_count": 3,
                "evidence": {"error_snippet": "Connection refused"},
            }
        ],
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_round_trip(tmp_path: Path) -> None:
    """Upsert a result and read it back; every field must match."""
    conn = open_db(tmp_path / "tes.db")
    result = _make_result(with_judge=True)

    upsert_session(conn, result, source_path="/tmp/sess.jsonl", source_mtime=1.0, source_hash="abc")
    row = get_session(conn, "sess-001")

    assert row is not None
    assert row["session_id"] == result.session_id
    assert row["task_type"] == result.task_type
    assert row["real_tokens"] == result.real_tokens
    assert row["scope_status"] == result.scope_status
    assert row["baseline_available"] is True
    assert row["p25"] == result.p25
    assert row["p75"] == result.p75
    assert row["median"] == result.median
    assert row["band_verdict"] == result.band_verdict
    assert row["interpretation"] == result.interpretation
    assert row["token_domain_of_validity"] == TOKEN_DOMAIN_OF_VALIDITY
    assert row["judge_verdict"] == result.judge_verdict
    assert row["judge_score"] == result.judge_score
    assert row["judge_reasoning"] == result.judge_reasoning
    assert row["trajectory_domain_of_validity"] == TRAJECTORY_DOMAIN_OF_VALIDITY
    assert row["waste_event_count"] == result.waste_event_count
    assert row["waste_domain_of_validity"] == WASTE_DOMAIN_OF_VALIDITY
    # Proof-turns intact after JSON round-trip.
    assert row["waste_events"][0]["turns"] == [14, 15, 18, 19]
    assert row["judge_stale"] is False


def test_ledger_unchanged(tmp_path: Path) -> None:
    """needs_scoring returns False on matching hash, True on changed or absent session."""
    conn = open_db(tmp_path / "tes.db")
    result = _make_result()

    upsert_session(conn, result, source_path="/tmp/sess.jsonl", source_mtime=1.0, source_hash="abc123")

    assert needs_scoring(conn, "sess-001", "abc123") is False
    assert needs_scoring(conn, "sess-001", "def456") is True
    assert needs_scoring(conn, "sess-not-seen", "any") is True


def test_merge_full_update_with_judge(tmp_path: Path) -> None:
    """Full UPDATE when both old and new rows carry judge data."""
    conn = open_db(tmp_path / "tes.db")

    result_a = ThreeAxisResult(
        session_id="sess-001",
        task_type="debug-fix",
        real_tokens=400_000,
        scope_status="in_scope",
        baseline_available=True,
        p25=353_000, p75=654_000, median=524_000,
        band_verdict="within_band",
        interpretation="Within the debug-fix band.",
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        judge_verdict="BETTER",
        judge_score=0.5,
        judge_reasoning="Acceptable.",
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        waste_event_count=0,
        waste_events=[],
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
    )
    upsert_session(conn, result_a, source_path="/tmp/s.jsonl", source_mtime=1.0, source_hash="hash_a")

    result_b = ThreeAxisResult(
        session_id="sess-001",
        task_type="debug-fix",
        real_tokens=520_000,
        scope_status="in_scope",
        baseline_available=True,
        p25=353_000, p75=654_000, median=524_000,
        band_verdict="within_band",
        interpretation="Within the debug-fix band.",
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        judge_verdict="MUCH_BETTER",
        judge_score=0.9,
        judge_reasoning="Efficient.",
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        waste_event_count=0,
        waste_events=[],
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
    )
    upsert_session(conn, result_b, source_path="/tmp/s.jsonl", source_mtime=2.0, source_hash="new_hash")

    row = get_session(conn, "sess-001")
    assert row is not None
    assert row["judge_verdict"] == "MUCH_BETTER"
    assert row["judge_score"] == 0.9
    assert row["source_hash"] == "new_hash"
    assert row["judge_stale"] is False


def test_merge_preserve_judge_stale(tmp_path: Path) -> None:
    """Partial UPDATE preserves judge fields and marks them stale when hash changes."""
    conn = open_db(tmp_path / "tes.db")

    result_a = ThreeAxisResult(
        session_id="sess-001",
        task_type="debug-fix",
        real_tokens=400_000,
        scope_status="in_scope",
        baseline_available=True,
        p25=353_000, p75=654_000, median=524_000,
        band_verdict="within_band",
        interpretation="Within the debug-fix band.",
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        judge_verdict="BETTER",
        judge_score=0.6,
        judge_reasoning="Good enough.",
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        waste_event_count=0,
        waste_events=[],
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
    )
    upsert_session(conn, result_a, source_path="/tmp/s.jsonl", source_mtime=1.0, source_hash="hash_v1")

    result_b = ThreeAxisResult(
        session_id="sess-001",
        task_type="debug-fix",
        real_tokens=520_000,
        scope_status="in_scope",
        baseline_available=True,
        p25=353_000, p75=654_000, median=524_000,
        band_verdict="within_band",
        interpretation="Within the debug-fix band.",
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        judge_verdict=None,
        judge_score=None,
        judge_reasoning=None,
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        waste_event_count=0,
        waste_events=[],
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
    )
    upsert_session(conn, result_b, source_path="/tmp/s.jsonl", source_mtime=2.0, source_hash="hash_v2")

    row = get_session(conn, "sess-001")
    assert row is not None
    # Judge fields preserved from result_a.
    assert row["judge_verdict"] == "BETTER"
    assert row["judge_score"] == 0.6
    # Ledger updated to new file state.
    assert row["source_hash"] == "hash_v2"
    # Staleness: judge_source_hash (hash_v1) != source_hash (hash_v2).
    assert row["judge_stale"] is True
    # axes_scored reflects what the last pass ran (no judge this time).
    assert row["axes_scored"] == ["token", "waste"]


def test_schema_version(tmp_path: Path) -> None:
    """PRAGMA user_version must equal SCHEMA_VERSION after DB creation."""
    conn = open_db(tmp_path / "tes.db")
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION


def test_wal_concurrent_access(tmp_path: Path) -> None:
    """WAL mode: concurrent write+read from separate threads does not raise OperationalError."""
    errors: list[Exception] = []

    # Pre-create the DB so both threads can open it cleanly.
    init_conn = open_db(tmp_path / "tes.db")
    init_conn.close()

    def writer() -> None:
        try:
            conn = open_db(tmp_path / "tes.db")
            for i in range(50):
                r = _make_result(session_id=f"w-sess-{i:03d}")
                upsert_session(conn, r, source_path=f"/tmp/{i}.jsonl",
                               source_mtime=float(i), source_hash=f"hash{i}")
            conn.close()
        except Exception as exc:
            errors.append(exc)

    def reader() -> None:
        try:
            conn = open_db(tmp_path / "tes.db")
            for _ in range(50):
                list_sessions(conn)
            conn.close()
        except Exception as exc:
            errors.append(exc)

    t_w = threading.Thread(target=writer)
    t_r = threading.Thread(target=reader)
    t_r.start()
    t_w.start()
    t_r.join(timeout=30)
    t_w.join(timeout=30)
    assert not errors, f"Concurrent access errors: {errors}"
