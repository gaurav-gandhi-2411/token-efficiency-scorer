from __future__ import annotations

"""tests/test_cost_unpriced.py — Structural guard for the S1 audit fix (0.10.2):

no code path in tes.cost may return a confidently-priced (priced=True /
total_usd > 0 from a real rate) TurnCost or SessionCost for a model absent
from the price table. Mirrors adk-tracegauge's own B1 test pattern
(``test_resolve_unknown_model_returns_none_not_a_default`` in
tests/test_pricing.py there) at the same rigor: a direct unit assertion PLUS
a property test sweeping many synthetic unknown model strings, not just one
hand-picked example.
"""

import string

import pytest
from tes._digest import SessionDigest, TurnDigest
from tes.cost import _resolve_model, compute_session_cost, compute_turn_cost, load_price_table

_BUNDLED = load_price_table()

# A representative-but-not-exhaustive sweep of garbage/unresolvable model
# strings: empty, whitespace-only, made-up vendor names, near-misses of real
# keys, and strings containing every ASCII letter/digit/punctuation class.
_UNKNOWN_MODEL_STRINGS: list[str] = [
    "",
    "   ",
    "totally-made-up-model-xyz",
    "gpt-4o",  # a real model, but not a tracegauge-priced one
    "claude-opus-6",  # near-miss of a real key -- must not fuzzy-match
    "claude-sonnet-5x",  # near-miss suffix -- must not prefix-match past the real key
    "CLAUDE-OPUS-5",  # case mismatch -- resolution is case-sensitive by design
    "claude-opus-99-preview",
    "bedrock/claude-opus-5",  # LiteLLM-style provider prefix tracegauge doesn't strip
    "".join(c for c in string.punctuation),
    "🤖-model",
    "claude-3-does-not-exist",
]


def _ai_turn(model: str, *, index: int = 0) -> TurnDigest:
    return TurnDigest(
        turn_index=index,
        role="ai",
        tool_names=[],
        content_snippet="",
        token_count_input=10_000,
        token_count_output=2_000,
        cache_read=0,
        h2_duplicate=False,
        cache_creation=0,
        model=model,
    )


def _session(turns: list[TurnDigest]) -> SessionDigest:
    return SessionDigest(
        session_id="unpriced-test",
        domain="test",
        resolved=True,
        total_tokens=sum(t.token_count_input for t in turns),
        turn_count=len(turns),
        h2_duplicate_count=0,
        cache_hit_rate=0.0,
        p25_token_ratio=1.0,
        output_tokens_available=True,
        task_description="test",
        turns=turns,
    )


# ---------------------------------------------------------------------------
# Direct assertion (adk-tracegauge B1 pattern): _resolve_model returns None,
# never a default key, for a genuinely unresolvable model string.
# ---------------------------------------------------------------------------


def test_resolve_unknown_model_returns_none_not_a_default() -> None:
    for model_str in _UNKNOWN_MODEL_STRINGS:
        key, is_approximate, reason = _resolve_model(model_str, _BUNDLED)
        assert key is None, f"{model_str!r} resolved to {key!r}, expected None"
        assert is_approximate is True
        assert reason != ""


# ---------------------------------------------------------------------------
# Property test: for EVERY unknown model string, compute_turn_cost must
# return priced=False and total_usd == 0.0 -- never a nonzero dollar figure
# computed at the default_model's (or any other) rate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_str", _UNKNOWN_MODEL_STRINGS)
def test_compute_turn_cost_never_confidently_prices_an_unknown_model(model_str: str) -> None:
    turn = _ai_turn(model_str)
    tc = compute_turn_cost(turn, _BUNDLED)

    assert tc.priced is False
    assert tc.total_usd == 0.0
    assert tc.fresh_cost == 0.0
    assert tc.cache_read_cost == 0.0
    assert tc.cache_creation_cost == 0.0
    assert tc.output_cost == 0.0
    assert tc.is_approximate is True
    # The reason must name the model AND a concrete remedy -- not a bare
    # "approximate" flag with no actionable content.
    assert (
        "TES_PRICE_TABLE" in tc.approximate_reason or "empty model string" in tc.approximate_reason
    )


# ---------------------------------------------------------------------------
# Property test at the session level: a session made ENTIRELY of unknown-
# model turns must total exactly $0.00, never a nonzero guessed figure.
# ---------------------------------------------------------------------------


