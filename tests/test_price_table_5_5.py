from __future__ import annotations

"""Pricing of the 5.5-generation Claude models and the per-model price-schema extensions
(``cache_read_multiplier``, ``long_context``), plus explicit ``<synthetic>`` handling.

Every expected dollar figure below is hand-computed from the official pricing page
(https://platform.claude.com/docs/en/about-claude/pricing, retrieved 2026-10-09), not read back
from the code under test. Reference turn: 1,000,000 prompt tokens of which 600,000 are cache
reads and 100,000 cache writes (5m) -> 300,000 fresh input tokens; 200,000 output tokens.
"""

import pytest
from tes._digest import SessionDigest, TurnDigest
from tes.cost import (
    NON_MODEL_IDS,
    _resolve_model,
    cache_read_multiplier,
    compute_session_cost,
    compute_turn_cost,
    load_price_table,
)

_PRICES = load_price_table()


def _turn(
    model: str,
    *,
    prompt: int = 1_000_000,
    cache_read: int = 600_000,
    cache_creation: int = 100_000,
    output: int = 200_000,
    request_count: int = 1,
) -> TurnDigest:
    return TurnDigest(
        turn_index=0,
        role="ai",
        tool_names=[],
        content_snippet="",
        token_count_input=prompt,
        token_count_output=output,
        cache_read=cache_read,
        h2_duplicate=False,
        cache_creation=cache_creation,
        model=model,
        request_count=request_count,
    )


def _session(turns: list[TurnDigest], subagent: list[TurnDigest] | None = None) -> SessionDigest:
    return SessionDigest(
        session_id="t",
        domain="test",
        resolved=True,
        total_tokens=0,
        turn_count=len(turns),
        h2_duplicate_count=0,
        cache_hit_rate=0.0,
        p25_token_ratio=1.0,
        output_tokens_available=True,
        task_description="t",
        turns=turns,
        subagent_turns=subagent or [],
    )


# model, fresh $, cache-read $, cache-write $, output $, total $ for the reference turn.
# fresh = 0.3M * input; read = 0.6M * input * mult; write = 0.1M * input * 1.25; out = 0.2M * out.
_REFERENCE = [
    # opus 5.5: in $4, out $20, read $0.20 (0.05x), 5m write $5
    ("claude-opus-5-5", 1.2, 0.12, 0.5, 4.0, 5.82),
    # sonnet 5.5: in $2, out $10, read $0.10 (0.05x), 5m write $2.50
    ("claude-sonnet-5-5", 0.6, 0.06, 0.25, 2.0, 2.91),
    # fable 5.1 / mythos 5.1: in $10, out $50, read $0.25 (0.025x), 5m write $12.50
    ("claude-fable-5-1", 3.0, 0.15, 1.25, 10.0, 14.4),
    ("claude-mythos-5-1", 3.0, 0.15, 1.25, 10.0, 14.4),
    # unchanged legacy rows (0.1x read): sonnet 5 $2/$10 read $0.20; fable 5 read $1
    ("claude-sonnet-5", 0.6, 0.12, 0.25, 2.0, 2.97),
    ("claude-fable-5", 3.0, 0.6, 1.25, 10.0, 14.85),
    ("claude-opus-4-5", 1.5, 0.3, 0.625, 5.0, 7.425),
]


@pytest.mark.parametrize(("model", "fresh", "read", "write", "out", "total"), _REFERENCE)
def test_reference_turn_cost_matches_hand_computation(
    model: str, fresh: float, read: float, write: float, out: float, total: float
) -> None:
    tc = compute_turn_cost(_turn(model), _PRICES)
    assert tc.priced
    assert tc.model_key == model
    assert tc.fresh_cost == pytest.approx(fresh)
    assert tc.cache_read_cost == pytest.approx(read)
    assert tc.cache_creation_cost == pytest.approx(write)
    assert tc.output_cost == pytest.approx(out)
    assert tc.total_usd == pytest.approx(total)


