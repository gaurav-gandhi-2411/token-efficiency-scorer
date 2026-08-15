from __future__ import annotations

"""tests/test_server_tool_warning.py — Tests for the S1 audit's server-side
tool billing fix (0.10.2).

Before this fix, tes.adapt._parse_usage read only 4 token-count fields from
a Claude API response's `usage` dict and silently dropped `server_tool_use`
(e.g. web search, billed at $10/1,000 searches on top of token costs) --
zero warning of any kind, worse than the unknown-model case which at least
partially flagged. This tests the full path: adapt.py detects it ->
TurnDigest carries it -> tes.cost warns about it (never prices it) ->
tes.score/report surface the warning.
"""

import json
from pathlib import Path

from tes._digest import SessionDigest, TurnDigest
from tes.adapt import _parse_server_tool_use, adapt_session
from tes.cost import compute_session_cost, compute_turn_cost

_PRICES: dict = {
    "as_of": "2026-08-15",
    "cache_multipliers": {"read": 0.1, "write_5min": 1.25, "write_1hr": 2.0},
    "models": {
        "claude-sonnet-4-6": {"input_usd_per_mtok": 3.0, "output_usd_per_mtok": 15.0},
    },
    "model_patterns": [],
    "default_model": "claude-sonnet-4-6",
    "approximate_threshold_pct": 25,
}


# ---------------------------------------------------------------------------
# Unit tests — tes.adapt._parse_server_tool_use
# ---------------------------------------------------------------------------


def test_parse_server_tool_use_detects_web_search() -> None:
    usage = {
        "input_tokens": 105,
        "output_tokens": 6039,
        "server_tool_use": {"web_search_requests": 1},
    }
    assert _parse_server_tool_use(usage) == {"web_search_requests": 1}


def test_parse_server_tool_use_absent_returns_none() -> None:
    usage = {"input_tokens": 105, "output_tokens": 6039}
    assert _parse_server_tool_use(usage) is None


def test_parse_server_tool_use_all_zero_counts_returns_none() -> None:
    usage = {"server_tool_use": {"web_search_requests": 0, "code_execution_requests": 0}}
    assert _parse_server_tool_use(usage) is None


def test_parse_server_tool_use_multiple_kinds() -> None:
    usage = {"server_tool_use": {"web_search_requests": 3, "code_execution_requests": 1}}
    result = _parse_server_tool_use(usage)
    assert result == {"web_search_requests": 3, "code_execution_requests": 1}


# ---------------------------------------------------------------------------
# End-to-end: adapt_session on a synthetic JSONL transcript with a
# web-search-bearing assistant turn.
# ---------------------------------------------------------------------------


def _write_session_jsonl(path: Path) -> None:
    lines = [
        {
            "type": "user",
            "isSidechain": False,
            "message": {"role": "user", "content": "Search for the current AAPL price."},
        },
        {
            "type": "assistant",
            "isSidechain": False,
            "message": {
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "I'll search for that."}],
                "usage": {
                    "input_tokens": 105,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 6039,
                    "server_tool_use": {"web_search_requests": 1},
                },
            },
        },
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def test_adapt_session_captures_server_tool_use(tmp_path: Path) -> None:
    session_path = tmp_path / "server-tool-session.jsonl"
    _write_session_jsonl(session_path)

    record = adapt_session(session_path)
    turns = record["digest"]["turns"]
    ai_turns = [t for t in turns if t["role"] == "ai"]
    assert len(ai_turns) == 1
    assert ai_turns[0]["server_tool_use"] == {"web_search_requests": 1}


def test_adapt_session_without_server_tool_use_leaves_field_none(tmp_path: Path) -> None:
    session_path = tmp_path / "no-server-tool-session.jsonl"
    lines = [
        {
            "type": "assistant",
            "isSidechain": False,
            "message": {
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "Plain answer, no tools."}],
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 50,
                },
            },
        },
    ]
    session_path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    record = adapt_session(session_path)
    ai_turns = [t for t in record["digest"]["turns"] if t["role"] == "ai"]
    assert len(ai_turns) == 1
    assert ai_turns[0]["server_tool_use"] is None


# ---------------------------------------------------------------------------
# tes.cost: server tool use is warned about, never priced.
# ---------------------------------------------------------------------------