def test_session_of_only_unknown_models_totals_zero_never_a_guess() -> None:
    turns = [_ai_turn(m, index=i) for i, m in enumerate(_UNKNOWN_MODEL_STRINGS)]
    sc = compute_session_cost(_session(turns), _BUNDLED)

    assert sc.total_usd == 0.0
    assert sc.approximate is True
    assert sc.approximate_turn_count == len(_UNKNOWN_MODEL_STRINGS)
    assert all(not tc.priced for tc in sc.turn_costs)


# ---------------------------------------------------------------------------
# Every resolvable (non-retired) entry in the bundled table IS confidently
# priced -- the flip side of the property above, so this file can't pass by
# accident (e.g. a bug that marks every turn unpriced).
# ---------------------------------------------------------------------------


def test_every_active_bundled_model_is_confidently_priced() -> None:
    for model_key, entry in _BUNDLED["models"].items():
        if entry.get("retired"):
            continue
        tc = compute_turn_cost(_ai_turn(model_key), _BUNDLED)
        assert tc.priced is True, f"{model_key} should resolve and be priced"
        assert tc.is_approximate is False
        assert tc.total_usd > 0.0


# ---------------------------------------------------------------------------
# 0.10.2 (S1 fix, task 1.3): exact live-verified rates for the two flagship
# models that were previously MISSING from the table entirely. Locks in the
# real figures fetched from platform.claude.com/docs/en/about-claude/pricing
# on 2026-08-15 so a future edit can't silently drift them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model_key", "expected_input", "expected_output"),
    [
        ("claude-opus-5", 5.00, 25.00),
        ("claude-sonnet-5", 2.00, 10.00),
        ("claude-fable-5", 10.00, 50.00),
        ("claude-mythos-5", 10.00, 50.00),
    ],
)
def test_new_model_rates_match_live_pricing_page(
    model_key: str, expected_input: float, expected_output: float
) -> None:
    entry = _BUNDLED["models"][model_key]
    assert entry["input_usd_per_mtok"] == pytest.approx(expected_input, rel=1e-9)
    assert entry["output_usd_per_mtok"] == pytest.approx(expected_output, rel=1e-9)


# ---------------------------------------------------------------------------
# 0.10.2 (S1 fix, task 1.7): dollar-magnitude regression for the realistic
# 10,000-input / 2,000-output single call the audit used to quantify the
# defect. Pre-fix (0.10.1), an unresolved claude-opus-5/claude-sonnet-5 call
# silently priced at the claude-sonnet-4-6 default ($3/$15/Mtok) = $0.06.
# Post-fix, each model prices at its own real rate.
# ---------------------------------------------------------------------------


def test_realistic_call_dollar_magnitude_sonnet_5() -> None:
    turn = _ai_turn("claude-sonnet-5")  # 10_000 input / 2_000 output, no cache
    tc = compute_turn_cost(turn, _BUNDLED)
    # 10_000 * 2.00 / 1e6 + 2_000 * 10.00 / 1e6 = 0.02 + 0.02 = 0.04
    assert tc.total_usd == pytest.approx(0.04, rel=1e-9)
    pre_fix_charge = 10_000 * 3.00 / 1_000_000 + 2_000 * 15.00 / 1_000_000  # old default rate
    assert pre_fix_charge == pytest.approx(0.06, rel=1e-9)
    assert pre_fix_charge - tc.total_usd == pytest.approx(
        0.02, rel=1e-9
    )  # 50% overcharge, matches S1


def test_realistic_call_dollar_magnitude_opus_5() -> None:
    turn = _ai_turn("claude-opus-5")  # 10_000 input / 2_000 output, no cache
    tc = compute_turn_cost(turn, _BUNDLED)
    # 10_000 * 5.00 / 1e6 + 2_000 * 25.00 / 1e6 = 0.05 + 0.05 = 0.10
    assert tc.total_usd == pytest.approx(0.10, rel=1e-9)
    pre_fix_charge = 10_000 * 3.00 / 1_000_000 + 2_000 * 15.00 / 1_000_000  # old default rate
    assert pre_fix_charge == pytest.approx(0.06, rel=1e-9)
    assert tc.total_usd - pre_fix_charge == pytest.approx(
        0.04, rel=1e-9
    )  # 40% undercharge, matches S1
