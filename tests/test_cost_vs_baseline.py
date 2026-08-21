from __future__ import annotations

"""tests/test_cost_vs_baseline.py — Tests for cost-vs-baseline framing logic.

Covers the framing string format and ThreeAxisResult cost annotation fields.
Cost is an annotation only — not a score, not part of any composite.
"""

import pytest
from tes.cost import SessionCost
from tes.score import load_baselines, score_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_session_cost(total_usd: float, approximate: bool = False) -> SessionCost:
    return SessionCost(
        session_id="test",
        total_usd=total_usd,
        turn_costs=[],
        approximate=approximate,
        approximate_reasons=[],
        domain_of_validity="test domain",
        ai_turn_count=5,
        approximate_turn_count=1 if approximate else 0,
    )


def format_cost_framing(
    session_cost_usd: float,
    baseline_cost_band: tuple[float, float, float] | None,
) -> str:
    """Format a human-readable cost-vs-baseline framing string.

    Format: "$4.20, 62% above your typical efficient run (~$2.60)"
            "$2.00, 33% below your typical efficient run (~$3.00)"
    Returns "baseline cost unavailable" when band is None.
    """
    if baseline_cost_band is None:
        return "baseline cost unavailable"
    _p25, median_usd, _p75 = baseline_cost_band
    if median_usd == 0:
        return f"${session_cost_usd:.2f}, no baseline"
    pct = (session_cost_usd - median_usd) / median_usd * 100
    direction = "above" if pct >= 0 else "below"
    abs_pct = abs(pct)
    return f"${session_cost_usd:.2f}, {abs_pct:.0f}% {direction} your typical efficient run (~${median_usd:.2f})"


def _pct_vs_median(session_cost_usd: float, median_usd: float) -> float:
    return (session_cost_usd - median_usd) / median_usd * 100


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cost_above_baseline() -> None:
    band = (2.0, 3.0, 4.0)
    pct = _pct_vs_median(5.0, 3.0)
    assert pct == pytest.approx(66.6666, rel=1e-4)


def test_cost_below_baseline() -> None:
    band = (2.0, 3.0, 4.0)
    pct = _pct_vs_median(2.0, 3.0)
    assert pct == pytest.approx(-33.3333, rel=1e-4)


def test_cost_at_median() -> None:
    pct = _pct_vs_median(3.0, 3.0)
    assert pct == pytest.approx(0.0, rel=1e-9)


def test_cost_framing_above() -> None:
    framing = format_cost_framing(4.20, (1.5, 2.60, 3.5))
    assert "above" in framing
    assert "$4.20" in framing
    assert "$2.60" in framing
    # pct = (4.20 - 2.60) / 2.60 * 100 = 61.538... → rounds to 62
    assert "62%" in framing


def test_no_baseline_framing() -> None:
    framing = format_cost_framing(3.50, None)
    assert framing == "baseline cost unavailable"


def test_session_cost_in_three_axis_result() -> None:
    """score_session() with session_cost populates cost fields on ThreeAxisResult."""
    baselines = load_baselines()
    record = {
        "session_id": "test-session-001",
        "turn_count": 50,
        "total_tokens": 120_000,
        "h2_duplicate_count": 0,
        "cache_hit_rate": 0.3,
        "p25_token_ratio": 1.0,
        "output_tokens_available": True,
        "domain_id": "infra",
        "domain_inferred": "infra",
        "test_outcome": False,
        "task_description": "deploy pipeline",
        "digest": {
            "session_id": "test-session-001",
            "domain": "infra",
            "resolved": True,
            "total_tokens": 120_000,
            "turn_count": 50,
            "h2_duplicate_count": 0,
            "cache_hit_rate": 0.3,
            "p25_token_ratio": 1.0,
            "output_tokens_available": True,
            "task_description": "deploy pipeline",
            "turns": [],
        },
    }
    cost = _mock_session_cost(4.20)
    result = score_session(record, baselines, session_cost=cost)
    assert result.session_cost_usd == pytest.approx(4.20, rel=1e-9)
    assert result.cost_approximate is False
    assert result.cost_domain_of_validity == "test domain"


