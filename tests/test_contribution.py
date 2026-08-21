from __future__ import annotations

"""Tests for tes/contribution.py — allow-listed payload builder."""

import sqlite3
import uuid
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

from tes.contribution import (
    ALLOWED_FIELDS,
    CONTRIBUTION_SCHEMA_VERSION,
    ContributionManifest,
    ContributionPayload,
    _allowed_model,
    _extract_waste_detectors,
    _week_bucket_from_mtime,
    build_contribution_payload,
    get_or_create_contributor_id,
)

# ---------------------------------------------------------------------------
# _week_bucket_from_mtime
# ---------------------------------------------------------------------------


def test_week_bucket_known_date() -> None:
    # 2026-06-09 00:00:00 UTC → 2026-W24
    from datetime import datetime

    mtime = datetime(2026, 6, 9, 0, 0, 0, tzinfo=UTC).timestamp()
    result = _week_bucket_from_mtime(mtime)
    assert result == "2026-W24"


def test_week_bucket_zero_pad() -> None:
    # ISO week 1 of 2026 should be zero-padded to W01
    from datetime import datetime

    # 2026-01-05 is in week 2, 2026-01-01 is in week 1
    mtime = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC).timestamp()
    result = _week_bucket_from_mtime(mtime)
    assert result.endswith("-W01")


# ---------------------------------------------------------------------------
# _allowed_model
# ---------------------------------------------------------------------------


