from __future__ import annotations

"""tests/test_takeaway.py -- the shared finding logic (tes/takeaway.py).

A lever hint ("finding") is an absolute rule that is quiet for most sessions; "context dominates"
is never one (it held for 76 of 76 audited sessions, so it said nothing about any of them). The
informational breakdown has its own tests in tests/test_cost_breakdown.py.
"""

import pytest
from tes.attribution import AttributionResult
from tes.takeaway import build_lever_hint


def _attr(
    *,
    resend: float = 0.0,
    growth: float = 0.0,
    output: float = 0.0,
    fresh: float = 0.0,
    rr: float = 0.0,
    rfr: float = 0.0,
) -> AttributionResult:
    """An AttributionResult with the given USD buckets (tokens are irrelevant to the levers)."""
    total = resend + growth + output + fresh + rr + rfr
    return AttributionResult(
        session_id="s",
        rr_waste_tokens=0,
        rfr_waste_tokens=0,
        context_resend_tokens=0,
        output_tokens=0,
        fresh_input_tokens=0,
        context_growth_tokens=0,
        rr_waste_usd=rr,
        rfr_waste_usd=rfr,
        context_resend_usd=resend,
        output_usd=output,
        fresh_input_usd=fresh,
        context_growth_usd=growth,
        total_billed_tokens=0,
        total_usd=total,
        real_tokens=0,
        domain_of_validity="d",
    )


def test_dashboard_uses_the_shared_function() -> None:
    """No duplicated logic: the dashboard's finding IS the shared function."""
    import tes.web.server as server

    assert server.build_lever_hint is build_lever_hint


@pytest.mark.parametrize(("resend", "growth"), [(7.0, 0.5), (9.9, 0.0), (0.1, 9.0), (6.0, 3.9)])
def test_context_dominance_is_never_a_finding(resend: float, growth: float) -> None:
    """Whatever the context share (up to 99%), no lever fires."""
    attr = _attr(resend=resend, growth=growth, output=0.05, fresh=0.05)
    assert build_lever_hint(attr) is None


def test_waste_finding_carries_dollars_share_and_points_at_proof_turns() -> None:
    hint = build_lever_hint(_attr(resend=2.0, output=2.0, fresh=1.0, rr=1.0))
    assert hint is not None
    assert "$1.00 (17% of the priced cost) in detectable waste" in hint
    assert "proof turns" in hint
    assert "context" not in hint.lower() and "compact" not in hint.lower()


def test_waste_finding_threshold_is_absolute() -> None:
    assert build_lever_hint(_attr(resend=9.0, fresh=0.5, rr=0.5)) is not None  # $0.50
    assert build_lever_hint(_attr(resend=9.0, fresh=0.51, rr=0.49)) is None  # just under
    # relative path: >= 10% of cost and >= $0.05
    assert build_lever_hint(_attr(resend=0.4, fresh=0.05, rr=0.05)) is not None
    assert build_lever_hint(_attr(resend=0.4, fresh=0.06, rr=0.04)) is None  # under the floor


def test_output_finding_fires_at_forty_percent_even_with_big_context() -> None:
    hint = build_lever_hint(_attr(resend=3.0, growth=1.0, output=6.0))
    assert hint is not None and "Output was 60% of the priced cost" in hint
    assert build_lever_hint(_attr(resend=6.1, output=3.9)) is None  # 39%


def test_lever_does_not_fire_when_balanced() -> None:
    assert build_lever_hint(_attr(resend=3.0, output=3.0, fresh=4.0)) is None


def test_lever_does_not_fire_on_small_waste_noise() -> None:
    # $0.02 of waste in a $10 session is below both the absolute and the relative threshold.
    assert build_lever_hint(_attr(resend=3.0, output=3.0, fresh=3.98, rr=0.02)) is None


def test_unpriced_session_has_no_finding() -> None:
    # The breakdown (not the lever) says "not computed": silence here is not "no problem".
    assert build_lever_hint(_attr(), ("claude-test-unpriced-9",)) is None


def test_partially_priced_finding_states_it_is_a_priced_subtotal() -> None:
    hint = build_lever_hint(
        _attr(resend=2.0, output=2.0, fresh=1.0, rr=1.0), ("claude-test-unpriced-9",)
    )
    assert hint is not None and "priced part only" in hint
