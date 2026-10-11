from __future__ import annotations

"""Cache-churn detector: definition, causes, edge cases, pricing, injection recall/precision.

Expected dollar figures are computed by hand from the published per-MTok rates (see each
test), not by calling the code under test. Rates used (tes/data/prices.json):
claude-sonnet-5-5 in $2.00, 5m write 1.25x = $2.50, 1h write 2x = $4.00, read 0.05x = $0.10 /MTok;
claude-haiku-5-5 in $0.10 (long tier over 100k prompt tokens: $0.50), same multipliers
except read 0.1x.
"""

import json
import random
from pathlib import Path
from typing import Any

import pytest
from tes import cache_churn as cc

from tests.churn_synth import (
    MODEL_A,
    MODEL_B,
    T0,
    asst_rec,
    build_chain,
    clean_chain,
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


# ------------------------------------------------------------------ pricing


def test_churn_tokens_and_usd_hand_computed() -> None:
    # C_prev = 100,000; cr_t = 10,000; cc_t = 90,000 (5m). churn_tokens = min(90,000, 90,000).
    # $ = 90,000 * (2.50 - 0.10) / 1e6 = 0.216
    (e,) = _detect(_pair(99_000, 1_000, 10_000, 90_000, gap=4_000)).events
    assert e.churn_tokens == 90_000
    assert e.churn_usd == pytest.approx(0.216, abs=1e-9)


def test_growth_is_excluded_from_churn_tokens() -> None:
    # C_prev = 100,000; cr_t = 10,000; cc_t = 120,000 -> only 90,000 of the write was the old
    # prefix, 30,000 is new content: $ = 90,000 * (2.50 - 0.10) / 1e6 = 0.216
    (e,) = _detect(_pair(99_000, 1_000, 10_000, 120_000, gap=4_000)).events
    assert e.churn_tokens == 90_000
    assert e.churn_usd == pytest.approx(0.216, abs=1e-9)


def test_blended_write_rate_uses_the_turns_own_split() -> None:
    # cc_t = 90,000, of which 30,000 are 1h writes: blended = (60,000*2.5 + 30,000*4.0)/90,000
    # = 3.0 $/MTok; churn = 90,000 tokens -> 90,000 * (3.0 - 0.10) / 1e6 = 0.261
    recs = _pair(99_000, 1_000, 10_000, 90_000, gap=4_000)
    recs[-1]["message"]["usage"]["cache_creation"] = {
        "ephemeral_5m_input_tokens": 60_000,
        "ephemeral_1h_input_tokens": 30_000,
    }
    (e,) = _detect(recs).events
    assert e.churn_usd == pytest.approx(0.261, abs=1e-9)


def test_haiku_long_context_tier_is_honoured() -> None:
    # prompt = 3 + 150,000 + 5,000 = 155,003 > 100k -> long tier: in $0.50, 5m write $0.625,
    # read 0.1x = $0.05 /MTok; churn tokens = min(150,000, 205,000 - 5,000) = 150,000
    # -> 150,000 * (0.625 - 0.05) / 1e6 = 0.08625
    recs = [
        user_rec(T0),
        asst_rec("a", "claude-haiku-5-5", cr=200_000, cc=5_000, ts=T0 + 1),
        user_rec(T0 + 4_000),
        asst_rec("b", "claude-haiku-5-5", cr=5_000, cc=150_000, ts=T0 + 4_001),
    ]
    (e,) = _detect(recs).events
    assert e.churn_usd == pytest.approx(0.08625, abs=1e-9)


def test_unpriced_model_is_unpriced_not_zero() -> None:
    recs = _pair(99_000, 1_000, 10_000, 90_000, gap=4_000, model2="mystery-model-9")
    (e,) = _detect(recs).events
    assert e.cause == cc.CAUSE_MODEL_SWITCH and e.churn_usd is None
    s = cc.summarise([cc.detect_chain(_chain(recs), cc.RateTable())])
    assert s.total.events == 1 and s.total.unpriced_events == 1 and s.total.churn_usd == 0.0
    assert s.advice == []  # no net claimed when a write is unpriced


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


def test_synthetic_model_records_are_ignored() -> None:
    recs = [
        user_rec(T0),
        asst_rec("a", SONNET, cr=99_000, cc=1_000, ts=T0 + 1),
        user_rec(T0 + 4_000),
        asst_rec("s", "<synthetic>", cr=0, cc=0, ts=T0 + 4_001),
        asst_rec("b", SONNET, cr=10_000, cc=90_000, ts=T0 + 4_002),
    ]
    chain = _chain(recs)
    assert [t.mid for t in chain.turns] == ["a", "b"]
    assert len(cc.detect_chain(chain, cc.RateTable()).events) == 1


def test_gap_is_start_to_start_and_falls_back_to_assistant_timestamps() -> None:
    # trigger timestamps present: 4,000 s apart even though the assistant answered at +900 s.
    with_trigger = _detect(
        [
            user_rec(T0),
            asst_rec("a", SONNET, cr=99_000, cc=1_000, ts=T0 + 900),
            user_rec(T0 + 4_000),
            asst_rec("b", SONNET, cr=10_000, cc=90_000, ts=T0 + 4_900),
        ]
    ).events[0]
    assert with_trigger.gap_s == pytest.approx(4_000.0)
    # no user records: assistant timestamps are used
    fallback = _detect(
        [
            asst_rec("a", SONNET, cr=99_000, cc=1_000, ts=T0),
            asst_rec("b", SONNET, cr=10_000, cc=90_000, ts=T0 + 4_100),
        ]
    ).events[0]
    assert fallback.gap_s == pytest.approx(4_100.0)


def test_missing_timestamps_leave_the_cause_unexplained() -> None:
    recs = [
        asst_rec("a", SONNET, cr=99_000, cc=1_000),
        asst_rec("b", SONNET, cr=10_000, cc=90_000),
    ]
    (e,) = _detect(recs).events
    assert e.gap_s is None and e.cause == cc.CAUSE_UNEXPLAINED


def test_out_of_order_timestamps_never_claim_a_ttl_gap() -> None:
    recs = _pair(99_000, 1_000, 10_000, 90_000, gap=-500.0)
    (e,) = _detect(recs).events
    assert e.gap_s == pytest.approx(-500.0) and e.cause == cc.CAUSE_UNEXPLAINED


def test_hostile_records_do_not_raise() -> None:
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
    res = _detect(hostile)
    assert res.events == [] and res.n_turns == 1  # only "w" is a (zero-usage) turn


def test_empty_chain_and_empty_file(tmp_path: Path) -> None:
    assert _detect([]).events == []
    f = tmp_path / "proj" / "s.jsonl"
    f.parent.mkdir()
    f.write_text("", encoding="utf-8")
    s = cc.analyze_session(f)
    assert s.total.events == 0 and s.events == [] and s.advice == []


# ------------------------------------------------------------------ session level


def _write(path: Path, recs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def test_analyze_session_main_and_subagents_never_mixed(tmp_path: Path) -> None:
    rng = random.Random(42)
    main_p = tmp_path / "proj" / "s1.jsonl"
    _write(main_p, build_chain(rng, 12, {5: "TTL_GAP"}, one_hour=True, prefix="main"))
    for n in range(2):
        sub = build_chain(rng, 10, {4: "TTL_GAP", 7: "TTL_GAP"}, prefix=f"sub{n}_")
        for rec in sub:
            rec["isSidechain"] = True
        _write(main_p.with_suffix("") / "subagents" / f"agent-{n}.jsonl", sub)
    s = cc.analyze_session(main_p)
    assert s.by_kind["main"].events == 1 and s.by_kind["subagent"].events == 4
    assert s.by_bucket[cc.CAUSE_TTL_GAP].events == 5
    assert s.total.events == 5 and len(s.chains) == 3
    # the main chain is 1h-tier, the gap is over the hour: unavoidable; subagent gaps: avoidable
    assert s.unavoidable_usd > 0 and s.avoidable_usd > 0


def test_main_transcript_skips_sidechain_records(tmp_path: Path) -> None:
    rng = random.Random(1)
    recs = build_chain(rng, 6, {3: "TTL_GAP"})
    for rec in recs:
        rec["isSidechain"] = True
    p = tmp_path / "proj" / "s.jsonl"
    _write(p, recs)
    assert cc.analyze_session(p).total.events == 0  # belongs to a subagent chain, not the main


def test_session_with_cross_file_duplicate_counts_event_once(tmp_path: Path) -> None:
    rng = random.Random(7)
    recs = build_chain(rng, 8, {4: "TTL_GAP"}, prefix="x")
    p = tmp_path / "proj" / "s.jsonl"
    _write(p, recs)
    _write(p.with_suffix("") / "subagents" / "agent-a.jsonl", recs)  # same ids again
    s = cc.analyze_session(p)
    assert s.total.events == 1 and s.duplicate_turns_dropped == 8


def test_unrelated_lines_are_skipped_and_spaced_json_still_parses(tmp_path: Path) -> None:
    f = tmp_path / "proj" / "s.jsonl"
    f.parent.mkdir()
    spaced = json.dumps(asst_rec("a", SONNET, cr=99_000, cc=1_000, ts=T0), indent=None)
    spaced = spaced.replace('"type": "assistant"', '"type" :  "assistant"')
    lines = [
        json.dumps({"type": "attachment", "attachment": {"type": "x"}}),
        "{broken json",
        spaced,
        json.dumps(user_rec(T0 + 4_000)),
        json.dumps(asst_rec("b", SONNET, cr=10_000, cc=90_000, ts=T0 + 4_001)),
    ]
    f.write_text("\n".join(lines), encoding="utf-8")  # no trailing newline: a live file
    s = cc.analyze_session(f)
    assert s.total.events == 1 and s.chains[0].n_turns == 2


# ------------------------------------------------------------------ TTL advice


def _sub_session(tmp_path: Path, gaps: int, n_turns: int = 40) -> cc.SessionChurn:
    rng = random.Random(3)
    p = tmp_path / "proj" / "s.jsonl"
    _write(p, [user_rec(T0)])
    inj = {5 + 7 * i: "TTL_GAP" for i in range(gaps)}
    _write(
        p.with_suffix("") / "subagents" / "agent-a.jsonl",
        build_chain(rng, n_turns, inj, prefix="s"),
    )
    return cc.analyze_session(p)


def test_advice_is_shown_only_when_net_positive(tmp_path: Path) -> None:
    many = _sub_session(tmp_path / "a", gaps=5)
    none = _sub_session(tmp_path / "b", gaps=0)
    assert none.advice == []
    (a,) = many.advice
    assert a.scope == "subagent" and a.setting == "subagentPromptCacheTtl"
    assert a.env_var == "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL" and a.value == "1h"
    assert a.net_usd == pytest.approx(a.saved_usd - a.premium_usd) and a.net_usd > 0
    assert a.events_avoided == 5 and "300 s to 1 h" in a.assumptions


def test_advice_withheld_when_premium_exceeds_savings(tmp_path: Path) -> None:
    # one gap in a long chain: reprice all the 5m writes of 400 turns > one saved re-write
    s = _sub_session(tmp_path, gaps=1, n_turns=400)
    assert s.total.events == 1 and s.advice == []


def test_advice_hand_computed(tmp_path: Path) -> None:
    # One subagent chain, all 5m writes, three gaps of 600 s. Per gap: cr 10,000 -> 2,000 and
    # 8,000 re-written, so churn = min(8,000, 10,000 - 2,000) = 8,000 tokens each.
    #   saved   = 3 * 8,000 * (2.50 - 0.10) / 1e6            = 0.0576
    #   premium = (10,000 + 3 * 8,000) * (4.00 - 2.50) / 1e6 = 0.0510   -> net 0.0066
    recs: list[dict[str, Any]] = [
        user_rec(T0),
        asst_rec("t0", SONNET, cr=0, cc=10_000, ts=T0 + 1),
    ]
    t = T0
    for i in range(3):
        t += 30
        recs += [user_rec(t), asst_rec(f"r{i}", SONNET, cr=10_000, cc=0, ts=t + 1)]
        t += 600
        recs += [user_rec(t), asst_rec(f"g{i}", SONNET, cr=2_000, cc=8_000, ts=t + 1)]
    for r in recs:
        r["isSidechain"] = True
    p = tmp_path / "proj" / "s.jsonl"
    _write(p, [user_rec(T0)])
    _write(p.with_suffix("") / "subagents" / "agent-a.jsonl", recs)
    s = cc.analyze_session(p)
    (a,) = s.advice
    assert a.events_avoided == 3
    assert a.saved_usd == pytest.approx(0.0576, abs=1e-9)
    assert a.premium_usd == pytest.approx(0.0510, abs=1e-9)
    assert a.net_usd == pytest.approx(0.0066, abs=1e-9)


def test_main_chain_advice_needs_a_five_minute_main_chain(tmp_path: Path) -> None:
    rng = random.Random(5)
    p5 = tmp_path / "a" / "s.jsonl"
    _write(p5, build_chain(rng, 40, {5: "TTL_GAP", 12: "TTL_GAP", 20: "TTL_GAP"}, prefix="m"))
    p1 = tmp_path / "b" / "s.jsonl"
    _write(p1, build_chain(rng, 40, {5: "TTL_GAP"}, one_hour=True, prefix="m"))
    s5, s1 = cc.analyze_session(p5), cc.analyze_session(p1)
    assert [a.scope for a in s5.advice] == ["main"]
    assert s5.advice[0].setting == "promptCacheTtl"
    assert s1.advice == []  # already a 1h cache: nothing to reprice, nothing to recommend


# ------------------------------------------------------------------ seeded injection


def _inject_corpus(seed: int, n_chains: int) -> tuple[list[cc.ChainResult], dict[str, int]]:
    rng = random.Random(seed)
    table = cc.RateTable()
    injected = {c: 0 for c in cc.CAUSES}
    results: list[cc.ChainResult] = []
    expected: dict[tuple[int, int], str] = {}
    for n in range(n_chains):
        n_turns = rng.randint(40, 90)
        one_hour = rng.random() < 0.4
        positions = sorted(rng.sample(range(2, n_turns - 1, 3), 4))  # >= 3 apart, never turn 0/1
        inj = {p: rng.choice(cc.CAUSES) for p in positions}
        recs = build_chain(rng, n_turns, inj, one_hour=one_hour, prefix=f"c{n}_")
        res = cc.detect_chain(_chain(recs), table)
        results.append(res)
        for k, cause in inj.items():
            injected[cause] += 1
            expected[(n, k)] = cause
    detected = {(n, e.cur.turn_index): e.cause for n, r in enumerate(results) for e in r.events}
    hits = {c: 0 for c in cc.CAUSES}
    for key, cause in expected.items():
        if detected.get(key) == cause:
            hits[cause] += 1
    false_events = [key for key in detected if key not in expected]
    assert false_events == [], f"events on clean turns: {false_events[:5]}"
    return results, hits | {f"n_{c}": injected[c] for c in cc.CAUSES}


def test_seeded_injection_recall_per_cause_and_precision_on_clean_chains() -> None:
    _, stats = _inject_corpus(seed=42, n_chains=60)
    for cause in cc.CAUSES:
        n = stats[f"n_{cause}"]
        assert n >= 30, (cause, n)  # enough injections for the rate to mean something
        assert stats[cause] / n >= 0.9, (cause, stats[cause], n)


def test_clean_chains_have_no_events() -> None:
    rng = random.Random(42)
    table = cc.RateTable()
    for n in range(40):
        recs = clean_chain(rng, rng.randint(30, 120), one_hour=n % 2 == 0, prefix=f"k{n}_")
        assert cc.detect_chain(_chain(recs), table).events == []


def test_models_in_synthetic_chains_are_priced() -> None:
    # guards the fixture itself: if these ever stopped resolving the injection tests would
    # silently exercise the unpriced path.
    table = cc.RateTable()
    for m in (MODEL_A, MODEL_B):
        t = cc.CacheTurn("x", m, 1, 1, 1, 0, 0, 0)
        assert table.rates(t) is not None


def test_committed_fixture_events() -> None:
    fixture = Path(__file__).parent / "fixtures" / "cache_churn" / "session.jsonl"
    recs = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines() if line]
    res = _detect(recs)
    causes = [e.cause for e in res.events]
    assert causes == [cc.CAUSE_TTL_GAP, cc.CAUSE_MODEL_SWITCH, cc.CAUSE_COMPACTION]
    assert [e.cur.turn_index for e in res.events] == [4, 8, 12]


# ------------------------------------------------------------------ unsplit (tier unknown) writes


def _unsplit_pair(gap: float, *, split_first: bool = False) -> list[dict[str, Any]]:
    """Two turns like ``_pair`` (prefix lost on the second) with no 5m/1h split on the writes."""
    return [
        user_rec(T0),
        asst_rec("a", SONNET, cr=99_000, cc=1_000, ts=T0 + 2, split=split_first),
        user_rec(T0 + gap),
        asst_rec("b", SONNET, cr=10_000, cc=90_000, ts=T0 + gap + 2, split=False),
    ]


def test_chain_without_any_split_has_unknown_tier_and_no_avoidable_gap() -> None:
    res = _detect(_unsplit_pair(1_800.0))
    (e,) = res.events
    assert e.cause == cc.CAUSE_UNEXPLAINED and e.avoidability == cc.UNKNOWN
    assert e.ttl_s is None and e.note == cc.UNKNOWN_TIER_NOTE
    assert res.write_5m_tokens == 0 and res.premium_1h_usd == 0.0
    assert res.unsplit_write_tokens == 91_000


def test_unknown_tier_short_gap_has_no_note() -> None:
    (e,) = _detect(_unsplit_pair(100.0)).events
    assert e.cause == cc.CAUSE_UNEXPLAINED and e.ttl_s is None and e.note == ""


def test_unsplit_chain_gives_no_ttl_advice_and_reports_tokens(tmp_path: Path) -> None:
    p = tmp_path / "proj" / "s.jsonl"
    _write(p, _unsplit_pair(1_800.0))
    s = cc.analyze_session(p)
    assert s.advice == [] and s.avoidable_usd == 0.0
    assert s.unsplit_write_tokens == 91_000


def test_mixed_chain_takes_tier_from_turns_that_have_the_split() -> None:
    recs = [
        user_rec(T0),
        asst_rec("a", SONNET, cr=0, cc=50_000, cc_1h=50_000, ts=T0 + 1),  # split: 1h
        user_rec(T0 + 30),
        asst_rec("b", SONNET, cr=50_000, cc=49_000, ts=T0 + 31, split=False),  # unsplit
        user_rec(T0 + 60),
        asst_rec("c", SONNET, cr=99_000, cc=1_000, ts=T0 + 61, split=False),
        user_rec(T0 + 60 + 1_800),
        asst_rec("d", SONNET, cr=2_000, cc=98_000, ts=T0 + 1_862, split=False),
    ]
    res = _detect(recs)
    (e,) = res.events
    # the tier is 1h (from turn a, the only turn with a split), so 1,800 s is inside the lifetime
    assert e.ttl_s == 3_600.0 and e.cause == cc.CAUSE_UNEXPLAINED and e.note == ""
    assert res.write_5m_tokens == 0 and res.unsplit_write_tokens == 49_000 + 1_000 + 98_000


def test_mixed_chain_five_minute_split_still_makes_a_gap_avoidable() -> None:
    recs = _unsplit_pair(1_800.0, split_first=True)
    (e,) = _detect(recs).events
    assert e.ttl_s == 300.0 and e.cause == cc.CAUSE_TTL_GAP and e.avoidability == cc.AVOIDABLE


def test_empty_split_dict_is_not_a_split() -> None:
    rec = asst_rec("a", SONNET, cr=0, cc=10, ts=T0)
    rec["message"]["usage"]["cache_creation"] = {}
    (t,) = _chain([rec]).turns
    assert t.has_split is False and t.unsplit_cc == 10
