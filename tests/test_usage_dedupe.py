from __future__ import annotations

"""Regression: one Claude Code API response = N assistant records, usage counted ONCE.

Claude Code writes one JSONL record per content block (thinking / text / tool_use), each
repeating the response's identical ``usage`` (verified on real transcripts: 0 of 23,553
multi-record groups differed). Summing per record over-counted real_tokens ~2.4x and cost
~2.3x. The fixture is synthetic content in the real record shape.
"""

import json
from pathlib import Path
from typing import Any

from tes._digest import reconstruct_digest
from tes.adapt import ADAPTER_VERSION, adapt_session
from tes.cost import compute_session_cost, load_price_table

FIXTURE = Path(__file__).parent / "fixtures" / "usage_dedupe" / "multi_block_session.jsonl"

# msg_A: 4 records, usage (2, 5000, 30000, 600). msg_B: 1 record, usage (3, 0, 35000, 200).
EXPECTED_REAL = (2 + 5000 + 600) + (3 + 200)  # input + cache_creation + output, no cache_read
EXPECTED_BILLED = (2 + 5000 + 30000 + 600) + (3 + 35000 + 200)


def _ai(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in record["digest"]["turns"] if t["role"] == "ai"]


def test_usage_counted_once_per_message_id() -> None:
    record = adapt_session(FIXTURE)
    assert record["total_tokens"] == EXPECTED_BILLED
    real = sum(
        t["token_count_input"] - t["cache_read"] + t["token_count_output"] for t in _ai(record)
    )
    assert real == EXPECTED_REAL
    assert record["adapter_version"] == ADAPTER_VERSION == 2


def test_turn_structure_and_tool_calls_unchanged() -> None:
    """Dedupe touches usage only: one turn per record, every tool_use still visible."""
    record = adapt_session(FIXTURE)
    ai = _ai(record)
    assert len(ai) == 5  # 4 records of msg_A + 1 of msg_B, as before the fix
    assert [t["tool_names"] for t in ai[:4]] == [[], [], ["Read"], ["Bash"]]
    assert record["turn_count"] == 7  # 5 ai + user task + tool_result turn
    # Carrier = LAST record with a tool_use (waste on tool-calling turns must be charged).
    assert [t["token_count_output"] for t in ai] == [0, 0, 0, 600, 200]
    assert ai[3]["cache_read"] == 30000 and ai[2]["cache_read"] == 0
    assert all(t["model"] == "claude-sonnet-4-6" for t in ai)


def test_usage_dedupe_counters_observable() -> None:
    record = adapt_session(FIXTURE)
    assert record["usage_dedupe"] == {
        "usage_records": 5,
        "usage_records_deduped": 2,
        "duplicate_usage_records": 3,
    }
    assert record["digest"]["usage_records"] == 5
    assert record["digest"]["usage_records_deduped"] == 2


def test_cost_equals_one_record_per_response(tmp_path: Path) -> None:
    """Cost of the multi-block transcript == cost of the same session with one record/response."""
    rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    collapsed = [r for r in rows if r.get("uuid") not in ("a0", "a1", "a2")]
    p = tmp_path / "one_per_response.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in collapsed) + "\n", encoding="utf-8")
    prices = load_price_table()

    def total(path: Path) -> float:
        digest = reconstruct_digest(adapt_session(path)["digest"])
        return compute_session_cost(digest, prices).total_usd

    assert total(FIXTURE) > 0
    assert abs(total(FIXTURE) - total(p)) < 1e-12


def test_max_over_streaming_partials(tmp_path: Path) -> None:
    """Partials of one response may differ in output_tokens: take the per-field max."""
    rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    rows[1]["message"]["usage"]["output_tokens"] = 40  # early partial of msg_A
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    ai = _ai(adapt_session(p))
    assert sum(t["token_count_output"] for t in ai) == 600 + 200


def test_records_without_ids_are_never_merged(tmp_path: Path) -> None:
    rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
    for r in rows:
        if r["type"] == "assistant":
            r["message"].pop("id", None)
            r.pop("requestId", None)
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    record = adapt_session(p)  # uuid fallback is unique => legacy per-record counting
    assert record["usage_dedupe"]["duplicate_usage_records"] == 0
    assert record["total_tokens"] > EXPECTED_BILLED


def test_score_result_exposes_dedupe_counters() -> None:
    from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
    from tes.score import score_session

    result = score_session(adapt_session(FIXTURE), load_baselines(BUNDLED_BASELINES_PATH))
    assert result.adapter_version == 2
    assert (result.usage_records, result.usage_records_deduped) == (5, 2)
    assert result.duplicate_usage_records == 3
    assert result.real_tokens == EXPECTED_REAL
