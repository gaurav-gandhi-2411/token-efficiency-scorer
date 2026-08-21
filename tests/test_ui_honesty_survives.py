from __future__ import annotations

"""test_ui_honesty_survives.py — P9 regression guard.

Asserts all 10 honesty elements render in the redesigned templates.
A future restyle that drops any element will fail this file.

Two render strategies:
- Route strategy: upsert_session → test_client().get(route). Attribution is None
  (source file /tmp/test.jsonl absent). Tests elements 1, 4, 5, 6, 7, 8, 9.
- Mock strategy: render_template() with mock attribution data. Tests elements 2, 3, 10a.
  Also tests 10b (session_list attribution_line prominence) via route.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import render_template
from tes.score import (
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
    ThreeAxisResult,
)
from tes.store import TrajectoryRenderState, open_db, upsert_session
from tes.web.server import ServerConfig, create_app

# ── Shared fixtures ──────────────────────────────────────────────────────────


def _base_result() -> ThreeAxisResult:
    """Session with no judge (UNAVAILABLE traj), 1 waste event, self-baseline."""
    return ThreeAxisResult(
        session_id="honesty-test-001",
        task_type="infra-deploy",
        real_tokens=6_000_000,
        scope_status="in_scope",
        baseline_available=True,
        p25=300_000,
        p75=1_100_000,
        median=600_000,
        band_verdict="above_p75",
        interpretation="Above p75 for infra-deploy.",
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        baseline_source="self",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning=None,
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        waste_event_count=1,
        waste_events=[
            {
                "detector": "REDUNDANT-READ",
                "turns": [14, 22],
                "wasted_cost_usd": 0.12,
                "evidence": {"file": "build.sh"},
            }
        ],
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
    )


@pytest.fixture()
def route_app(tmp_path: Path):
    """App with a seeded test session for route-based rendering."""
    db = tmp_path / "honesty.db"
    conn = open_db(db)
    upsert_session(conn, _base_result(), "/tmp/test.jsonl", 83.11, "abc123")
    conn.close()
    return create_app(ServerConfig(db_path=db))


@pytest.fixture()
def mock_app(tmp_path: Path):
    """Minimal app for render_template()-based tests (no DB sessions needed)."""
    db = tmp_path / "mock.db"
    return create_app(ServerConfig(db_path=db))


# ── Mock data for elements requiring attribution ──────────────────────────────

_TAKEAWAY = (
    "Cost: context (95% re-send + 3% growth) and output (30%); no detectable waste. "
    "— a long context drove most of the cost; checkpointing or /compact mid-session reduces re-send."
)


def _mock_session() -> dict:
    return {
        "session_id": "mock-session-attribution-001",
        "task_type": "infra-deploy",
        "source_path": "/tmp/test.jsonl",
        "scored_at": "2026-06-12T10:00:00",
        "axes_scored": ["token", "waste"],
        "real_tokens": 6_000_000,
        "band_verdict": "above_p75",
        "baseline_available": True,
        "p25": 300_000,
        "median": 600_000,
        "p75": 1_100_000,
        "baseline_source": "self",
        "scope_status": "in_scope",
        "interpretation": "Above p75 for infra-deploy.",
        "token_domain_of_validity": TOKEN_DOMAIN_OF_VALIDITY,
        "trajectory_domain_of_validity": TRAJECTORY_DOMAIN_OF_VALIDITY,
        "waste_domain_of_validity": WASTE_DOMAIN_OF_VALIDITY,
        "waste_event_count": 1,
        "waste_events": [
            {
                "detector": "REDUNDANT-READ",
                "turns": [14, 22],
                "wasted_cost_usd": 0.12,
                "evidence": {"file": "build.sh"},
            },
        ],
        "judge_verdict": None,
        "judge_score": None,
        "judge_reasoning": None,
        "session_cost_usd": 83.11,
        "cost_approximate": False,
        "cost_domain_of_validity": "Cost computed at API-equivalent rates.",
    }


def _mock_attribution():
    """AttributionResult-like object with B3 divergence (95% tok / 49% cost)."""
    return SimpleNamespace(
        total_billed_tokens=10_000_000,
        total_usd=83.11,
        real_tokens=6_000_000,
        domain_of_validity="Attribution basis: over all billed tokens including cached re-reads.",
    )


def _mock_attribution_rows() -> list[dict]:
    return [
        {
            "bucket": "B3",
            "label": "Context re-send (cache reads)",
            "tokens": 9_500_000,
            "tok_pct": 95.0,
            "usd": 40.72,
            "cost_pct": 49.0,
            "is_waste": False,
        },
        {
            "bucket": "B4",
            "label": "Output",
            "tokens": 100_000,
            "tok_pct": 1.0,
            "usd": 24.93,
            "cost_pct": 30.0,
            "is_waste": False,
        },
        {
            "bucket": "B6",
            "label": "Context growth (cache writes)",
            "tokens": 300_000,
            "tok_pct": 3.0,
            "usd": 17.45,
            "cost_pct": 21.0,
            "is_waste": False,
        },
        {
            "bucket": "B5",
            "label": "Fresh input",
            "tokens": 50_000,
            "tok_pct": 0.5,
            "usd": 0.0,
            "cost_pct": 0.0,
            "is_waste": False,
        },
        {
            "bucket": "B1",
            "label": "Redundant-read waste",
            "tokens": 50_000,
            "tok_pct": 0.5,
            "usd": 0.01,
            "cost_pct": 0.0,
            "is_waste": True,
        },
        {
            "bucket": "B2",
            "label": "Retry-loop waste",
            "tokens": 0,
            "tok_pct": 0.0,
            "usd": 0.0,
            "cost_pct": 0.0,
            "is_waste": True,
        },
    ]


# ── Route-based tests (elements 1, 4, 5, 6, 7, 8, 9) ──────────────────────


def test_1a_token_domain_of_validity_in_detail(route_app) -> None:
    """Element 1: token DOV caveat present in session detail."""
    with route_app.test_client() as c:
        body = c.get("/session/honesty-test-001").data.decode()
    assert "Calibrated to a high-waste" in body, "token_domain_of_validity missing"


def test_1b_trajectory_domain_of_validity_in_detail(route_app) -> None:
    """Element 1: trajectory DOV caveat present in session detail."""
    with route_app.test_client() as c:
        body = c.get("/session/honesty-test-001").data.decode()
    assert "Positive signal" in body, "trajectory_domain_of_validity missing"


def test_1c_waste_domain_of_validity_in_detail(route_app) -> None:
    """Element 1: waste DOV caveat present in session detail."""
    with route_app.test_client() as c:
        body = c.get("/session/honesty-test-001").data.decode()
    assert "Observable-invariant waste" in body, "waste_domain_of_validity missing"


def test_4a_unavailable_uses_neutral_badge_in_detail(route_app) -> None:
    """Element 4: UNAVAILABLE uses badge-unavailable (calm/gray), not an error class."""
    with route_app.test_client() as c:
        body = c.get("/session/honesty-test-001").data.decode()
    assert "badge-unavailable" in body
    assert "badge-error" not in body
    assert "alert-danger" not in body


def test_4b_unavailable_present_in_list(route_app) -> None:
    """Element 4: UNAVAILABLE renders in the list without alarm styling."""
    with route_app.test_client() as c:
        body = c.get("/").data.decode()
    assert "badge-unavailable" in body
    assert "badge-error" not in body


def test_5a_relative_framing_in_detail(route_app) -> None:
    """Element 5: 'relative to your own baseline' framing in session detail."""
    with route_app.test_client() as c:
        body = c.get("/session/honesty-test-001").data.decode()
    assert "your own lean" in body


def test_5b_relative_framing_in_list(route_app) -> None:
    """Element 5: relative framing caveat present on session list."""
    with route_app.test_client() as c:
        body = c.get("/").data.decode()
    assert "your own lean" in body


def test_6a_baseline_source_label_in_detail(route_app) -> None:
    """Element 6: baseline-source label (self/building/corpus) in session detail."""
    with route_app.test_client() as c:
        body = c.get("/session/honesty-test-001").data.decode()
    assert "badge-self" in body


def test_6b_baseline_source_label_in_list(route_app) -> None:
    """Element 6: baseline-source badge in session list rows."""
    with route_app.test_client() as c:
        body = c.get("/").data.decode()
    assert "badge-self" in body


def test_7a_waste_proof_turns_collapsed_signal(route_app) -> None:
    """Element 7: collapsed waste state signals existence with 'show proof turns'."""
    with route_app.test_client() as c:
        body = c.get("/session/honesty-test-001").data.decode()
    assert "show proof turns" in body


def test_7b_waste_evidence_accessible(route_app) -> None:
    """Element 7: waste evidence (detector name, turns) present in detail."""
    with route_app.test_client() as c:
        body = c.get("/session/honesty-test-001").data.decode()
    assert "REDUNDANT-READ" in body
    assert "14" in body


def test_8_api_judge_egress_warning(route_app) -> None:
    """Element 8: API-judge 'may contain your code' egress warning present
    in the UNAVAILABLE trajectory section (the decision point for enabling it)."""
    with route_app.test_client() as c:
        body = c.get("/session/honesty-test-001").data.decode()
    assert "may contain your code" in body


def test_9a_no_composite_score_in_detail(route_app) -> None:
    """Element 9: no composite/blended single score invented in detail."""
    with route_app.test_client() as c:
        body = c.get("/session/honesty-test-001").data.decode()
    low = body.lower()
    assert "composite score" not in low
    assert "blended score" not in low
    assert "efficiency score" not in low
    assert "overall score" not in low


def test_9b_no_composite_score_in_list(route_app) -> None:
    """Element 9: no composite score on list page."""
    with route_app.test_client() as c:
        body = c.get("/").data.decode()
    low = body.lower()
    assert "composite score" not in low
    assert "blended score" not in low


def test_9c_no_composite_score_in_trends(route_app) -> None:
    """Element 9: no composite score on trends (parked) page."""
    with route_app.test_client() as c:
        body = c.get("/trends").data.decode()
    low = body.lower()
    assert "composite" not in low
    assert "blended" not in low


def test_10b_takeaway_prominent_in_list(route_app) -> None:
    """Element 10: attribution_line rendered with font-weight:600 (prominent) in list."""
    with route_app.test_client() as c:
        body = c.get("/").data.decode()
    # The attribution_line link must use font-weight:600 (not subdued gray)
    assert "font-weight:600" in body


# ── Mock render tests (elements 2, 3, 10a — require attribution data) ────────


def _render_detail_with_attribution(mock_app) -> str:
    """Render session_detail.html with mock attribution data (no DB needed)."""
    with mock_app.test_request_context("/session/mock-session-attribution-001"):
        return render_template(
            "session_detail.html",
            session=_mock_session(),
            traj_state=TrajectoryRenderState.UNAVAILABLE,
            TrajectoryRenderState=TrajectoryRenderState,
            price_provenance="claude-3-haiku: $0.25/MTok input",
            cost_band=None,
            cost_vs_baseline_pct=None,
            baseline_cost_median=None,
            attribution=_mock_attribution(),
            attribution_takeaway=_TAKEAWAY,
            attribution_rows=_mock_attribution_rows(),
        )


def test_2_dollar_and_token_pct_both_visible(mock_app) -> None:
    """Element 2: both Cost% and Tok% columns present in attribution table.
    The divergence (95% tok / 49% cost) IS the insight — only one must not appear."""
    body = _render_detail_with_attribution(mock_app)
    assert "Cost% ▼" in body, "Cost% column missing from attribution table"
    assert "Tok%" in body, "Tok% column missing from attribution table"


def test_3a_billed_basis_label(mock_app) -> None:
    """Element 3: 'over all billed tokens' basis label present."""
    body = _render_detail_with_attribution(mock_app)
    assert "total billed tokens" in body


def test_3b_real_tokens_differs_note(mock_app) -> None:
    """Element 3: 'do not compare' note distinguishing attribution from verdict."""
    body = _render_detail_with_attribution(mock_app)
    assert "do not compare" in body


def test_10a_takeaway_prominent_in_detail(mock_app) -> None:
    """Element 10: takeaway rendered with class='takeaway' at the top of detail page,
    and the takeaway text is present."""
    body = _render_detail_with_attribution(mock_app)
    assert 'class="takeaway"' in body
    assert _TAKEAWAY[:50] in body
