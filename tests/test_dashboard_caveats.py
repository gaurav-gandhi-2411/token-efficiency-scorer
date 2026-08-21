from __future__ import annotations

from pathlib import Path

from tes.score import (
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
    ThreeAxisResult,
)
from tes.store import open_db, upsert_session
from tes.web.server import ServerConfig, create_app


def _make_judge_absent_result() -> ThreeAxisResult:
    return ThreeAxisResult(
        session_id="dash-test-001",
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
        baseline_source="b2_corpus",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning=None,
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


def test_session_detail_carries_all_caveats(tmp_path: Path) -> None:
    """Session detail page must include all three domain-of-validity strings."""
    db_path = tmp_path / "tes.db"
    conn = open_db(db_path)
    result = _make_judge_absent_result()
    upsert_session(conn, result, "/tmp/test.jsonl", 1.0, "abc123")
    conn.close()

    config = ServerConfig(host="127.0.0.1", port=9002, db_path=db_path)
    app = create_app(config)

    with app.test_client() as client:
        resp = client.get("/session/dash-test-001")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")

    # All three domain-of-validity strings must be present verbatim (first ~40 chars)
    assert "Calibrated to a high-waste" in body, "token_domain_of_validity missing"
    assert "Positive signal" in body, "trajectory_domain_of_validity missing"
    assert "Observable-invariant waste" in body, "waste_domain_of_validity missing"

    # UNAVAILABLE must appear (no judge)
    assert "UNAVAILABLE" in body

    # No composite score blending axes
    assert "composite" not in body.lower()
    assert "efficiency score" not in body.lower()

    # Waste proof-turns must be present
    assert "14" in body and "15" in body  # turns [14,15,18,19] in the proof


def test_session_list_renders(tmp_path: Path) -> None:
    """Session list page returns 200 and contains session data."""
    db_path = tmp_path / "tes.db"
    conn = open_db(db_path)
    result = _make_judge_absent_result()
    upsert_session(conn, result, "/tmp/test.jsonl", 1.0, "abc123")
    conn.close()

    config = ServerConfig(host="127.0.0.1", port=9003, db_path=db_path)
    app = create_app(config)

    with app.test_client() as client:
        resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "dash-test-001" in body
    assert "debug-fix" in body


def test_trends_page_no_composite_score(tmp_path: Path) -> None:
    """Trends page must not contain composite/blended efficiency score."""
    db_path = tmp_path / "tes.db"
    conn = open_db(db_path)
    result = _make_judge_absent_result()
    upsert_session(conn, result, "/tmp/test.jsonl", 1.0, "abc123")
    conn.close()

    config = ServerConfig(host="127.0.0.1", port=9004, db_path=db_path)
    app = create_app(config)

    with app.test_client() as client:
        resp = client.get("/trends")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "composite" not in body.lower()
    assert "blended" not in body.lower()