def _turn_with_server_tool_use(counts: dict[str, int] | None) -> TurnDigest:
    return TurnDigest(
        turn_index=0,
        role="ai",
        tool_names=[],
        content_snippet="",
        token_count_input=1000,
        token_count_output=100,
        cache_read=0,
        h2_duplicate=False,
        cache_creation=0,
        model="claude-sonnet-4-6",
        server_tool_use=counts,
    )


def test_compute_turn_cost_warns_but_does_not_price_server_tool_use() -> None:
    turn = _turn_with_server_tool_use({"web_search_requests": 20})
    tc = compute_turn_cost(turn, _PRICES)

    # The known model IS still priced normally -- server-tool billing is a
    # SEPARATE, additive gap, not something that should also break token
    # cost pricing for an otherwise-known model.
    assert tc.priced is True
    assert tc.is_approximate is False
    assert tc.total_usd > 0.0

    # But the warning must be present and name the detected usage.
    assert tc.server_tool_warning != ""
    assert "web_search_requests" in tc.server_tool_warning
    assert "20" in tc.server_tool_warning


def test_compute_turn_cost_no_warning_when_no_server_tool_use() -> None:
    turn = _turn_with_server_tool_use(None)
    tc = compute_turn_cost(turn, _PRICES)
    assert tc.server_tool_warning == ""


def test_compute_session_cost_collects_server_tool_warnings() -> None:
    digest = SessionDigest(
        session_id="server-tool-test",
        domain="test",
        resolved=True,
        total_tokens=2000,
        turn_count=2,
        h2_duplicate_count=0,
        cache_hit_rate=0.0,
        p25_token_ratio=1.0,
        output_tokens_available=True,
        task_description="test",
        turns=[
            _turn_with_server_tool_use({"web_search_requests": 5}),
            TurnDigest(
                turn_index=1,
                role="ai",
                tool_names=[],
                content_snippet="",
                token_count_input=1000,
                token_count_output=100,
                cache_read=0,
                h2_duplicate=False,
                cache_creation=0,
                model="claude-sonnet-4-6",
                server_tool_use=None,
            ),
        ],
    )
    sc = compute_session_cost(digest, _PRICES)
    assert len(sc.server_tool_warnings) == 1
    assert "5" in sc.server_tool_warnings[0]


def test_session_with_no_server_tool_use_has_empty_warnings_list() -> None:
    turn = _turn_with_server_tool_use(None)
    digest = SessionDigest(
        session_id="clean-test",
        domain="test",
        resolved=True,
        total_tokens=1000,
        turn_count=1,
        h2_duplicate_count=0,
        cache_hit_rate=0.0,
        p25_token_ratio=1.0,
        output_tokens_available=True,
        task_description="test",
        turns=[turn],
    )
    sc = compute_session_cost(digest, _PRICES)
    assert sc.server_tool_warnings == []


# ---------------------------------------------------------------------------
# tes.report: the warning surfaces in human-readable output as a distinct,
# clearly-marked line (never silently absent from a confident-looking total).
# ---------------------------------------------------------------------------


def test_report_surfaces_server_tool_warning_in_human_output() -> None:
    from tes.report import format_human
    from tes.score import (
        TOKEN_DOMAIN_OF_VALIDITY,
        TRAJECTORY_DOMAIN_OF_VALIDITY,
        WASTE_DOMAIN_OF_VALIDITY,
        ThreeAxisResult,
    )

    result = ThreeAxisResult(
        session_id="test-session-server-tool",
        task_type="research",
        real_tokens=10_000,
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
        session_cost_usd=0.04,
        cost_approximate=False,
        cost_domain_of_validity="test dov",
        cost_server_tool_warnings=[
            "turn 0: server-side tool usage detected (20 web_search_requests) but NOT priced"
        ],
    )
    output = format_human(result)
    assert "NOT PRICED" in output
    assert "web_search_requests" in output


def test_report_no_not_priced_line_when_no_server_tool_warnings() -> None:
    from tes.report import format_human
    from tes.score import (
        TOKEN_DOMAIN_OF_VALIDITY,
        TRAJECTORY_DOMAIN_OF_VALIDITY,
        WASTE_DOMAIN_OF_VALIDITY,
        ThreeAxisResult,
    )

    result = ThreeAxisResult(
        session_id="test-session-clean",
        task_type="research",
        real_tokens=10_000,
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
        session_cost_usd=0.04,
        cost_approximate=False,
        cost_domain_of_validity="test dov",
    )
    output = format_human(result)
    assert "NOT PRICED" not in output
