from __future__ import annotations

"""tests/test_attribution_rules.py — Behavioral rules for each attribution bucket.

Verifies that every bucket follows its exact observable definition:
  B1  rr_waste_tokens      = Σ rr_exclusive_ai_turns: (input + output)
  B2  rfr_waste_tokens     = Σ rfr_waste_ai_turns:    (input + output)
  B3  context_resend_tokens= Σ clean_ai_turns:         cache_read
  B4  output_tokens        = Σ clean_ai_turns:         token_count_output
  B5  fresh_input_tokens   = Σ clean_ai_turns:         (input - cache_read - cache_creation)
  B6  context_growth_tokens= Σ clean_ai_turns:         cache_creation

All tests use synthetic fixtures; no external files, no LLM calls.
"""


from tes._digest import SessionDigest, TurnDigest
from tes.attribution import compute_attribution

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


def _rr_event(turns: list[int]) -> dict:
    return {
        "detector": "REDUNDANT-READ",
        "session_id": "test-session",
        "turns": turns,
        "repeat_count": 1,
        "evidence": {},
    }


def _rfr_event(turns: list[int], repeat_count: int = 2) -> dict:
    return {
        "detector": "REPEATED-FAILED-RETRY",
        "session_id": "test-session",
        "turns": turns,
        "repeat_count": repeat_count,
        "evidence": {},
    }


def _waste_entry(events: list[dict]) -> dict:
    return {"session_id": "test-session", "waste_events": events}


# ---------------------------------------------------------------------------
# B1 — rr_waste bucket rules
# ---------------------------------------------------------------------------


def test_rr_bucket_is_proof_turns_2on() -> None:
    """RR event with turns=[t0, t1, t2, t3]; t2 and t3 are AI turns.
    rr_waste_tokens == (input+output) of t2 + (input+output) of t3.
    """
    t2 = _ai(2, token_count_input=500, token_count_output=100, cache_read=300, cache_creation=50)
    t3 = _ai(3, token_count_input=600, token_count_output=120, cache_read=400, cache_creation=80)
    turns = [_ai(0), _tool(1), t2, t3]
    digest = _session(turns)
    result = compute_attribution(digest, _waste_entry([_rr_event([0, 1, 2, 3])]))

    expected = (t2.token_count_input + t2.token_count_output) + (
        t3.token_count_input + t3.token_count_output
    )
    assert result.rr_waste_tokens == expected


def test_rfr_bucket_is_proof_turns_2on() -> None:
    """RFR event with turns=[t0, t1, t2, t3]; t2 and t3 are AI turns.
    rfr_waste_tokens == (input+output) of t2 + (input+output) of t3.
    """
    t2 = _ai(2, token_count_input=700, token_count_output=150, cache_read=500, cache_creation=100)
    t3 = _ai(3, token_count_input=800, token_count_output=160, cache_read=600, cache_creation=110)
    turns = [_ai(0), _tool(1), t2, t3]
    digest = _session(turns)
    result = compute_attribution(digest, _waste_entry([_rfr_event([0, 1, 2, 3])]))

    expected = (t2.token_count_input + t2.token_count_output) + (
        t3.token_count_input + t3.token_count_output
    )
    assert result.rfr_waste_tokens == expected


def test_rr_legitimate_turns_not_counted() -> None:
    """Proof turns[0:2] — the first legitimate call+result pair — are NOT in rr_waste.

    The legitimate AI turn (turns[0]) must appear in clean buckets.
    """
    # turns[0] = AI (legitimate), turns[1] = tool, turns[2] = AI (waste), turns[3] = tool
    t0 = _ai(0, token_count_input=1000, token_count_output=200, cache_read=0, cache_creation=0)
    t2 = _ai(2, token_count_input=400, token_count_output=80, cache_read=0, cache_creation=0)
    turns = [t0, _tool(1), t2, _tool(3)]
    digest = _session(turns)
    result = compute_attribution(digest, _waste_entry([_rr_event([0, 1, 2, 3])]))

    # rr_waste should only include t2 tokens
    expected_rr = t2.token_count_input + t2.token_count_output
    assert result.rr_waste_tokens == expected_rr

    # t0 tokens must NOT be in rr_waste; they should appear in clean buckets
    # Check: total_billed = rr_waste + clean_buckets
    clean_total = (
        result.context_resend_tokens
        + result.output_tokens
        + result.fresh_input_tokens
        + result.context_growth_tokens
    )
    expected_clean = t0.token_count_input + t0.token_count_output
    assert clean_total == expected_clean


