from __future__ import annotations

"""Cache-churn detector: definition, causes, edge cases, pricing, injection recall/precision.

The seeded-injection tests are a REGRESSION CHECK of the code on clear-cut synthetic cases, not an
accuracy estimate: the generator (tests/churn_synth.py) plants events with the same ideas the
detector uses (a gap well past the TTL, a read that collapses to a small constant) and has no
near-misses, so a high recall there says the code does what it was written to do, nothing about
how often it is right on real sessions. The separate "harder synthetic cases" test below plants
borderline cases and records what the detector does with them; those rates are observations, and
the thresholds were not tuned to make them pass.

Expected dollar figures are computed by hand from the published per-MTok rates (see each
test), not by calling the code under test. Rates used (tes/data/prices.json):
claude-sonnet-5-5 in $2.00, 5m write 1.25x = $2.50, 1h write 2x = $4.00, read 0.05x = $0.10 /MTok;
claude-haiku-5-5 in $0.10 (long tier over 100k prompt tokens: $0.50), same multipliers
except read 0.1x.
"""

from typing import Any

import pytest
from tes import cache_churn as cc

from tests.churn_synth import (
    T0,
    asst_rec,
    compact_boundary,
    summary_rec,
    user_rec,
)

SONNET = "claude-sonnet-5-5"


def _chain(recs: list[dict[str, Any]], kind: str = "main", **kw: Any) -> cc.ChainInput:
    return cc.read_chain_records(recs, kind=kind, label="t", **kw)


# ------------------------------------------------------------------ turn values and rates


def _turn(model: str, *, inp: int = 3, cc_: int = 0, cr: int = 0, has_split: bool = True) -> Any:
    return cc.CacheTurn(
        mid="m",
        model=model,
        inp=inp,
        cc=cc_,
        cr=cr,
        cc_1h=0,
        i_first=0,
        i_last=0,
        has_split=has_split,
    )


def test_prompt_is_input_plus_writes_plus_reads() -> None:
    assert _turn(SONNET, inp=3, cc_=100, cr=900).prompt == 1_003


def test_unsplit_writes_are_reported_only_when_the_split_is_missing() -> None:
    assert _turn(SONNET, cc_=700).unsplit_cc == 0
    assert _turn(SONNET, cc_=700, has_split=False).unsplit_cc == 700


def test_rates_are_per_token_from_the_price_table() -> None:
    # sonnet: 5m write $2.50, 1h write $4.00, read $0.10 per MTok (tes/data/prices.json)
    r = cc.RateTable().rates(_turn(SONNET, cc_=1_000, cr=1_000))
    assert r is not None
    assert (r.w5, r.w1, r.rd) == pytest.approx((2.5e-6, 4.0e-6, 1.0e-7))


def test_long_context_tier_is_chosen_by_prompt_size() -> None:
    # haiku: a prompt over 100k tokens is billed at the long tier (write $0.625, read $0.05 /MTok)
    haiku = "claude-haiku-5-5"
    short = cc.RateTable().rates(_turn(haiku, cr=10_000))
    long_ = cc.RateTable().rates(_turn(haiku, cr=150_000))
    assert short is not None and long_ is not None
    assert long_.w5 == pytest.approx(0.625e-6) and long_.rd == pytest.approx(0.05e-6)
    assert short.w5 < long_.w5


def test_unknown_or_blank_model_is_unpriced_not_zero() -> None:
    table = cc.RateTable()
    assert table.rates(_turn("mystery-model-9")) is None
    assert table.rates(_turn("  ")) is None


# ------------------------------------------------------------------ parsing / dedupe / hostile


def test_records_of_one_message_id_are_one_turn_with_max_usage() -> None:
    a = asst_rec("a", SONNET, cr=99_000, cc=1_000, ts=T0 + 1)
    a2 = asst_rec("a", SONNET, cr=99_000, cc=1_000, ts=T0 + 1.5)
    a2["message"]["usage"]["output_tokens"] = 999  # streaming partial: other fields repeat
    chain = _chain([user_rec(T0), a, a2])
    assert len(chain.turns) == 1 and chain.turns[0].cc == 1_000


def test_cross_file_duplicate_ids_are_counted_once() -> None:
    main = _chain([user_rec(T0), asst_rec("dup", SONNET, cr=0, cc=9_000, ts=T0 + 1)])
    sub = _chain(
        [
            user_rec(T0),
            asst_rec("dup", SONNET, cr=0, cc=9_000, ts=T0 + 1),
            asst_rec("own", SONNET, cr=9_000, cc=10, ts=T0 + 5),
        ],
        kind="subagent",
    )
    seen: set[str] = set()
    assert cc.drop_seen_turns(main, seen) == 0
    assert cc.drop_seen_turns(sub, seen) == 1
    assert [t.mid for t in sub.turns] == ["own"]


def test_parse_ts_accepts_iso_and_rejects_everything_else() -> None:
    assert cc.parse_ts("2026-01-01T00:00:10Z") - cc.parse_ts("2026-01-01T00:00:00Z") == 10.0
    for bad in (None, "", "garbage", 12345, ["2026-01-01T00:00:00Z"]):
        assert cc.parse_ts(bad) is None


def test_hostile_records_never_raise_and_only_usage_turns_survive() -> None:
    hostile: list[Any] = [
        "not a dict",
        {"type": "assistant"},
        {"type": "assistant", "message": "x"},
        {"type": "assistant", "message": {"id": "u", "model": SONNET}},  # no usage
        {"type": "assistant", "message": {"id": "v", "model": SONNET, "usage": "bad"}},
        {
            "type": "assistant",
            "timestamp": "garbage",
            "message": {
                "id": "w",
                "model": SONNET,
                "usage": {"input_tokens": "9", "cache_read_input_tokens": -5, "cache_creation": 3},
            },
        },
        {"type": "user", "timestamp": 12345},
        {"type": "system"},
    ]
    chain = _chain(hostile)
    assert [t.mid for t in chain.turns] == ["w"]  # the zero-usage turn; the rest is skipped
    assert chain.turns[0].cr == 0 and chain.turns[0].ts_first is None


def test_compaction_markers_are_collected_in_record_order() -> None:
    chain = _chain([user_rec(T0), compact_boundary(), summary_rec()])
    assert [kind for _, kind in chain.compact_marks] == [
        cc.MARK_COMPACT_BOUNDARY,
        cc.MARK_COMPACT_SUMMARY,
    ]
    assert chain.compact_marks[0][0] < chain.compact_marks[1][0]
