from __future__ import annotations

"""tests/test_takeaway.py -- the shared takeaway / lever-hint logic (tes/takeaway.py)."""

from tes.attribution import AttributionResult
from tes.takeaway import build_attribution_takeaway, build_lever_hint


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
    """No duplicated logic: the dashboard's takeaway IS the shared function."""
    import tes.web.server as server

    assert server.build_attribution_takeaway is build_attribution_takeaway
    assert not hasattr(server, "_build_attribution_takeaway")


def test_lever_fires_context() -> None:
    hint = build_lever_hint(_attr(resend=7.0, growth=0.5, output=1.0, fresh=1.5))
    assert hint is not None
    assert "checkpointing or /compact" in hint
    assert "70% re-send" in hint  # impact is carried in the text, not just the fix


def test_lever_fires_waste_with_dollars() -> None:
    hint = build_lever_hint(_attr(resend=2.0, output=2.0, fresh=1.0, rr=1.0))
    assert hint is not None
    assert "$1.00 in detectable waste" in hint


def test_lever_fires_output() -> None:
    hint = build_lever_hint(_attr(resend=2.0, output=5.0, fresh=3.0))
    assert hint is not None
    assert "shorter responses" in hint


def test_lever_does_not_fire_when_balanced() -> None:
    assert build_lever_hint(_attr(resend=3.0, output=3.0, fresh=4.0)) is None


def test_lever_does_not_fire_on_small_waste_noise() -> None:
    # $0.02 of waste in a $10 session is below both the absolute and the relative threshold.
    assert build_lever_hint(_attr(resend=3.0, output=3.0, fresh=3.98, rr=0.02)) is None


def test_lever_matches_dashboard_text_when_it_fires() -> None:
    attr = _attr(resend=8.0, output=1.0, fresh=1.0)
    assert build_lever_hint(attr) == build_attribution_takeaway(attr)


def test_unpriced_session_says_levers_unavailable_not_silence() -> None:
    hint = build_lever_hint(_attr(), ("claude-test-unpriced-9",))
    assert hint is not None
    assert "levers unavailable" in hint
    assert "unpriced (claude-test-unpriced-9)" in hint


def test_unpriced_session_without_models_named_is_quiet() -> None:
    # Zero cost and nothing unpriced is a genuinely empty session, not an unpriced one.
    assert build_lever_hint(_attr()) is None


def test_partially_priced_hint_states_it_is_a_priced_subtotal() -> None:
    hint = build_lever_hint(_attr(resend=8.0, output=1.0, fresh=1.0), ("claude-test-unpriced-9",))
    assert hint is not None
    assert "priced part only" in hint
