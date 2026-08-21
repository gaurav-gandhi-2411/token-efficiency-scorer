from __future__ import annotations

"""tests/test_contribution_allowlist.py — Payload has ONLY the 14 allow-listed keys.

Any extra key = test failure. Proves the field-by-field construction invariant.
"""

import json
from datetime import UTC, datetime

from tes.contribution import ALLOWED_FIELDS, build_contribution_payload
from tes.score import ThreeAxisResult
from tes.store import open_db, upsert_session


def _make_conn():
    return open_db(":memory:")


def _make_session(conn, session_id: str = "allowlist-test", task_type: str = "debug-fix") -> None:
    result = ThreeAxisResult(
        session_id=session_id,
        task_type=task_type,
        real_tokens=2000,
        scope_status="in_scope",
        baseline_available=True,
        p25=1000,
        p75=3000,
        median=2000,
        band_verdict="within_band",
        interpretation="some interpretation text",
        token_domain_of_validity="",
        baseline_source="b2_corpus",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning="do not include this",
        trajectory_domain_of_validity="",
        waste_event_count=1,
        waste_events=[
            {
                "detector": "REDUNDANT-READ",
                "session_id": session_id,
                "turns": [2, 4],
                "repeat_count": 1,
                "evidence": {"content": "sensitive content here"},
            }
        ],
        waste_domain_of_validity="",
        session_cost_usd=0.01,
        cost_approximate=False,
        cost_domain_of_validity=None,
    )
    mtime = datetime(2026, 6, 10, tzinfo=UTC).timestamp()
    upsert_session(conn, result, "/nonexistent/path.jsonl", mtime, "hash-al", turn_count=25)


def test_each_row_has_exactly_allowed_keys() -> None:
    """Every row must have exactly ALLOWED_FIELDS keys — no more, no less."""
    conn = _make_conn()
    _make_session(conn)
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)
    assert len(payload.rows) == 1
    row = payload.rows[0]
    assert set(row.keys()) == ALLOWED_FIELDS


def test_extra_key_would_be_detected() -> None:
    """Self-check: if a row had an extra key, the test above would catch it."""
    extra_row = {k: None for k in ALLOWED_FIELDS}
    extra_row["EXTRA_SENSITIVE_FIELD"] = "should not be here"
    assert set(extra_row.keys()) != ALLOWED_FIELDS  # confirms the test has teeth


def test_no_extra_keys_across_multiple_sessions() -> None:
    """Multiple sessions — every row has exactly ALLOWED_FIELDS keys."""
    conn = _make_conn()
    for i in range(5):
        _make_session(conn, session_id=f"session-{i}", task_type="ml-eval")
    payload = build_contribution_payload(
        conn, contributor_id="cid-xyz", include_source_components=False
    )
    assert len(payload.rows) == 5
    for row in payload.rows:
        assert set(row.keys()) == ALLOWED_FIELDS, (
            f"Row keys mismatch: {set(row.keys()) ^ ALLOWED_FIELDS}"
        )


def test_manifest_contains_all_allowed_fields_list() -> None:
    conn = _make_conn()
    _make_session(conn)
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)
    assert set(payload.manifest.fields_included) == ALLOWED_FIELDS


def test_manifest_excluded_list_contains_banned_fields() -> None:
    conn = _make_conn()
    _make_session(conn)
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)
    excluded = set(payload.manifest.fields_excluded)
    for banned in ("session_id", "source_path", "judge_reasoning"):
        assert banned in excluded, f"{banned!r} missing from manifest.fields_excluded"


def test_serialized_row_has_no_extra_keys() -> None:
    """Round-trip through JSON: deserialized row still has only allowed keys."""
    conn = _make_conn()
    _make_session(conn)
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)
    row_json = json.dumps(payload.rows[0])
    row_back = json.loads(row_json)
    assert set(row_back.keys()) == ALLOWED_FIELDS
