from __future__ import annotations

"""tests/test_waste_costs.py — Unit tests for annotate_waste_costs.

Tests the per-event cost annotation with the "redundant turns only" definition:
  - cost = sum of per_turn_cost for proof_turns[2:]
  - proof_turns[0:2] is the first (legitimate) call+result pair, excluded from cost
  - Only AI turns have non-zero per_turn_cost; tool/user turns contribute 0 naturally
"""


from tes.waste import annotate_waste_costs


def _rfr_event(turns: list[int], repeat_count: int = 2) -> dict:
    return {
        "detector": "REPEATED-FAILED-RETRY",
        "session_id": "test-session",
        "turns": turns,
        "repeat_count": repeat_count,
        "evidence": {"error_snippet": "Exit code 1"},
    }


def _rr_b_event(turns: list[int]) -> dict:
    return {
        "detector": "REDUNDANT-READ",
        "session_id": "test-session",
        "turns": turns,
        "repeat_count": 1,
        "evidence": {"path": "B", "gap": 2, "content_snippet": "1\tdef foo():"},
    }


def _rr_a_event(turns: list[int]) -> dict:
    return {
        "detector": "REDUNDANT-READ",
        "session_id": "test-session",
        "turns": turns,
        "repeat_count": 1,
        "evidence": {"path": "A", "gap": 0, "content_snippet": "File unchanged since last read"},
    }


# ---------------------------------------------------------------------------
# annotate_waste_costs: basic contract
# ---------------------------------------------------------------------------


def test_rfr_two_repeats_cost():
    """RFR with 2 repeats: proof_turns = [c1,r1,c2,r2]. Wasted = [c2,r2]."""
    per_turn_cost = {10: 0.05, 11: 0.0, 12: 0.04, 13: 0.0}
    events = [_rfr_event(turns=[10, 11, 12, 13], repeat_count=2)]
    annotate_waste_costs(events, per_turn_cost)
    # redundant = [12, 13] → 0.04 + 0.0 = 0.04
    assert abs(events[0]["wasted_cost_usd"] - 0.04) < 1e-9


def test_rfr_three_repeats_cost():
    """RFR with 3 repeats: proof_turns = [c1,r1,c2,r2,c3,r3]. Wasted = [c2,r2,c3,r3]."""
    per_turn_cost = {0: 0.10, 1: 0.0, 2: 0.08, 3: 0.0, 4: 0.09, 5: 0.0}
    events = [_rfr_event(turns=[0, 1, 2, 3, 4, 5], repeat_count=3)]
    annotate_waste_costs(events, per_turn_cost)
    # redundant = [2, 3, 4, 5] → 0.08 + 0.0 + 0.09 + 0.0 = 0.17
    assert abs(events[0]["wasted_cost_usd"] - 0.17) < 1e-9


def test_rr_b_cost():
    """RR-B: proof_turns = [c1,r1,c2,r2]. Wasted = [c2,r2]."""
    per_turn_cost = {20: 0.03, 21: 0.0, 24: 0.06, 25: 0.0}
    events = [_rr_b_event(turns=[20, 21, 24, 25])]
    annotate_waste_costs(events, per_turn_cost)
    # redundant = [24, 25] → 0.06 + 0.0 = 0.06
    assert abs(events[0]["wasted_cost_usd"] - 0.06) < 1e-9


def test_rr_a_cost_is_zero():
    """RR-A has only 2 proof turns → redundant = [] → cost = 0.0."""
    per_turn_cost = {100: 0.05, 101: 0.0}
    events = [_rr_a_event(turns=[100, 101])]
    annotate_waste_costs(events, per_turn_cost)
    assert events[0]["wasted_cost_usd"] == 0.0


def test_empty_events_list():
    """annotate_waste_costs on empty list returns empty list without error."""
    result = annotate_waste_costs([], {})
    assert result == []


def test_mutates_and_returns_same_list():
    """annotate_waste_costs mutates in-place and returns the same list object."""
    events = [_rfr_event(turns=[1, 2, 3, 4])]
    original_id = id(events)
    returned = annotate_waste_costs(events, {3: 0.05, 4: 0.0})
    assert id(returned) == original_id
    assert "wasted_cost_usd" in events[0]


def test_missing_turns_from_per_turn_cost():
    """Turn indices absent from per_turn_cost contribute 0 (default)."""
    per_turn_cost = {2: 0.07}  # turn 3 absent (tool turn, no cost)
    events = [_rfr_event(turns=[0, 1, 2, 3])]
    annotate_waste_costs(events, per_turn_cost)
    # redundant = [2, 3] → 0.07 + 0.0 = 0.07
    assert abs(events[0]["wasted_cost_usd"] - 0.07) < 1e-9


def test_multiple_events_annotated_independently():
    """Multiple events in one list each get their own cost."""
    per_turn_cost = {10: 0.05, 11: 0.0, 12: 0.04, 13: 0.0, 20: 0.03, 21: 0.0, 24: 0.06, 25: 0.0}
    events = [
        _rfr_event(turns=[10, 11, 12, 13]),
        _rr_b_event(turns=[20, 21, 24, 25]),
    ]
    annotate_waste_costs(events, per_turn_cost)
    assert abs(events[0]["wasted_cost_usd"] - 0.04) < 1e-9
    assert abs(events[1]["wasted_cost_usd"] - 0.06) < 1e-9


def test_zero_cost_per_turn():
    """All-zero costs produce 0.0 wasted_cost_usd."""
    per_turn_cost = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0}
    events = [_rfr_event(turns=[0, 1, 2, 3])]
    annotate_waste_costs(events, per_turn_cost)
    assert events[0]["wasted_cost_usd"] == 0.0


def test_event_with_empty_turns():
    """Event with empty turns list → redundant = [] → cost = 0.0."""
    events = [{"detector": "REPEATED-FAILED-RETRY", "turns": [], "evidence": {}}]
    annotate_waste_costs(events, {5: 0.10})
    assert events[0]["wasted_cost_usd"] == 0.0