def test_allowed_model_known() -> None:
    assert _allowed_model("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_allowed_model_strips_date_suffix() -> None:
    # date-suffixed model string → strip suffix, then check allow-list
    assert _allowed_model("claude-sonnet-4-6-20250619") == "claude-sonnet-4-6"


def test_allowed_model_unknown_returns_other() -> None:
    assert _allowed_model("gpt-4o") == "other"


def test_allowed_model_empty_returns_other() -> None:
    assert _allowed_model("") == "other"


def test_allowed_model_none_returns_other() -> None:
    assert _allowed_model(None) == "other"


# ---------------------------------------------------------------------------
# _extract_waste_detectors
# ---------------------------------------------------------------------------


def test_extract_waste_detectors_deduplicates_and_sorts() -> None:
    events = [
        {"detector": "REPEATED-FAILED-RETRY", "evidence": "some content"},
        {"detector": "REDUNDANT-READ", "evidence": "other content"},
        {"detector": "REPEATED-FAILED-RETRY", "evidence": "dup content"},
    ]
    result = _extract_waste_detectors(events)
    assert result == ["REDUNDANT-READ", "REPEATED-FAILED-RETRY"]


def test_extract_waste_detectors_empty_list() -> None:
    assert _extract_waste_detectors([]) == []


def test_extract_waste_detectors_no_evidence_leaks() -> None:
    events = [{"detector": "REPEATED-FAILED-RETRY", "evidence": "SECRET", "content": "ALSO_SECRET"}]
    result = _extract_waste_detectors(events)
    assert result == ["REPEATED-FAILED-RETRY"]
    # Verify none of the event values except detector name appear
    assert "SECRET" not in str(result)
    assert "ALSO_SECRET" not in str(result)


def test_extract_waste_detectors_unknown_detector_dropped() -> None:
    """An unknown detector name is silently dropped — never passed through raw."""
    events = [{"detector": "UNKNOWN-CUSTOM-DETECTOR", "evidence": "SECRET"}]
    result = _extract_waste_detectors(events)
    assert result == []
    assert "UNKNOWN-CUSTOM-DETECTOR" not in str(result)


def test_extract_waste_detectors_wrong_key_returns_empty() -> None:
    """Events using the old 'detector_type' key produce no output (wrong key, silently ignored)."""
    events = [{"detector_type": "REPEATED-FAILED-RETRY", "evidence": "content"}]
    result = _extract_waste_detectors(events)
    assert result == []


# ---------------------------------------------------------------------------
# ALLOWED_FIELDS invariants
# ---------------------------------------------------------------------------


def test_allowed_fields_count() -> None:
    assert len(ALLOWED_FIELDS) == 14


def test_allowed_fields_contains_required_keys() -> None:
    expected = {
        "task_type",
        "real_tokens",
        "token_count_input",
        "token_count_output",
        "cache_creation",
        "cache_read",
        "waste_event_count",
        "waste_detectors_fired",
        "model",
        "turn_count",
        "week_bucket",
        "tracegauge_version",
        "schema_version",
        "contributor_id",
    }
    assert expected == ALLOWED_FIELDS


def test_allowed_fields_excludes_sensitive_columns() -> None:
    for banned in ("session_id", "source_path", "scored_at", "judge_reasoning", "interpretation"):
        assert banned not in ALLOWED_FIELDS


# ---------------------------------------------------------------------------
# get_or_create_contributor_id
# ---------------------------------------------------------------------------


def test_get_or_create_contributor_id_creates_file(tmp_path: Path) -> None:
    id_file = tmp_path / ".tes" / "contributor_id.txt"
    with patch("tes.contribution._CONTRIBUTOR_ID_FILE", id_file):
        cid = get_or_create_contributor_id()
    assert id_file.exists()
    assert cid == id_file.read_text(encoding="utf-8").strip()
    # Must be a valid UUID
    uuid.UUID(cid)


def test_get_or_create_contributor_id_stable_on_reread(tmp_path: Path) -> None:
    id_file = tmp_path / ".tes" / "contributor_id.txt"
    with patch("tes.contribution._CONTRIBUTOR_ID_FILE", id_file):
        cid1 = get_or_create_contributor_id()
        cid2 = get_or_create_contributor_id()
    assert cid1 == cid2


# ---------------------------------------------------------------------------
# build_contribution_payload — structural invariants
# ---------------------------------------------------------------------------


def _make_minimal_conn() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with no sessions (empty store)."""
    from tes.store import open_db

    conn = open_db(":memory:")
    return conn


def test_build_contribution_payload_empty_store() -> None:
    conn = _make_minimal_conn()
    payload = build_contribution_payload(conn, contributor_id=None)
    assert isinstance(payload, ContributionPayload)
    assert isinstance(payload.manifest, ContributionManifest)
    assert payload.manifest.row_count == 0
    assert payload.rows == []


def test_build_contribution_payload_manifest_fields() -> None:
    conn = _make_minimal_conn()
    payload = build_contribution_payload(conn, contributor_id="test-uuid-123")
    m = payload.manifest
    assert m.schema_version == CONTRIBUTION_SCHEMA_VERSION
    assert m.contributor_id == "test-uuid-123"
    assert m.fields_included == sorted(ALLOWED_FIELDS)
    assert "session_id" in m.fields_excluded
    assert "source_path" in m.fields_excluded
    assert "judge_reasoning" in m.fields_excluded
    assert m.row_count == 0


def test_build_contribution_payload_anonymous() -> None:
    conn = _make_minimal_conn()
    payload = build_contribution_payload(conn, contributor_id=None)
    assert payload.manifest.contributor_id is None


def test_build_contribution_payload_row_keys_exactly_allowed() -> None:
    """Each row must contain exactly ALLOWED_FIELDS keys — no more, no less."""
    from datetime import datetime

    from tes.score import ThreeAxisResult
    from tes.store import upsert_session

    conn = _make_minimal_conn()
    result = ThreeAxisResult(
        session_id="test-abc",
        task_type="debug",
        real_tokens=1000,
        scope_status="in_scope",
        baseline_available=True,
        p25=800,
        p75=1200,
        median=1000,
        band_verdict="ok",
        interpretation="normal",
        token_domain_of_validity="",
        baseline_source="b2_corpus",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning=None,
        trajectory_domain_of_validity="",
        waste_event_count=0,
        waste_events=[],
        waste_domain_of_validity="",
        session_cost_usd=None,
        cost_approximate=False,
        cost_domain_of_validity=None,
    )
    mtime = datetime(2026, 1, 15, tzinfo=UTC).timestamp()
    upsert_session(conn, result, "/nonexistent/path.jsonl", mtime, "hash123", turn_count=5)

    payload = build_contribution_payload(
        conn, contributor_id="cid-test", include_source_components=False
    )
    assert len(payload.rows) == 1
    row = payload.rows[0]
    assert set(row.keys()) == ALLOWED_FIELDS


def test_build_contribution_payload_no_banned_fields_in_rows() -> None:
    """session_id, source_path, scored_at, judge_reasoning must never appear in rows."""
    from datetime import datetime

    from tes.score import ThreeAxisResult
    from tes.store import upsert_session

    conn = _make_minimal_conn()
    result = ThreeAxisResult(
        session_id="test-banned-check",
        task_type="code_generation",
        real_tokens=500,
        scope_status="in_scope",
        baseline_available=False,
        p25=None,
        p75=None,
        median=None,
        band_verdict="out_of_scope",
        interpretation="no baseline",
        token_domain_of_validity="",
        baseline_source="b2_corpus",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning="SHOULD NOT APPEAR",
        trajectory_domain_of_validity="",
        waste_event_count=1,
        waste_events=[
            {
                "detector": "REDUNDANT-READ",
                "session_id": "test-banned-check",
                "turns": [2, 4],
                "repeat_count": 1,
                "evidence": {"content": "SENSITIVE"},
            }
        ],
        waste_domain_of_validity="",
        session_cost_usd=None,
        cost_approximate=False,
        cost_domain_of_validity=None,
    )
    mtime = datetime(2026, 3, 1, tzinfo=UTC).timestamp()
    upsert_session(conn, result, "/nonexistent/path.jsonl", mtime, "hash456", turn_count=3)

    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)
    row = payload.rows[0]
    for banned in ("session_id", "source_path", "scored_at", "judge_reasoning"):
        assert banned not in row
    # Evidence must not appear in waste_detectors_fired
    assert "SENSITIVE" not in str(row["waste_detectors_fired"])
    assert row["waste_detectors_fired"] == ["REDUNDANT-READ"]