# The page's "Cache hits and refreshes" column, USD per MTok, for 1M cache-read tokens.
@pytest.mark.parametrize(
    ("model", "page_cache_hit_per_mtok", "tokens"),
    [
        ("claude-fable-5-1", 0.25, 1_000_000),
        ("claude-mythos-5-1", 0.25, 1_000_000),
        ("claude-opus-5-5", 0.20, 1_000_000),
        ("claude-sonnet-5-5", 0.10, 1_000_000),
        ("claude-sonnet-5", 0.20, 1_000_000),
        ("claude-fable-5", 1.00, 1_000_000),
        ("claude-opus-5", 0.50, 1_000_000),
        ("claude-sonnet-4-6", 0.30, 1_000_000),
        ("claude-haiku-4-5", 0.10, 1_000_000),
        # Haiku 5.5 cache hit $0.01/MTok at <=100k prompts: price 100k tokens -> $0.001.
        ("claude-haiku-5-5", 0.01, 100_000),
    ],
)
def test_cache_read_rate_matches_pricing_page_column(
    model: str, page_cache_hit_per_mtok: float, tokens: int
) -> None:
    tc = compute_turn_cost(
        _turn(model, prompt=tokens, cache_read=tokens, cache_creation=0, output=0), _PRICES
    )
    assert tc.cache_read_cost == pytest.approx(page_cache_hit_per_mtok * tokens / 1_000_000)


@pytest.mark.parametrize(
    "model",
    ["claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-mythos-5-1"],
)
def test_new_ids_resolve_exactly_and_with_date_suffix(model: str) -> None:
    assert _resolve_model(model, _PRICES) == (model, False, "")
    assert _resolve_model(f"{model}-20261001", _PRICES)[0] == model


def test_cache_read_multiplier_defaults_to_table_wide_value() -> None:
    assert cache_read_multiplier({"input_usd_per_mtok": 1.0}, _PRICES) == 0.1
    assert cache_read_multiplier({"cache_read_multiplier": 0.05}, _PRICES) == 0.05


