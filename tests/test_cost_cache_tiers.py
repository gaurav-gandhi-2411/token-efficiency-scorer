from __future__ import annotations

"""1-hour vs 5-minute cache-write pricing, from the usage.cache_creation breakdown.

Before this fix every cache write was priced at the 5-minute rate (1.25x input) although
Claude Code records ``usage.cache_creation.ephemeral_1h_input_tokens`` and the official page
prices 1-hour writes at 2x input. Every expected dollar figure below is computed by hand from
the published per-MTok rates (see the arithmetic in each test), not by calling the code.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import tes
from tes._digest import SessionDigest, TurnDigest
from tes.adapt import adapt_session, collect_subagent_usage
from tes.cost import compute_session_cost, compute_turn_cost

PRICES: dict[str, Any] = json.loads(
    (Path(tes.__file__).parent / "data" / "prices.json").read_text(encoding="utf-8")
)


def _turn(
    model: str,
    fresh: int,
    created: int,
    one_hour: int,
    read: int,
    out: int,
    known: int | None = None,
) -> TurnDigest:
    return TurnDigest(
        turn_index=0,
        role="ai",
        tool_names=[],
        content_snippet="",
        token_count_input=fresh + created + read,
        token_count_output=out,
        cache_read=read,
        h2_duplicate=False,
        cache_creation=created,
        model=model,
        cache_creation_1h=one_hour,
        cache_creation_tier_known=created if known is None else known,
    )


# 1,000,000 fresh / 1,000,000 written (400,000 of them 1h) / 2,000,000 read / 100,000 out.
_BIG = {"fresh": 1_000_000, "created": 1_000_000, "one_hour": 400_000, "read": 2_000_000}


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # opus-5-5: in 4, out 20, read 0.05x=0.20, 5m write 5, 1h write 8 ($/MTok)
        #   fresh 4.00 + read 0.40 + write (0.6M*5 + 0.4M*8 = 3.00+3.20) + out 0.1M*20=2.00
        ("claude-opus-5-5", 12.60),
        # sonnet-5-5: in 2, out 10, read 0.10, 5m 2.5, 1h 4
        #   2.00 + 0.20 + (1.50 + 1.60) + 1.00
        ("claude-sonnet-5-5", 6.30),
        # sonnet-5: in 2, out 10, read 0.1x=0.20, 5m 2.5, 1h 4
        #   2.00 + 0.40 + (1.50 + 1.60) + 1.00
        ("claude-sonnet-5", 6.50),
    ],
)
def test_one_hour_writes_priced_at_2x_per_model(model: str, expected: float) -> None:
    tc = compute_turn_cost(_turn(model, out=100_000, **_BIG), PRICES)
    assert tc.priced
    assert tc.total_usd == pytest.approx(expected, abs=1e-9)
    assert tc.cache_write_1h_tokens == 400_000
    assert tc.cache_write_unspecified_tokens == 0


def test_haiku_base_tier_one_hour_split() -> None:
    # prompt 50k+30k+10k = 90k <= 100k -> base tier: in 0.10, out 0.50, read 0.01, 5m .125, 1h .20
    # fresh 0.005 + read 0.0001 + write (20k*.125 + 10k*.20 = 0.0025 + 0.002) + out 0.01
    t = _turn("claude-haiku-5-5", 50_000, 30_000, 10_000, 10_000, 20_000)
    tc = compute_turn_cost(t, PRICES)
    assert tc.total_usd == pytest.approx(0.0196, abs=1e-9)


def test_haiku_long_context_tier_one_hour_split() -> None:
    # prompt 100k+60k+40k = 200k > 100k -> long tier: in 0.50, out 2.50, read 0.05,
    # 5m 0.625, 1h 1.00. fresh 0.05 + read 0.002 + write (40k*.625 + 20k*1.0 = .025 + .02)
    # + out 10k*2.5 = 0.025
    t = _turn("claude-haiku-5-5", 100_000, 60_000, 20_000, 40_000, 10_000)
    tc = compute_turn_cost(t, PRICES)
    assert tc.total_usd == pytest.approx(0.122, abs=1e-9)


def test_turn_without_split_prices_all_writes_at_5m_and_flags_them() -> None:
    # No breakdown on the record: 1M written, tier unknown -> 5m rate, and visibly so.
    t = _turn("claude-opus-5-5", 0, 1_000_000, 0, 0, 0, known=0)
    tc = compute_turn_cost(t, PRICES)
    assert tc.cache_creation_cost == pytest.approx(5.00, abs=1e-9)  # 1M * $5 (1.25 x $4)
    assert tc.cache_write_unspecified_tokens == 1_000_000
    assert tc.cache_write_1h_tokens == 0


def test_partially_known_split_counts_only_the_remainder_as_unspecified() -> None:
    t = _turn("claude-opus-5-5", 0, 1_000_000, 100_000, 0, 0, known=700_000)
    tc = compute_turn_cost(t, PRICES)
    assert tc.cache_write_unspecified_tokens == 300_000
    # 0.9M at $5 + 0.1M at $8
    assert tc.cache_creation_cost == pytest.approx(4.50 + 0.80, abs=1e-9)


def test_legacy_turndigest_without_split_fields_is_unchanged() -> None:
    legacy = TurnDigest(
        turn_index=0,
        role="ai",
        tool_names=[],
        content_snippet="",
        token_count_input=1_000_000,
        token_count_output=0,
        cache_read=0,
        h2_duplicate=False,
        cache_creation=1_000_000,
        model="claude-sonnet-5-5",
    )
    assert compute_turn_cost(legacy, PRICES).cache_creation_cost == pytest.approx(2.50)


def test_explicit_cache_duration_still_forces_all_writes() -> None:
    t = _turn("claude-opus-5-5", 0, 1_000_000, 400_000, 0, 0)
    assert compute_turn_cost(t, PRICES, cache_duration="5min").cache_creation_cost == (
        pytest.approx(5.00)
    )
    assert compute_turn_cost(t, PRICES, cache_duration="1hr").cache_creation_cost == (
        pytest.approx(8.00)
    )


def test_session_cost_totals_and_note() -> None:
    sub = _turn("claude-opus-5-5", 0, 1_000_000, 1_000_000, 0, 0)
    main = _turn("claude-opus-5-5", 0, 1_000_000, 0, 0, 0, known=0)
    digest = SessionDigest(
        session_id="s",
        domain="unknown",
        resolved=False,
        total_tokens=2_000_000,
        turn_count=1,
        h2_duplicate_count=0,
        cache_hit_rate=0.0,
        p25_token_ratio=1.0,
        output_tokens_available=True,
        task_description="",
        turns=[main],
        subagent_turns=[sub],
    )
    sc = compute_session_cost(digest, PRICES)
    assert sc.total_usd == pytest.approx(5.00 + 8.00)  # main 1M@5m + subagent 1M@1h
    assert sc.subagent_usd == pytest.approx(8.00)
    assert sc.cache_write_1h_tokens == 1_000_000
    assert sc.cache_write_unspecified_tokens == 1_000_000
    assert "assumed 5-minute (1000000 of 2000000 cache-write tokens" in sc.domain_of_validity


# --- adapter: dedupe max semantics, missing breakdown, subagent aggregation ------------------


def _rec(mid: str, cc: int, five: int | None, hour: int | None, model: str = "claude-opus-5-5"):
    usage: dict[str, Any] = {
        "input_tokens": 1,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": 0,
        "output_tokens": 1,
    }
    if five is not None or hour is not None:
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": five or 0,
            "ephemeral_1h_input_tokens": hour or 0,
        }
    return {
        "type": "assistant",
        "uuid": f"u-{mid}-{cc}-{hour}",
        "message": {
            "id": mid,
            "model": model,
            "content": [{"type": "text", "text": "x"}],
            "usage": usage,
        },
    }


def _write(path: Path, recs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def test_adapt_dedupes_message_id_with_per_field_max_and_prices_1h(tmp_path: Path) -> None:
    # Two records of ONE response (same message.id) repeat the usage; a streaming partial has a
    # smaller 1h figure. Max per field => cc 1,000,000, 1h 400,000, known 1,000,000.
    session = tmp_path / "sess.jsonl"
    _write(
        session,
        [
            _rec("m1", 1_000_000, 600_000, 400_000),
            _rec("m1", 1_000_000, 700_000, 300_000),
            _rec("m2", 500_000, None, None),  # no breakdown -> unspecified
        ],
    )
    record = adapt_session(session)
    from tes._digest import reconstruct_digest

    sc = compute_session_cost(reconstruct_digest(record["digest"]), PRICES)
    # m1: 0.6M@$5 + 0.4M@$8 = 6.20 ; m2: 0.5M@$5 = 2.50 ; plus 1 fresh-in/1 out token each
    # (2 x 4e-6 + 2 x 20e-6 = 4.8e-5)
    assert sc.total_usd == pytest.approx(6.20 + 2.50 + 4.8e-5, abs=1e-9)
    ai = [t for t in record["digest"]["turns"] if t["role"] == "ai"]
    assert sum(t["cache_creation_1h"] for t in ai) == 400_000
    assert sum(t["cache_creation_tier_known"] for t in ai) == 1_000_000
    assert sum(t["cache_creation"] for t in ai) == 1_500_000
    assert sc.cache_write_unspecified_tokens == 500_000
    from tes.adapt import COST_VERSION

    assert record["cost_version"] == COST_VERSION


def test_real_tokens_inputs_unchanged_by_the_split(tmp_path: Path) -> None:
    session = tmp_path / "s.jsonl"
    _write(session, [_rec("m1", 1000, 0, 1000)])
    record = adapt_session(session)
    # total = input 1 + cache_creation 1000 + read 0 + output 1; the 1h subset adds nothing.
    assert record["total_tokens"] == 1002


def test_subagent_aggregation_sums_the_split_per_model(tmp_path: Path) -> None:
    session = tmp_path / "sess.jsonl"
    _write(session, [_rec("p", 10, 10, 0)])
    sub = tmp_path / "sess" / "subagents"
    _write(
        sub / "agent-a.jsonl",
        [
            _rec("a1", 1000, 600, 400),
            _rec("a1", 1000, 600, 400),  # duplicate content-block record, counted once
            _rec("a2", 2000, 0, 2000),
            _rec("a3", 300, None, None),
        ],
    )
    usage = collect_subagent_usage(session)
    assert usage is not None
    (turn,) = usage["turns"]
    assert turn["cache_creation"] == 3300
    assert turn["cache_creation_1h"] == 2400
    assert turn["cache_creation_tier_known"] == 3000
    # real_tokens must not move: input 3 + cache_creation 3300 + output 3 = 3306
    assert usage["real_tokens"] == 3306


# --- store: stale cost rows are re-priced, baselines are untouched -------------------------


def test_backfill_reprices_rows_with_stale_cost_version_only(tmp_path: Path) -> None:
    import shutil
    import sqlite3

    from tes.adapt import ADAPTER_VERSION, COST_VERSION
    from tes.store import backfill_waste, open_db

    fixture = Path(__file__).parent / "fixtures" / "usage_dedupe" / "multi_block_session.jsonl"
    src = tmp_path / "proj" / "sess-1.jsonl"
    src.parent.mkdir()
    shutil.copy(fixture, src)
    db = tmp_path / "b.db"
    conn = open_db(db)
    conn.execute(
        "INSERT INTO sessions (session_id, task_type, source_path, source_mtime, source_hash, "
        "scored_at, axes_scored, real_tokens, scope_status, baseline_available, band_verdict, "
        "interpretation, token_domain_of_validity, trajectory_domain_of_validity, "
        "waste_event_count, waste_events, waste_domain_of_validity, turn_count, adapter_version, "
        "session_cost_usd) VALUES ('sess-1', 'ml-eval', ?, 0, 'h', 't', '[]', 7, 'in_scope', 1, "
        "'within_band', 'i', 'd', 'd', 0, '[]', 'd', 80, ?, 0.0001)",
        (str(src), ADAPTER_VERSION),  # current adapter_version, cost_version NULL (pre-split)
    )
    conn.commit()
    conn.close()

    assert backfill_waste(db_path=db)["refreshed"] == 1
    conn = open_db(db)
    row: sqlite3.Row = conn.execute("SELECT * FROM sessions").fetchone()
    assert row["cost_version"] == COST_VERSION
    assert row["session_cost_usd"] != 0.0001
    conn.close()
    assert backfill_waste(db_path=db)["refreshed"] == 0