# ---------------------------------------------------------------------------
# B3 — context_resend bucket (cache_read from clean turns)
# ---------------------------------------------------------------------------


def test_context_resend_is_cache_read_clean_only() -> None:
    """B3 = sum of cache_read for CLEAN turns only; waste turns' cache_read is in waste bucket."""
    clean_turn = _ai(
        0, token_count_input=1000, token_count_output=200, cache_read=700, cache_creation=100
    )
    waste_turn = _ai(
        2, token_count_input=900, token_count_output=180, cache_read=600, cache_creation=90
    )
    turns = [clean_turn, _tool(1), waste_turn, _tool(3)]
    digest = _session(turns)
    result = compute_attribution(digest, _waste_entry([_rr_event([1, 1, 2, 3])]))
    # turns[0:2] = [1, 1] (tool, tool – not AI), so turn 2 is waste

    # context_resend should be only clean_turn.cache_read
    assert result.context_resend_tokens == clean_turn.cache_read
    # waste turn's cache_read is absorbed into rr_waste_tokens (input+output total)
    assert result.rr_waste_tokens == waste_turn.token_count_input + waste_turn.token_count_output


def test_output_is_clean_turns_only() -> None:
    """B4 = sum of token_count_output for CLEAN turns only."""
    clean = _ai(0, token_count_input=1000, token_count_output=200, cache_read=800, cache_creation=0)
    waste = _ai(2, token_count_input=400, token_count_output=80, cache_read=300, cache_creation=0)
    turns = [clean, _tool(1), waste, _tool(3)]
    digest = _session(turns)
    result = compute_attribution(digest, _waste_entry([_rfr_event([0, 1, 2, 3])]))

    # clean is legitimate (turns[0] of RFR), waste is turns[2]
    # The legitimate turn (turns[0]) is NOT in waste
    # RFR proof_turns[2:] = [2, 3] → only turn 2 is AI waste
    assert result.rfr_waste_tokens == waste.token_count_input + waste.token_count_output
    # clean's output should be in output_tokens bucket
    assert result.output_tokens == clean.token_count_output


def test_fresh_input_is_clean_turns_only() -> None:
    """B5 = sum of (input - cache_read - cache_creation) for CLEAN turns only."""
    clean = _ai(
        0, token_count_input=1000, token_count_output=200, cache_read=600, cache_creation=200
    )
    waste = _ai(
        2, token_count_input=800, token_count_output=160, cache_read=500, cache_creation=150
    )
    turns = [clean, _tool(1), waste, _tool(3)]
    digest = _session(turns)
    result = compute_attribution(digest, _waste_entry([_rr_event([0, 1, 2, 3])]))

    # Only waste turn 2 is in rr_waste; turn 0 is legitimate (turns[0:2])
    expected_fresh = max(0, clean.token_count_input - clean.cache_read - clean.cache_creation)
    assert result.fresh_input_tokens == expected_fresh


def test_context_growth_is_cache_creation_clean() -> None:
    """B6 = sum of cache_creation for CLEAN turns only."""
    clean = _ai(
        0, token_count_input=1000, token_count_output=200, cache_read=700, cache_creation=250
    )
    waste = _ai(
        2, token_count_input=900, token_count_output=180, cache_read=600, cache_creation=200
    )
    turns = [clean, _tool(1), waste, _tool(3)]
    digest = _session(turns)
    result = compute_attribution(digest, _waste_entry([_rfr_event([0, 1, 2, 3])]))

    assert result.context_growth_tokens == clean.cache_creation


# ---------------------------------------------------------------------------
# Priority and overlap rules
# ---------------------------------------------------------------------------


