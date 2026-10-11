from __future__ import annotations

"""tests/test_cost_note_multiplier.py -- the CLI cost note names the cache-read multiplier the
model was actually billed at (0.10x default, 0.05x Opus/Sonnet 5.5, 0.025x Fable/Mythos 5.1),
not a flat 0.10x, and states the cache-write tier assumption."""

import pytest
from tes._digest import SessionDigest, TurnDigest
from tes.cost import compute_session_cost, load_price_table
from tes.report import format_human
from tes.score import (
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
    ThreeAxisResult,
    _priced_model_keys,
)


def _digest(model: str) -> SessionDigest:
    turn = TurnDigest(
        turn_index=0,
        role="ai",
        tool_names=[],
        content_snippet="",
        token_count_input=20_000,
        token_count_output=1_000,
        cache_read=15_000,
        h2_duplicate=False,
        cache_creation=2_000,
        model=model,
    )
    return SessionDigest(
        session_id="mult-test",
        domain="test",
        resolved=True,
        total_tokens=20_000,
        turn_count=1,
        h2_duplicate_count=0,
        cache_hit_rate=0.75,
        p25_token_ratio=1.0,
        output_tokens_available=True,
        task_description="test",
        turns=[turn],
    )


def _result(cost: float, models: list[str]) -> ThreeAxisResult:
    return ThreeAxisResult(
        session_id="mult-test",
        task_type="research",
        real_tokens=6_000,
        scope_status="out_of_scope",
        baseline_available=False,
        p25=None,
        p75=None,
        median=None,
        band_verdict="unavailable",
        interpretation="n/a",
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        baseline_source="b2_corpus",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning=None,
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        waste_event_count=0,
        waste_events=[],
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
        session_cost_usd=cost,
        cost_approximate=False,
        cost_domain_of_validity="test dov",
        cost_models=models,
    )


@pytest.mark.parametrize(
    ("model", "note"),
    [
        ("claude-opus-5-5", "cache read 0.05× (claude-opus-5-5)"),
        ("claude-sonnet-5-5", "cache read 0.05× (claude-sonnet-5-5)"),
        ("claude-fable-5-1", "cache read 0.025× (claude-fable-5-1)"),
        ("claude-mythos-5-1", "cache read 0.025× (claude-mythos-5-1)"),
        ("claude-sonnet-4-6", "cache read 0.10× (claude-sonnet-4-6)"),
    ],
)
def test_cost_note_names_the_multiplier_the_model_was_billed_at(model: str, note: str) -> None:
    session_cost = compute_session_cost(_digest(model), load_price_table())
    keys = _priced_model_keys(session_cost)
    assert keys == [model]
    out = format_human(_result(session_cost.total_usd, keys))
    assert note in out
    assert "cache writes 1.25× (5m) / 2.00× (1h)" in out
    assert "unspecified assumed 5m" in out


def test_multiplier_in_the_note_reproduces_the_billed_cache_read_cost() -> None:
    """The stated 0.05x is the one compute_turn_cost applied: 15k cache-read tokens at 5% of $4."""
    session_cost = compute_session_cost(_digest("claude-opus-5-5"), load_price_table())
    assert session_cost.turn_costs[0].cache_read_cost == pytest.approx(15_000 * 4.0 * 0.05 / 1e6)


def test_synthetic_stubs_are_not_named_in_the_note() -> None:
    session_cost = compute_session_cost(_digest("<synthetic>"), load_price_table())
    assert _priced_model_keys(session_cost) == []
