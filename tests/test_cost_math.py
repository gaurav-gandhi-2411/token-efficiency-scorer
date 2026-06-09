from __future__ import annotations

"""tests/test_cost_math.py — Exact-dollar tests for tes.cost computation.

Uses a hardcoded price dict to avoid any dependency on file I/O or the bundled
prices.json. All float comparisons use pytest.approx(rel=1e-9).
"""

import pytest

from tes._digest import SessionDigest, TurnDigest
from tes.cost import compute_session_cost, compute_turn_cost

PRICES: dict = {
    "as_of": "2026-06-09",
    "cache_multipliers": {"read": 0.1, "write_5min": 1.25, "write_1hr": 2.0},
    "models": {
        "claude-opus-4-8": {"input_usd_per_mtok": 5.0, "output_usd_per_mtok": 25.0},
        "claude-opus-4-7": {"input_usd_per_mtok": 5.0, "output_usd_per_mtok": 25.0},
        "claude-sonnet-4-6": {"input_usd_per_mtok": 3.0, "output_usd_per_mtok": 15.0},
        "claude-haiku-4-5": {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 5.0},
    },
    "model_patterns": [
        {"prefix": "claude-opus-4", "model_key": "claude-opus-4-8"},
        {"prefix": "claude-sonnet-4-6", "model_key": "claude-sonnet-4-6"},
        {"prefix": "claude-haiku-4-5", "model_key": "claude-haiku-4-5"},
    ],
    "default_model": "claude-sonnet-4-6",
    "approximate_threshold_pct": 25,
}


def _turn(
    *,
    index: int = 0,
    role: str = "ai",
    token_count_input: int,
    token_count_output: int,
    cache_read: int,
    cache_creation: int,
    model: str,
) -> TurnDigest:
    return TurnDigest(
        turn_index=index,
        role=role,
        tool_names=[],
        content_snippet="",
        token_count_input=token_count_input,
        token_count_output=token_count_output,
        cache_read=cache_read,
        h2_duplicate=False,
        cache_creation=cache_creation,
        model=model,
    )


# ---------------------------------------------------------------------------
# Test 1 — Sonnet, all three token classes, 5-min cache
# ---------------------------------------------------------------------------

def test_sonnet_all_classes_5min() -> None:
    turn = _turn(
        token_count_input=1_000_000,
        token_count_output=100_000,
        cache_read=200_000,
        cache_creation=300_000,
        model="claude-sonnet-4-6",
    )
    tc = compute_turn_cost(turn, PRICES, cache_duration="5min")

    assert tc.fresh_tokens == 500_000
    assert tc.fresh_cost == pytest.approx(1.5, rel=1e-9)
    assert tc.cache_read_cost == pytest.approx(0.06, rel=1e-9)
    assert tc.cache_creation_cost == pytest.approx(1.125, rel=1e-9)
    assert tc.output_cost == pytest.approx(1.5, rel=1e-9)
    assert tc.total_usd == pytest.approx(4.185, rel=1e-9)


# ---------------------------------------------------------------------------
# Test 2 — Opus, all three token classes, 5-min cache
# ---------------------------------------------------------------------------

def test_opus_all_classes_5min() -> None:
    turn = _turn(
        token_count_input=1_000_000,
        token_count_output=50_000,
        cache_read=300_000,
        cache_creation=300_000,
        model="claude-opus-4-7",
    )
    tc = compute_turn_cost(turn, PRICES, cache_duration="5min")

    assert tc.fresh_tokens == 400_000
    assert tc.fresh_cost == pytest.approx(2.0, rel=1e-9)
    assert tc.cache_read_cost == pytest.approx(0.15, rel=1e-9)
    assert tc.cache_creation_cost == pytest.approx(1.875, rel=1e-9)
    assert tc.output_cost == pytest.approx(1.25, rel=1e-9)
    assert tc.total_usd == pytest.approx(5.275, rel=1e-9)


# ---------------------------------------------------------------------------
# Test 3 — Haiku, all three token classes, 5-min cache
# ---------------------------------------------------------------------------