# Rates published in tes/data/prices.json at b261c4b (before this change). A drift here means a
# legacy entry changed; the cost figures for old transcripts would silently move with it.
_LEGACY = {
    "claude-fable-5": (10.0, 50.0),
    "claude-mythos-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@pytest.mark.parametrize("model", sorted(_LEGACY))
def test_legacy_entries_unchanged_and_use_table_wide_cache_read(model: str) -> None:
    entry = _PRICES["models"][model]
    assert (entry["input_usd_per_mtok"], entry["output_usd_per_mtok"]) == _LEGACY[model]
    assert "cache_read_multiplier" not in entry
    assert "long_context" not in entry


def test_legacy_session_total_golden() -> None:
    # sonnet-4-6 ($3/$15): 10k prompt of which 4k read + 2k write -> 4k fresh; 2k output.
    # 4k*3 + 4k*3*0.1 + 2k*3*1.25 + 2k*15 per M = 0.012 + 0.0012 + 0.0075 + 0.03 = 0.0507
    t = _turn(
        "claude-sonnet-4-6", prompt=10_000, cache_read=4_000, cache_creation=2_000, output=2_000
    )
    sc = compute_session_cost(_session([t]), _PRICES)
    assert sc.total_usd == pytest.approx(0.0507)
    assert sc.priced and sc.pricing_caveats == []


def test_claude_sonnet_4_bare_resolves_exactly_at_same_rate() -> None:
    assert _resolve_model("claude-sonnet-4-20250514", _PRICES)[0] == "claude-sonnet-4"
    entry = _PRICES["models"]["claude-sonnet-4"]
    assert (entry["input_usd_per_mtok"], entry["output_usd_per_mtok"]) == (3.0, 15.0)


# --- Haiku 5.5 long-context tier (page: <=100,000-token prompts vs over 100,000) ---


def test_haiku_5_5_base_tier_hand_computed() -> None:
    # prompt 80k (read 50k, write 10k, fresh 20k), out 5k at $0.10 in / $0.50 out:
    # 0.02*0.10 + 0.05*0.10*0.1 + 0.01*0.10*1.25 + 0.005*0.50 = 0.00625
    tc = compute_turn_cost(
        _turn(
            "claude-haiku-5-5",
            prompt=80_000,
            cache_read=50_000,
            cache_creation=10_000,
            output=5_000,
        ),
        _PRICES,
    )
    assert tc.total_usd == pytest.approx(0.00625)
    assert tc.pricing_caveat == ""


def test_haiku_5_5_long_context_tier_hand_computed() -> None:
    # prompt 200k (read 150k, write 20k, fresh 30k), out 10k at $0.50 in / $2.50 out:
    # 0.03*0.5 + 0.15*0.5*0.1 + 0.02*0.5*1.25 + 0.01*2.5 = 0.06
    tc = compute_turn_cost(
        _turn(
            "claude-haiku-5-5",
            prompt=200_000,
            cache_read=150_000,
            cache_creation=20_000,
            output=10_000,
        ),
        _PRICES,
    )
    assert tc.total_usd == pytest.approx(0.06)


def test_haiku_5_5_threshold_is_exclusive() -> None:
    # "up to 100,000" is the base tier; only strictly more is "over".
    at = compute_turn_cost(
        _turn("claude-haiku-5-5", prompt=100_000, cache_read=0, cache_creation=0, output=0), _PRICES
    )
    over = compute_turn_cost(
        _turn("claude-haiku-5-5", prompt=100_001, cache_read=0, cache_creation=0, output=0), _PRICES
    )
    assert at.total_usd == pytest.approx(100_000 * 0.10 / 1e6)
    assert over.total_usd == pytest.approx(100_001 * 0.50 / 1e6)


def test_haiku_5_5_aggregate_within_threshold_is_exact_without_caveat() -> None:
    # 3 requests summing to 90k prompt tokens cannot include one over 100k.
    tc = compute_turn_cost(
        _turn(
            "claude-haiku-5-5",
            prompt=90_000,
            cache_read=0,
            cache_creation=0,
            output=0,
            request_count=3,
        ),
        _PRICES,
    )
    assert tc.pricing_caveat == ""
    assert tc.total_usd == pytest.approx(90_000 * 0.10 / 1e6)


def test_haiku_5_5_aggregate_over_threshold_is_a_visible_floor() -> None:
    sub = _turn(
        "claude-haiku-5-5",
        prompt=250_000,
        cache_read=0,
        cache_creation=0,
        output=0,
        request_count=3,
    )
    tc = compute_turn_cost(sub, _PRICES)
    assert tc.total_usd == pytest.approx(250_000 * 0.10 / 1e6)  # base tier: a floor
    assert "floor" in tc.pricing_caveat and "claude-haiku-5-5" in tc.pricing_caveat
    sc = compute_session_cost(_session([], [sub]), _PRICES)
    assert sc.priced  # it IS priced; the simplification is reported separately
    assert len(sc.pricing_caveats) == 1


def test_unmodelled_billing_dimensions_are_documented_in_cost_output() -> None:
    sc = compute_session_cost(_session([_turn("claude-opus-5-5")]), _PRICES)
    for word in ("fast mode", "inference_geo", "Batch"):
        assert word in sc.domain_of_validity


# --- <synthetic> ---


def test_synthetic_is_a_zero_cost_non_model_never_unpriced() -> None:
    assert "<synthetic>" in NON_MODEL_IDS
    tc = compute_turn_cost(
        _turn("<synthetic>", prompt=0, cache_read=0, cache_creation=0, output=0), _PRICES
    )
    assert tc.priced and not tc.is_approximate and tc.total_usd == 0.0
    assert tc.approximate_reason == ""


def test_synthetic_with_tokens_does_not_make_a_session_unpriced() -> None:
    real = _turn("claude-sonnet-5-5", prompt=1_000, cache_read=0, cache_creation=0, output=100)
    syn = _turn("<synthetic>", prompt=500, cache_read=0, cache_creation=0, output=50)
    sc = compute_session_cost(_session([real, syn], [syn]), _PRICES)
    assert sc.unpriced_models == []
    assert sc.priced and not sc.approximate and sc.approximate_reasons == []
    assert sc.total_usd == pytest.approx(1_000 * 2 / 1e6 + 100 * 10 / 1e6)


def test_synthetic_cannot_be_priced_by_a_user_override_table() -> None:
    table = {
        **_PRICES,
        "models": {
            **_PRICES["models"],
            "<synthetic>": {"input_usd_per_mtok": 99.0, "output_usd_per_mtok": 99.0},
        },
    }
    tc = compute_turn_cost(_turn("<synthetic>"), table)
    assert tc.total_usd == 0.0