def test_rfr_priority_over_rr_for_overlap() -> None:
    """When AI turn T5 appears in both RFR proof_turns[2:] and RR proof_turns[2:],
    its tokens count in rfr_waste_tokens, NOT rr_waste_tokens.
    """
    t5 = _ai(5, token_count_input=600, token_count_output=120, cache_read=400, cache_creation=60)
    # Build a session with enough turns
    turns = [
        _ai(0),
        _tool(1),  # RFR legitimate pair
        _ai(2),
        _tool(3),  # RR legitimate pair
        _ai(4),
        t5,  # t5 is AI at index 5 (overlap waste)
    ]
    digest = _session(turns)
    waste_entry = _waste_entry(
        [
            _rfr_event([0, 1, 5]),  # RFR: proof_turns[2:] = [5]
            _rr_event([2, 3, 5]),  # RR:  proof_turns[2:] = [5] — overlap!
        ]
    )
    result = compute_attribution(digest, waste_entry)

    # t5 tokens = 600 + 120 = 720
    t5_billed = t5.token_count_input + t5.token_count_output

    # RFR takes priority — t5 goes to rfr_waste
    assert result.rfr_waste_tokens == t5_billed
    # rr_waste must NOT include t5
    assert result.rr_waste_tokens == 0


# ---------------------------------------------------------------------------
# Tool-turn exclusion
# ---------------------------------------------------------------------------


def test_tool_turns_excluded() -> None:
    """Tool turns (role='tool') in proof_turns[2:] do NOT contribute to any token bucket.

    This tests that tool turns listed in waste proof turns are properly filtered
    because they have role != 'ai' and are never in the ai_turn_index_set.
    """
    ai_waste = _ai(
        2, token_count_input=500, token_count_output=100, cache_read=300, cache_creation=50
    )
    tool_waste = _tool(3)  # tool turn also listed in proof_turns[2:]
    turns = [_ai(0), _tool(1), ai_waste, tool_waste]
    digest = _session(turns)
    result = compute_attribution(digest, _waste_entry([_rr_event([0, 1, 2, 3])]))

    # Only the AI waste turn (index 2) contributes to rr_waste
    expected = ai_waste.token_count_input + ai_waste.token_count_output
    assert result.rr_waste_tokens == expected
    # tool_waste has 0 tokens so this is also trivially correct, but verify it's not double-counted
    assert result.total_billed_tokens == sum(
        t.token_count_input + t.token_count_output for t in turns if t.role == "ai"
    )


# ---------------------------------------------------------------------------
# No waste entry path
# ---------------------------------------------------------------------------


def test_no_waste_entry_all_zero_waste() -> None:
    """When waste_entry=None, both waste buckets are 0 and clean buckets capture everything."""
    turns = [
        _ai(0, token_count_input=1000, token_count_output=200, cache_read=800, cache_creation=150),
        _tool(1),
        _ai(2, token_count_input=1200, token_count_output=250, cache_read=900, cache_creation=200),
    ]
    digest = _session(turns)
    result = compute_attribution(digest, waste_entry=None)

    assert result.rr_waste_tokens == 0
    assert result.rfr_waste_tokens == 0

    # All AI billed tokens should be captured by clean buckets
    clean_total = (
        result.context_resend_tokens
        + result.output_tokens
        + result.fresh_input_tokens
        + result.context_growth_tokens
    )
    expected_total = sum(
        t.token_count_input + t.token_count_output for t in turns if t.role == "ai"
    )
    assert clean_total == expected_total


def test_rr_path_a_two_proof_turns_zero_waste() -> None:
    """RR event with only turns=[t0, t1] (2 turns → proof_turns[2:] is empty).

    rr_waste_tokens must be 0 because there are no redundant turns.
    This mirrors 'PATH-A' RR events that detect duplication but flag no wasted turns.
    """
    turns = [_ai(0), _tool(1), _ai(2)]
    digest = _session(turns)
    # Only two proof turns — no redundant waste
    waste_entry = _waste_entry([_rr_event([0, 1])])
    result = compute_attribution(digest, waste_entry)

    assert result.rr_waste_tokens == 0
    # All tokens should be in clean buckets
    clean_total = (
        result.context_resend_tokens
        + result.output_tokens
        + result.fresh_input_tokens
        + result.context_growth_tokens
    )
    assert clean_total == result.total_billed_tokens


# ---------------------------------------------------------------------------
# DOV label rules
# ---------------------------------------------------------------------------


def test_bucket_labels_in_dov() -> None:
    """domain_of_validity contains 'total billed tokens' and does NOT contain
    'productive' or 'context bloat'.
    """
    digest = _session([_ai(0)])
    result = compute_attribution(digest, waste_entry=None)

    dov = result.domain_of_validity.lower()
    assert "total billed tokens" in dov, "DOV must mention 'total billed tokens'"
    assert "productive" not in dov, "DOV must NOT use the word 'productive'"
    assert "context bloat" not in dov, "DOV must NOT use the phrase 'context bloat'"