def test_haiku_all_classes_5min() -> None:
    turn = _turn(
        token_count_input=500_000,
        token_count_output=50_000,
        cache_read=100_000,
        cache_creation=200_000,
        model="claude-haiku-4-5",
    )
    tc = compute_turn_cost(turn, PRICES, cache_duration="5min")

    assert tc.fresh_tokens == 200_000
    assert tc.fresh_cost == pytest.approx(0.2, rel=1e-9)
    assert tc.cache_read_cost == pytest.approx(0.01, rel=1e-9)
    assert tc.cache_creation_cost == pytest.approx(0.25, rel=1e-9)
    assert tc.output_cost == pytest.approx(0.25, rel=1e-9)
    assert tc.total_usd == pytest.approx(0.71, rel=1e-9)


# ---------------------------------------------------------------------------
# Test 4 — 1-hr cache creation rate (Sonnet)
# ---------------------------------------------------------------------------

def test_sonnet_1hr_cache_creation() -> None:
    turn = _turn(
        token_count_input=1_000_000,
        token_count_output=100_000,
        cache_read=200_000,
        cache_creation=300_000,
        model="claude-sonnet-4-6",
    )
    tc = compute_turn_cost(turn, PRICES, cache_duration="1hr")

    # cache_creation_cost = 300_000 × (3.0 × 2.0) / 1_000_000 = 1.8
    assert tc.cache_creation_cost == pytest.approx(1.8, rel=1e-9)
    assert tc.total_usd == pytest.approx(4.86, rel=1e-9)


# ---------------------------------------------------------------------------
# Test 5 — Mixed-model session (2 AI turns)
# ---------------------------------------------------------------------------

def test_mixed_model_session() -> None:
    turn1 = _turn(
        index=0,
        token_count_input=1_000_000,
        token_count_output=100_000,
        cache_read=200_000,
        cache_creation=300_000,
        model="claude-sonnet-4-6",
    )
    turn2 = _turn(
        index=1,
        token_count_input=1_000_000,
        token_count_output=50_000,
        cache_read=300_000,
        cache_creation=300_000,
        model="claude-opus-4-7",
    )
    digest = SessionDigest(
        session_id="test-mixed",
        domain="test",
        resolved=True,
        total_tokens=2_000_000,
        turn_count=2,
        h2_duplicate_count=0,
        cache_hit_rate=0.5,
        p25_token_ratio=1.0,
        output_tokens_available=True,
        task_description="test task",
        turns=[turn1, turn2],
    )
    sc = compute_session_cost(digest, PRICES, cache_duration="5min")

    assert sc.total_usd == pytest.approx(9.46, rel=1e-9)
    assert sc.approximate is False


# ---------------------------------------------------------------------------
# Test 6 — Date-suffixed model string resolves correctly
# ---------------------------------------------------------------------------

def test_date_suffixed_model_resolves() -> None:
    turn = _turn(
        token_count_input=1_000_000,
        token_count_output=100_000,
        cache_read=200_000,
        cache_creation=300_000,
        model="claude-sonnet-4-6-20251022",
    )
    tc = compute_turn_cost(turn, PRICES, cache_duration="5min")

    # Must resolve to sonnet rates, NOT approximate
    assert tc.model_key == "claude-sonnet-4-6"
    assert tc.is_approximate is False
    assert tc.total_usd == pytest.approx(4.185, rel=1e-9)


# ---------------------------------------------------------------------------
# Test 7 — fresh_tokens clamped to 0
# ---------------------------------------------------------------------------

def test_fresh_tokens_clamped_to_zero() -> None:
    turn = _turn(
        token_count_input=100,
        token_count_output=10,
        cache_read=80,
        cache_creation=30,   # sum > input — impossible in reality, must not go negative
        model="claude-sonnet-4-6",
    )
    tc = compute_turn_cost(turn, PRICES, cache_duration="5min")

    assert tc.fresh_tokens == 0
    assert tc.fresh_cost == pytest.approx(0.0, rel=1e-9)
    # cache_read_cost  = 80 × 0.3 / 1_000_000 = 0.000024
    # cache_creation_cost = 30 × 3.75 / 1_000_000 = 0.00011250
    # output_cost = 10 × 15.0 / 1_000_000 = 0.00015
    assert tc.cache_read_cost == pytest.approx(0.000024, rel=1e-9)
    assert tc.cache_creation_cost == pytest.approx(0.00011250, rel=1e-9)
    assert tc.output_cost == pytest.approx(0.00015, rel=1e-9)
    assert tc.total_usd == pytest.approx(0.00028650, rel=1e-9)
