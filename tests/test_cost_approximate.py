from __future__ import annotations

"""tests/test_cost_approximate.py — Tests for approximate-flagging logic in tes.cost."""

from tes._digest import SessionDigest, TurnDigest
from tes.cost import compute_session_cost, compute_turn_cost

PRICES: dict = {
    "as_of": "2026-06-09",
    "cache_multipliers": {"read": 0.1, "write_5min": 1.25, "write_1hr": 2.0},
    "models": {
        "claude-sonnet-4-6": {"input_usd_per_mtok": 3.0, "output_usd_per_mtok": 15.0},
    },
    "model_patterns": [
        {"prefix": "claude-sonnet-4-6", "model_key": "claude-sonnet-4-6"},
    ],
    "default_model": "claude-sonnet-4-6",
    "approximate_threshold_pct": 25,
}


def _ai_turn(index: int, model: str) -> TurnDigest:
    return TurnDigest(
        turn_index=index,
        role="ai",
        tool_names=[],
        content_snippet="",
        token_count_input=1000,
        token_count_output=100,
        cache_read=0,
        h2_duplicate=False,
        cache_creation=0,
        model=model,
    )


def _session(turns: list[TurnDigest]) -> SessionDigest:
    return SessionDigest(
        session_id="approx-test",
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
# Test 1 — Known model → NOT approximate
# ---------------------------------------------------------------------------


def test_known_model_not_approximate() -> None:
    turn = _ai_turn(0, "claude-sonnet-4-6")
    tc = compute_turn_cost(turn, PRICES)
    assert tc.is_approximate is False
    assert tc.approximate_reason == ""


# ---------------------------------------------------------------------------
# Test 2 — Unknown model → approximate=True with reason
# ---------------------------------------------------------------------------


def test_unknown_model_is_approximate() -> None:
    turn = _ai_turn(0, "claude-unknown-99")
    tc = compute_turn_cost(turn, PRICES)
    assert tc.is_approximate is True
    assert "unknown model" in tc.approximate_reason
    # 0.10.2 (S1 fix): an unresolved model must NEVER be priced at the
    # default_model's rate -- total_usd is 0.0, not a guessed dollar figure.
    assert tc.priced is False
    assert tc.total_usd == 0.0
    assert tc.model_key == "claude-unknown-99"


# ---------------------------------------------------------------------------
# Test 3 — Empty model string → approximate=True
# ---------------------------------------------------------------------------


def test_empty_model_is_approximate() -> None:
    turn = _ai_turn(0, "")
    tc = compute_turn_cost(turn, PRICES)
    assert tc.is_approximate is True
    assert tc.priced is False
    assert tc.total_usd == 0.0


# ---------------------------------------------------------------------------
# Test 4 — Session with all-known models → session approximate=False
# ---------------------------------------------------------------------------


def test_session_all_known_not_approximate() -> None:
    turns = [_ai_turn(i, "claude-sonnet-4-6") for i in range(2)]
    sc = compute_session_cost(_session(turns), PRICES)
    assert sc.approximate is False


# ---------------------------------------------------------------------------
# Test 5 — Session with >25% unknown turns → session approximate=True
# ---------------------------------------------------------------------------


def test_session_over_threshold_is_approximate() -> None:
    # 4 AI turns: 2 known, 2 unknown → 50% > 25% → approximate
    turns = [
        _ai_turn(0, "claude-sonnet-4-6"),
        _ai_turn(1, "claude-sonnet-4-6"),
        _ai_turn(2, ""),
        _ai_turn(3, ""),
    ]
    sc = compute_session_cost(_session(turns), PRICES)
    assert sc.approximate is True


# ---------------------------------------------------------------------------
# Test 6 — Session with exactly 25% unknown turns → session approximate=False
# ---------------------------------------------------------------------------


def test_session_at_threshold_not_approximate() -> None:
    # 4 AI turns: 3 known, 1 unknown → 1/4 = 25.0% — NOT strictly > 25% → False
    turns = [
        _ai_turn(0, "claude-sonnet-4-6"),
        _ai_turn(1, "claude-sonnet-4-6"),
        _ai_turn(2, "claude-sonnet-4-6"),
        _ai_turn(3, ""),
    ]
    sc = compute_session_cost(_session(turns), PRICES)
    assert sc.approximate is False


# ---------------------------------------------------------------------------
# Test 7 — 8 turns: 6 known, 2 unknown → 25.0% → False
# ---------------------------------------------------------------------------


def test_session_at_threshold_8_turns_not_approximate() -> None:
    turns = [_ai_turn(i, "claude-sonnet-4-6") for i in range(6)] + [
        _ai_turn(6, ""),
        _ai_turn(7, ""),
    ]
    sc = compute_session_cost(_session(turns), PRICES)
    assert sc.approximate is False


# ---------------------------------------------------------------------------
# Test 8 — 0.10.2 (S1 fix): session total_usd excludes unresolved-model
# turns entirely -- it must equal exactly the sum of the KNOWN turns' costs,
# never a total that silently includes a guessed/default-rate dollar amount
# for the unresolved ones.
# ---------------------------------------------------------------------------


def test_session_total_excludes_unpriced_turns_not_guesses_them() -> None:
    known_turn = _ai_turn(0, "claude-sonnet-4-6")
    unknown_turn = _ai_turn(1, "totally-unrecognized-model-xyz")

    known_only_cost = compute_session_cost(_session([known_turn]), PRICES).total_usd
    mixed_cost = compute_session_cost(_session([known_turn, unknown_turn]), PRICES).total_usd

    # Adding an unresolved-model turn must not change the total at all --
    # if it silently priced at a default rate, mixed_cost would be strictly
    # greater than known_only_cost.
    assert mixed_cost == known_only_cost
    assert mixed_cost > 0.0
