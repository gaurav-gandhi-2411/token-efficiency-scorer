from __future__ import annotations

"""tests/test_attribution_reconcile.py — Invariant: all 6 token buckets sum to total_billed_tokens.

Tests use synthetic SessionDigest / TurnDigest objects built in-test.
No external fixtures, no LLM calls, no real corpus data.

Invariant under test:
    rr_waste_tokens + rfr_waste_tokens + context_resend_tokens +
    output_tokens + fresh_input_tokens + context_growth_tokens
    == total_billed_tokens
"""

import pytest

from tes._digest import SessionDigest, TurnDigest
from tes.attribution import AttributionResult, compute_attribution
from tes.cost import load_price_table


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ai(
    idx: int,
    token_count_input: int = 1000,
    token_count_output: int = 200,
    cache_read: int = 800,
    cache_creation: int = 150,
    model: str = "claude-sonnet-4-6",
) -> TurnDigest:
    """Build a synthetic AI TurnDigest with typical cache-heavy numbers."""
    return TurnDigest(
        turn_index=idx,
        role="ai",
        tool_names=[],
        content_snippet="AI turn",
        token_count_input=token_count_input,
        token_count_output=token_count_output,
        cache_read=cache_read,
        h2_duplicate=False,
        cache_creation=cache_creation,
        model=model,
    )


def _tool(idx: int) -> TurnDigest:
    """Build a synthetic tool-result TurnDigest (zero tokens)."""
    return TurnDigest(
        turn_index=idx,
        role="tool",
        tool_names=[],
        content_snippet="tool result",
        token_count_input=0,
        token_count_output=0,
        cache_read=0,
        h2_duplicate=False,
        cache_creation=0,
        model="",
    )


def _session(turns: list[TurnDigest], session_id: str = "test-session") -> SessionDigest:
    """Wrap a turn list in a minimal SessionDigest."""
    return SessionDigest(
        session_id=session_id,
        domain="test",
        resolved=True,
        total_tokens=sum(t.token_count_input + t.token_count_output for t in turns),
        turn_count=len(turns),
        h2_duplicate_count=0,
        cache_hit_rate=0.0,
        p25_token_ratio=0.0,
        output_tokens_available=True,
        task_description="synthetic test session",
        turns=turns,
    )