def test_session_cost_approximate_flag() -> None:
    """Approximate flag on SessionCost propagates to ThreeAxisResult."""
    baselines = load_baselines()
    record = {
        "session_id": "test-approx-001",
        "turn_count": 50,
        "total_tokens": 90_000,
        "h2_duplicate_count": 0,
        "cache_hit_rate": 0.2,
        "p25_token_ratio": 1.0,
        "output_tokens_available": True,
        "domain_id": "infra",
        "domain_inferred": "infra",
        "test_outcome": False,
        "task_description": "refactor script",
        "digest": {
            "session_id": "test-approx-001",
            "domain": "infra",
            "resolved": True,
            "total_tokens": 90_000,
            "turn_count": 50,
            "h2_duplicate_count": 0,
            "cache_hit_rate": 0.2,
            "p25_token_ratio": 1.0,
            "output_tokens_available": True,
            "task_description": "refactor script",
            "turns": [],
        },
    }
    cost = _mock_session_cost(2.50, approximate=True)
    result = score_session(record, baselines, session_cost=cost)
    assert result.cost_approximate is True
    assert result.session_cost_usd == pytest.approx(2.50, rel=1e-9)


def test_no_session_cost_defaults() -> None:
    """When session_cost is None, cost fields default to None/False/empty."""
    baselines = load_baselines()
    record = {
        "session_id": "test-no-cost-001",
        "turn_count": 50,
        "total_tokens": 80_000,
        "h2_duplicate_count": 0,
        "cache_hit_rate": 0.2,
        "p25_token_ratio": 1.0,
        "output_tokens_available": True,
        "domain_id": "infra",
        "domain_inferred": "infra",
        "test_outcome": False,
        "task_description": "write tests",
        "digest": {
            "session_id": "test-no-cost-001",
            "domain": "infra",
            "resolved": True,
            "total_tokens": 80_000,
            "turn_count": 50,
            "h2_duplicate_count": 0,
            "cache_hit_rate": 0.2,
            "p25_token_ratio": 1.0,
            "output_tokens_available": True,
            "task_description": "write tests",
            "turns": [],
        },
    }
    result = score_session(record, baselines)
    assert result.session_cost_usd is None
    assert result.cost_approximate is False
    assert result.cost_domain_of_validity == ""
    assert result.cost_unpriced_models is None


def test_cost_unpriced_models_extracted_from_approximate_reasons() -> None:
    """XX1.3: the raw unresolved model name is pulled out of
    approximate_reasons and persisted -- not just the boolean flag."""
    baselines = load_baselines()
    record = {
        "session_id": "test-unpriced-001",
        "turn_count": 50,
        "total_tokens": 90_000,
        "h2_duplicate_count": 0,
        "cache_hit_rate": 0.2,
        "p25_token_ratio": 1.0,
        "output_tokens_available": True,
        "domain_id": "infra",
        "domain_inferred": "infra",
        "test_outcome": False,
        "task_description": "refactor script",
        "digest": {
            "session_id": "test-unpriced-001",
            "domain": "infra",
            "resolved": True,
            "total_tokens": 90_000,
            "turn_count": 50,
            "h2_duplicate_count": 0,
            "cache_hit_rate": 0.2,
            "p25_token_ratio": 1.0,
            "output_tokens_available": True,
            "task_description": "refactor script",
            "turns": [],
        },
    }
    cost = SessionCost(
        session_id="test-unpriced-001",
        total_usd=0.0,
        turn_costs=[],
        approximate=True,
        approximate_reasons=[
            "unknown model 'claude-future-9' — cost unknown, not priced at a guessed/"
            "default rate (known models: claude-sonnet-5, claude-opus-5)",
        ],
        domain_of_validity="test domain",
        ai_turn_count=5,
        approximate_turn_count=5,
    )
    result = score_session(record, baselines, session_cost=cost)
    assert result.cost_unpriced_models == "claude-future-9"


def test_cost_unpriced_models_handles_empty_model_string() -> None:
    baselines = load_baselines()
    record = {
        "session_id": "test-unpriced-002",
        "turn_count": 50,
        "total_tokens": 90_000,
        "h2_duplicate_count": 0,
        "cache_hit_rate": 0.2,
        "p25_token_ratio": 1.0,
        "output_tokens_available": True,
        "domain_id": "infra",
        "domain_inferred": "infra",
        "test_outcome": False,
        "task_description": "refactor script",
        "digest": {
            "session_id": "test-unpriced-002",
            "domain": "infra",
            "resolved": True,
            "total_tokens": 90_000,
            "turn_count": 50,
            "h2_duplicate_count": 0,
            "cache_hit_rate": 0.2,
            "p25_token_ratio": 1.0,
            "output_tokens_available": True,
            "task_description": "refactor script",
            "turns": [],
        },
    }
    cost = SessionCost(
        session_id="test-unpriced-002",
        total_usd=0.0,
        turn_costs=[],
        approximate=True,
        approximate_reasons=[
            "empty model string — cost unknown, not priced at a guessed/default rate"
        ],
        domain_of_validity="test domain",
        ai_turn_count=5,
        approximate_turn_count=5,
    )
    result = score_session(record, baselines, session_cost=cost)
    assert result.cost_unpriced_models == "(empty)"
