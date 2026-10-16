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
    MODEL_B,
    T0,
    asst_rec,
    compact_boundary,
    summary_rec,
    user_rec,
)

SONNET = "claude-sonnet-5-5"


def _chain(recs: list[dict[str, Any]], kind: str = "main", **kw: Any) -> cc.ChainInput:
    return cc.read_chain_records(recs, kind=kind, label="t", **kw)


def _detect(recs: list[dict[str, Any]], **kw: Any) -> cc.ChainResult:
    return cc.detect_chain(_chain(recs, **kw), cc.RateTable())


def _pair(
    cr1: int,
    cc1: int,
    cr2: int,
    cc2: int,
    *,
    gap: float = 30.0,
    model2: str = SONNET,
    one_hour1: bool = False,
    between: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Two turns: (cr1, cc1) then (cr2, cc2), with the given start-to-start gap."""
    return [
        user_rec(T0),
        asst_rec("a", SONNET, cr=cr1, cc=cc1, cc_1h=cc1 if one_hour1 else 0, ts=T0 + 2),
        user_rec(T0 + gap),
        *(between or []),
        asst_rec("b", model2, cr=cr2, cc=cc2, ts=T0 + gap + 2),
    ]


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


# ------------------------------------------------------------------ event definition


def test_turn_zero_is_never_an_event() -> None:
    res = _detect([user_rec(T0), asst_rec("a", SONNET, cr=0, cc=90_000, ts=T0 + 1)])
    assert res.events == [] and res.n_turns == 1


def test_prefix_growth_without_read_drop_is_not_an_event() -> None:
    # A 80k-token file read: the whole previous prefix is read back (cr == C_prev) and a lot is
    # written. That is growth, not churn.
    res = _detect(_pair(50_000, 1_000, 51_000, 80_000))
    assert res.events == []


def test_small_prefix_is_not_an_event() -> None:
    res = _detect(_pair(3_000, 1_000, 100, 3_900, gap=4_000))  # C_prev = 4,000 < 5,000
    assert res.events == []


def test_thresholds_are_strict() -> None:
    # cr_t == 0.5 * cr_{t-1} is NOT a drop (strict <).
    assert _detect(_pair(11_000, 0, 5_500, 20_000, gap=4_000)).events == []
    assert _detect(_pair(11_000, 0, 5_499, 20_000, gap=4_000)).events != []

    # cc_t == 0.2 * P is NOT a large re-write (strict >): input 0, cr 1,000, cc 250 -> P 1,250.
    def with_cc(cc_t: int) -> list[dict[str, Any]]:
        return [
            user_rec(T0),
            asst_rec("a", SONNET, cr=20_000, cc=0, ts=T0 + 1),
            user_rec(T0 + 4_000),
            asst_rec("b", SONNET, cr=1_000, cc=cc_t, inp=0, ts=T0 + 4_001),
        ]

    assert _detect(with_cc(250)).events == []
    assert len(_detect(with_cc(251)).events) == 1


def test_event_has_proof_turns_and_marker_free_fields() -> None:
    res = _detect(_pair(99_000, 1_000, 10_000, 90_000, gap=4_000))
    (e,) = res.events
    assert (e.prev.turn_index, e.cur.turn_index) == (0, 1)
    assert (e.prev.cache_read, e.prev.cache_creation) == (99_000, 1_000)
    assert (e.cur.cache_read, e.cur.cache_creation, e.cur.cache_creation_1h) == (10_000, 90_000, 0)
    assert e.gap_s == pytest.approx(4_000.0)
    assert e.marker_index is None and e.chain_kind == "main"


# ------------------------------------------------------------------ causes


def test_ttl_gap_on_five_minute_chain_boundary() -> None:
    at = _detect(_pair(99_000, 1_000, 10_000, 90_000, gap=300.0)).events
    over = _detect(_pair(99_000, 1_000, 10_000, 90_000, gap=301.0)).events
    assert at[0].cause == cc.CAUSE_UNEXPLAINED  # a gap of exactly 300 s is not over the TTL
    assert over[0].cause == cc.CAUSE_TTL_GAP and over[0].ttl_s == 300.0
    assert over[0].avoidability == cc.AVOIDABLE


def test_ttl_grace_moves_boundary_events_to_unexplained() -> None:
    recs = _pair(99_000, 1_000, 10_000, 90_000, gap=320.0)
    default = cc.detect_chain(_chain(recs), cc.RateTable())
    graced = cc.detect_chain(_chain(recs), cc.RateTable(), cc.ChurnParams(ttl_grace_s=30.0))
    assert default.events[0].cause == cc.CAUSE_TTL_GAP
    assert graced.events[0].cause == cc.CAUSE_UNEXPLAINED
    assert cc.TTL_GRACE_S == 0.0  # the documented boundary is the default


def test_one_hour_chain_ttl_and_unavoidable_idle() -> None:
    inside = _detect(_pair(99_000, 1_000, 10_000, 90_000, gap=3_000.0, one_hour1=True)).events
    over = _detect(_pair(99_000, 1_000, 10_000, 90_000, gap=7_200.0, one_hour1=True)).events
    assert inside[0].cause == cc.CAUSE_UNEXPLAINED  # idle 50 min on a 1h cache: not TTL
    assert over[0].cause == cc.CAUSE_TTL_GAP and over[0].ttl_s == 3_600.0
    assert over[0].avoidability == cc.UNAVOIDABLE


def test_idle_over_an_hour_on_five_minute_chain_is_unavoidable() -> None:
    (e,) = _detect(_pair(99_000, 1_000, 10_000, 90_000, gap=5_000.0)).events
    assert e.cause == cc.CAUSE_TTL_GAP and e.avoidability == cc.UNAVOIDABLE


def test_ttl_tier_comes_from_most_recent_turn_that_wrote() -> None:
    # turn 0 wrote 1h, turn 1 wrote nothing (cc=0), so the tier is still 1h at turn 2.
    recs = [
        user_rec(T0),
        asst_rec("a", SONNET, cr=0, cc=50_000, cc_1h=50_000, ts=T0 + 1),
        user_rec(T0 + 30),
        asst_rec("b", SONNET, cr=50_000, cc=0, ts=T0 + 31),
        user_rec(T0 + 30 + 1_000),
        asst_rec("c", SONNET, cr=1_000, cc=49_000, ts=T0 + 1_032),
    ]
    (e,) = _detect(recs).events
    assert e.ttl_s == 3_600.0 and e.cause == cc.CAUSE_UNEXPLAINED  # 1,000 s is inside the 1h TTL


def test_model_switch() -> None:
    (e,) = _detect(_pair(99_000, 1_000, 10_000, 90_000, gap=30.0, model2=MODEL_B)).events
    assert e.cause == cc.CAUSE_MODEL_SWITCH and e.avoidability == cc.AVOIDABLE
    assert e.prev.model == SONNET and e.cur.model == MODEL_B


@pytest.mark.parametrize("marker", [compact_boundary(), summary_rec()])
def test_compaction_marker_forms(marker: dict[str, Any]) -> None:
    (e,) = _detect(_pair(99_000, 1_000, 10_000, 90_000, between=[marker])).events
    assert e.cause == cc.CAUSE_COMPACTION and e.avoidability == cc.UNAVOIDABLE
    assert e.marker_index is not None


def test_cause_priority_compaction_over_model_over_ttl() -> None:
    both = _detect(
        _pair(
            99_000, 1_000, 10_000, 90_000, gap=9_000, model2=MODEL_B, between=[compact_boundary()]
        )
    ).events
    model_ttl = _detect(_pair(99_000, 1_000, 10_000, 90_000, gap=9_000, model2=MODEL_B)).events
    assert both[0].cause == cc.CAUSE_COMPACTION
    assert model_ttl[0].cause == cc.CAUSE_MODEL_SWITCH


def test_marker_outside_the_two_turns_does_not_count() -> None:
    recs = [compact_boundary(), *_pair(99_000, 1_000, 10_000, 90_000, gap=30.0)]
    (e,) = _detect(recs).events
    assert e.cause == cc.CAUSE_UNEXPLAINED  # the only marker precedes both turns


def test_unexplained_is_never_given_a_cause() -> None:
    (e,) = _detect(_pair(99_000, 1_000, 10_000, 90_000, gap=30.0)).events
    assert e.cause == cc.CAUSE_UNEXPLAINED and e.avoidability == cc.UNKNOWN
    assert cc.CAUSE_UNEXPLAINED not in cc.CAUSES  # not a shipped cause


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