def _assert_reconciles(result: AttributionResult) -> None:
    """Assert the 6-bucket reconciliation invariant."""
    total_from_buckets = (
        result.rr_waste_tokens
        + result.rfr_waste_tokens
        + result.context_resend_tokens
        + result.output_tokens
        + result.fresh_input_tokens
        + result.context_growth_tokens
    )
    assert total_from_buckets == result.total_billed_tokens, (
        f"Bucket sum {total_from_buckets} != total_billed_tokens {result.total_billed_tokens}"
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_no_waste_reconciles() -> None:
    """Session with no waste events: all 6 buckets still sum to total_billed."""
    turns = [
        _ai(0),
        _tool(1),
        _ai(2, token_count_input=1500, token_count_output=300, cache_read=1200, cache_creation=200),
        _tool(3),
    ]
    digest = _session(turns)
    result = compute_attribution(digest, waste_entry=None)
    _assert_reconciles(result)
    # All billed tokens should be in clean buckets (no waste)
    assert result.rr_waste_tokens == 0
    assert result.rfr_waste_tokens == 0


def test_rr_waste_reconciles() -> None:
    """Session with one RR event (4 proof turns → turns[2:] are waste): buckets sum."""
    turns = [
        _ai(0),   # legitimate call (turns[0])
        _tool(1), # legitimate result (turns[1])
        _ai(2),   # redundant call (turns[2] — waste)
        _tool(3), # redundant result (turns[3] — waste, but tool so 0 tokens)
        _ai(4),   # clean turn (not in any waste event)
        _tool(5),
    ]
    digest = _session(turns)
    waste_entry = {
        "session_id": "test-session",
        "waste_events": [
            {
                "detector": "REDUNDANT-READ",
                "session_id": "test-session",
                "turns": [0, 1, 2, 3],  # turns[2:] = [2, 3] are waste
                "repeat_count": 1,
                "evidence": {},
            }
        ],
    }
    result = compute_attribution(digest, waste_entry)
    _assert_reconciles(result)
    # Turn 2 is AI → goes to rr_waste; turn 3 is tool → excluded
    assert result.rr_waste_tokens > 0
    assert result.rfr_waste_tokens == 0


def test_rfr_waste_reconciles() -> None:
    """Session with one RFR event: buckets sum."""
    turns = [
        _ai(0),
        _tool(1),
        _ai(2),   # rfr waste
        _tool(3),
        _ai(4),   # clean
    ]
    digest = _session(turns)
    waste_entry = {
        "session_id": "test-session",
        "waste_events": [
            {
                "detector": "REPEATED-FAILED-RETRY",
                "session_id": "test-session",
                "turns": [0, 1, 2, 3],
                "repeat_count": 2,
                "evidence": {},
            }
        ],
    }
    result = compute_attribution(digest, waste_entry)
    _assert_reconciles(result)
    assert result.rfr_waste_tokens > 0
    assert result.rr_waste_tokens == 0


def test_both_detectors_reconciles() -> None:
    """Session with both RR and RFR events active: buckets sum."""
    turns = [
        _ai(0),   # rfr proof turn 0 (legitimate)
        _tool(1), # rfr proof turn 1 (legitimate)
        _ai(2),   # rfr waste
        _tool(3), # rfr waste (tool — 0 tokens)
        _ai(4),   # rr proof turn 0 (legitimate)
        _tool(5), # rr proof turn 1 (legitimate)
        _ai(6),   # rr waste
        _tool(7), # rr waste (tool — 0 tokens)
        _ai(8),   # clean
    ]
    digest = _session(turns)
    waste_entry = {
        "session_id": "test-session",
        "waste_events": [
            {
                "detector": "REPEATED-FAILED-RETRY",
                "session_id": "test-session",
                "turns": [0, 1, 2, 3],
                "repeat_count": 2,
                "evidence": {},
            },
            {
                "detector": "REDUNDANT-READ",
                "session_id": "test-session",
                "turns": [4, 5, 6, 7],
                "repeat_count": 1,
                "evidence": {},
            },
        ],
    }
    result = compute_attribution(digest, waste_entry)
    _assert_reconciles(result)
    assert result.rfr_waste_tokens > 0
    assert result.rr_waste_tokens > 0


def test_overlap_turns_no_double_count() -> None:
    """A turn in both RFR and RR proof_turns[2:]: total is still correct; no double-count."""
    # Turn 2 will appear in both RFR turns[2:] and RR turns[2:]
    turns = [
        _ai(0),
        _tool(1),
        _ai(2),   # overlap: both RFR and RR claim it as waste
        _ai(3),   # clean
    ]
    digest = _session(turns)
    waste_entry = {
        "session_id": "test-session",
        "waste_events": [
            {
                "detector": "REPEATED-FAILED-RETRY",
                "session_id": "test-session",
                "turns": [0, 1, 2],
                "repeat_count": 2,
                "evidence": {},
            },
            {
                "detector": "REDUNDANT-READ",
                "session_id": "test-session",
                "turns": [0, 1, 2],
                "repeat_count": 1,
                "evidence": {},
            },
        ],
    }
    result = compute_attribution(digest, waste_entry)
    _assert_reconciles(result)
    # RFR takes priority → turn 2 should be in rfr only
    assert result.rfr_waste_tokens > 0
    # rr_waste should NOT include turn 2 (excluded by RFR priority)


def test_zero_tokens_session() -> None:
    """AI turn with all-zero token counts: all buckets = 0, total = 0."""
    turns = [
        TurnDigest(
            turn_index=0,
            role="ai",
            tool_names=[],
            content_snippet="",
            token_count_input=0,
            token_count_output=0,
            cache_read=0,
            h2_duplicate=False,
            cache_creation=0,
            model="claude-sonnet-4-6",
        )
    ]
    digest = _session(turns)
    result = compute_attribution(digest, waste_entry=None)
    _assert_reconciles(result)
    assert result.total_billed_tokens == 0
    assert result.rr_waste_tokens == 0
    assert result.rfr_waste_tokens == 0
    assert result.context_resend_tokens == 0
    assert result.output_tokens == 0
    assert result.fresh_input_tokens == 0
    assert result.context_growth_tokens == 0


def test_high_cache_session() -> None:
    """Session where cache_read dominates (typical CC session): still reconciles."""
    turns = [
        _ai(
            0,
            token_count_input=50_000,
            token_count_output=500,
            cache_read=49_000,
            cache_creation=800,
        ),
        _ai(
            1,
            token_count_input=60_000,
            token_count_output=600,
            cache_read=58_500,
            cache_creation=1_200,
        ),
        _ai(
            2,
            token_count_input=70_000,
            token_count_output=700,
            cache_read=68_000,
            cache_creation=1_500,
        ),
    ]
    digest = _session(turns)
    result = compute_attribution(digest, waste_entry=None)
    _assert_reconciles(result)
    # cache_read dominates so context_resend should be the biggest bucket
    assert result.context_resend_tokens > result.output_tokens


def test_reconcile_with_costs() -> None:
    """Supply a price table; verify sum of USD buckets equals total_usd on the result."""
    turns = [
        _ai(0, token_count_input=1000, token_count_output=200, cache_read=800, cache_creation=150),
        _tool(1),
        _ai(2, token_count_input=2000, token_count_output=400, cache_read=1600, cache_creation=300),
        _tool(3),
    ]
    digest = _session(turns)
    waste_entry = {
        "session_id": "test-session",
        "waste_events": [
            {
                "detector": "REDUNDANT-READ",
                "session_id": "test-session",
                "turns": [0, 1, 2, 3],
                "repeat_count": 1,
                "evidence": {},
            }
        ],
    }
    prices = load_price_table()
    result = compute_attribution(digest, waste_entry, prices)

    _assert_reconciles(result)

    # USD reconciliation
    usd_from_buckets = (
        result.rr_waste_usd
        + result.rfr_waste_usd
        + result.context_resend_usd
        + result.output_usd
        + result.fresh_input_usd
        + result.context_growth_usd
    )
    assert abs(usd_from_buckets - result.total_usd) < 1e-9, (
        f"USD bucket sum {usd_from_buckets} != total_usd {result.total_usd}"
    )
