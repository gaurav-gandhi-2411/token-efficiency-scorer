from __future__ import annotations

"""Subagent usage is rolled up into the parent session (D6) without double counting.

Fixtures reproduce the structure verified on real Claude Code data: the parent transcript
only records the launch (``toolUseResult.agentId``), the usage lives in
``<sid>/subagents/agent-<id>.jsonl`` where every record is ``isSidechain: true`` and one API
response is split over several records sharing ``message.id``.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from tes._digest import reconstruct_digest
from tes.adapt import adapt_session, collect_subagent_usage
from tes.attribution import compute_attribution
from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.cost import compute_session_cost, load_price_table
from tes.score import score_session
from tes.store import get_session, open_db, upsert_session

PRICED = "claude-sonnet-4-6"  # $3 in / $15 out per MTok in the bundled table
UNPRICED = "claude-test-unpriced-9"


def _assistant(
    msg_id: str, model: str, usage: dict[str, int], *, sidechain: bool, block: int = 0
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "uuid": f"{msg_id}-{block}",
        "requestId": f"req-{msg_id}",
        "message": {
            "id": msg_id,
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": "x"}],
            "usage": usage,
        },
    }


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _usage(inp: int, cc: int, cr: int, out: int) -> dict[str, int]:
    return {
        "input_tokens": inp,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
        "output_tokens": out,
    }


def _make_session(root: Path, *, sub_model: str = PRICED) -> Path:
    """Parent with one main response; two subagent files (one of them multi-block)."""
    parent = root / "proj" / "sid-1.jsonl"
    _write(
        parent,
        [
            {"type": "user", "message": {"role": "user", "content": "do the thing"}},
            _assistant("m1", PRICED, _usage(10, 0, 1_000, 100), sidechain=False),
            # the launch record: carries the agent id but no usage (verified on real data)
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
                "toolUseResult": {"status": "async_launched", "agentId": "aaa"},
            },
        ],
    )
    sub_dir = root / "proj" / "sid-1" / "subagents"
    # agent aaa: one response written as 3 content-block records (identical usage) ...
    _write(
        sub_dir / "agent-aaa.jsonl",
        [
            _assistant("s1", sub_model, _usage(5, 100, 2_000, 50), sidechain=True, block=b)
            for b in range(3)
        ]
        # ... plus a streamed response whose later record has the final output_tokens
        + [
            _assistant("s2", sub_model, _usage(1, 0, 3_000, 10), sidechain=True, block=0),
            _assistant("s2", sub_model, _usage(1, 0, 3_000, 70), sidechain=True, block=1),
        ],
    )
    _write(
        sub_dir / "agent-bbb.jsonl",
        [_assistant("t1", PRICED, _usage(2, 0, 500, 20), sidechain=True)],
    )
    return parent


def test_each_api_response_counted_once(tmp_path: Path) -> None:
    usage = collect_subagent_usage(_make_session(tmp_path))
    assert usage is not None
    assert usage["file_count"] == 2
    assert usage["message_count"] == 3  # s1, s2 (agent aaa) + t1 (agent bbb)
    by_model: dict[str, dict[str, int]] = {}
    for t in usage["turns"]:
        acc = by_model.setdefault(t["model"], {"in": 0, "out": 0, "cr": 0, "cc": 0})
        acc["in"] += t["token_count_input"]
        acc["out"] += t["token_count_output"]
        acc["cr"] += t["cache_read"]
        acc["cc"] += t["cache_creation"]
    # s1 (5+100+2000 in, 50 out) + s2 (1+3000 in, max out 70) if sonnet, plus t1
    assert by_model[PRICED]["out"] == 50 + 70 + 20
    assert by_model[PRICED]["in"] == (5 + 100 + 2_000) + (1 + 3_000) + (2 + 500)
    assert by_model[PRICED]["cr"] == 2_000 + 3_000 + 500
    assert by_model[PRICED]["cc"] == 100


def test_parent_is_not_double_counted_and_verdict_axis_unchanged(tmp_path: Path) -> None:
    parent = _make_session(tmp_path)
    rec = adapt_session(parent)
    # main-chain figures are exactly what the parent file alone contains
    assert rec["total_tokens"] == 10 + 1_000 + 100
    ai_turns = [t for t in rec["digest"]["turns"] if t["role"] == "ai"]
    assert len(ai_turns) == 1
    assert rec["subagent_usage"]["file_count"] == 2
    assert len(rec["digest"]["subagent_turns"]) == 2  # one per (file, model)
    # no sub-turn leaked into the main turn list (waste detectors / judge text)
    assert rec["turn_count"] == len(rec["digest"]["turns"])


def test_session_without_subagents_is_unchanged(tmp_path: Path) -> None:
    parent = tmp_path / "p" / "solo.jsonl"
    _write(parent, [_assistant("m1", PRICED, _usage(1, 0, 10, 5), sidechain=False)])
    rec = adapt_session(parent)
    assert rec["subagent_usage"]["file_count"] == 0
    assert rec["digest"]["subagent_turns"] == []
    cost = compute_session_cost(reconstruct_digest(rec["digest"]), load_price_table())
    assert cost.subagent_usd == 0.0
    assert cost.priced


def test_explicit_subagent_file_is_not_rolled_up_into_itself(tmp_path: Path) -> None:
    parent = _make_session(tmp_path)
    agent = parent.with_suffix("") / "subagents" / "agent-aaa.jsonl"
    assert collect_subagent_usage(agent) is None


def test_cost_includes_subagents_and_exposes_the_split(tmp_path: Path) -> None:
    prices = load_price_table()
    rec = adapt_session(_make_session(tmp_path))
    digest = reconstruct_digest(rec["digest"])
    cost = compute_session_cost(digest, prices)
    # hand-computed: sonnet-4-6 is $3/MTok in, $15 out, cache read 0.1x, cache write 1.25x
    sub_expected = (
        (5 + 1 + 2) * 3 / 1e6  # fresh input
        + (2_000 + 3_000 + 500) * 0.3 / 1e6  # cache reads
        + 100 * 3.75 / 1e6  # cache creation
        + (50 + 70 + 20) * 15 / 1e6  # output
    )
    main_expected = 10 * 3 / 1e6 + 1_000 * 0.3 / 1e6 + 100 * 15 / 1e6
    assert cost.subagent_usd == pytest.approx(sub_expected)
    assert cost.total_usd == pytest.approx(main_expected + sub_expected)
    assert cost.ai_turn_count == 1  # main-chain turn count unchanged
    assert cost.priced


def test_unpriced_subagent_model_marks_session_unpriced(tmp_path: Path) -> None:
    rec = adapt_session(_make_session(tmp_path, sub_model=UNPRICED))
    cost = compute_session_cost(reconstruct_digest(rec["digest"]), load_price_table())
    assert cost.unpriced_models == [UNPRICED]
    assert not cost.priced
    assert cost.approximate  # a whole agent's spend is missing from the total
    # the priced agent (bbb) still contributes
    assert cost.subagent_usd > 0


def test_attribution_includes_subagents_and_still_reconciles(tmp_path: Path) -> None:
    prices = load_price_table()
    rec = adapt_session(_make_session(tmp_path))
    digest = reconstruct_digest(rec["digest"])
    attr = compute_attribution(digest, None, prices)
    buckets = (
        attr.rr_waste_tokens
        + attr.rfr_waste_tokens
        + attr.context_resend_tokens
        + attr.output_tokens
        + attr.fresh_input_tokens
        + attr.context_growth_tokens
    )
    assert buckets == attr.total_billed_tokens
    assert attr.subagent_billed_tokens == rec["subagent_usage"]["billed_tokens"]
    assert attr.total_billed_tokens == (10 + 1_000 + 100) + attr.subagent_billed_tokens
    cost = compute_session_cost(digest, prices)
    assert attr.total_usd == pytest.approx(cost.total_usd)
    assert attr.subagent_usd == pytest.approx(cost.subagent_usd)
    # the verdict axis never sees subagent tokens
    assert attr.real_tokens == rec["total_tokens"] - 1_000


def test_score_session_and_store_carry_subagent_fields(tmp_path: Path) -> None:
    prices = load_price_table()
    parent = _make_session(tmp_path)
    rec = adapt_session(parent)
    digest = reconstruct_digest(rec["digest"])
    cost = compute_session_cost(digest, prices)
    result = score_session(
        rec, load_baselines(BUNDLED_BASELINES_PATH), session_cost=cost, attribution=None
    )
    assert result.subagent_count == 2
    assert result.subagent_tokens == rec["subagent_usage"]["real_tokens"] > 0
    assert result.real_tokens_incl_subagents == result.real_tokens + result.subagent_tokens
    assert result.subagent_cost_usd == pytest.approx(cost.subagent_usd)
    assert result.session_cost_usd == pytest.approx(cost.total_usd)

    conn = open_db(tmp_path / "tes.db")
    upsert_session(conn, result, str(parent), 1.0, "h")
    row = get_session(conn, result.session_id)
    assert row is not None
    assert row["subagent_count"] == 2
    assert row["subagent_tokens"] == result.subagent_tokens
    assert row["subagent_cost_usd"] == pytest.approx(cost.subagent_usd)


def test_store_migration_adds_subagent_columns_idempotently(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    conn = open_db(db)
    for col in ("subagent_tokens", "subagent_cost_usd", "subagent_count"):
        conn.execute(f"ALTER TABLE sessions DROP COLUMN {col}")  # simulate a pre-D6 database
    conn.commit()
    conn.close()
    for _ in range(2):  # re-opening must be a no-op the second time
        c = open_db(db)
        cols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
        assert {"subagent_tokens", "subagent_cost_usd", "subagent_count"} <= cols
        c.close()


def test_legacy_digest_dict_without_subagent_fields_still_loads() -> None:
    legacy = {
        "session_id": "old",
        "domain": "unknown",
        "resolved": False,
        "total_tokens": 0,
        "turn_count": 0,
        "h2_duplicate_count": 0,
        "cache_hit_rate": 0.0,
        "p25_token_ratio": 1.0,
        "output_tokens_available": True,
        "task_description": "N/A",
        "turns": [],
    }
    digest = reconstruct_digest(legacy)
    assert digest.subagent_turns == []
    assert digest.subagent_count == 0
