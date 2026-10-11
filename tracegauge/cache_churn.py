from __future__ import annotations

"""tracegauge/cache_churn.py -- prompt-cache churn: cache the model had to re-write instead of read.

A *chain* is one prompt-cache lineage: the main transcript, or ONE subagent transcript (a
subagent's first request does not read its parent's cache, so chains are never mixed). Inside a
chain, turn ``t`` is a churn *event* when the prefix that was cached after turn ``t-1`` was NOT
read back and a large share of the prompt was written again (definition and thresholds below).

Only three causes are reported as causes, each decided from record markers in the transcript:

* ``COMPACTION``   a compaction marker lies between the two turns.
* ``MODEL_SWITCH`` the model changed (each model has its own cache).
* ``TTL_GAP``      the idle gap between the two requests exceeded the cache TTL in force.

Everything else is ``UNEXPLAINED``: counted and priced, never given a cause. There is no
cause-labelled ground truth for this data, so the report says what the usage fields measure (a
read collapse plus a re-write, in tokens and dollars) and labels the cause as a rule applied to
markers and timestamps.

Layers (all pure except ``read_chain_records`` / ``analyze_session``):

* ``detect_chain``       list of normalised ``CacheTurn`` -> events. No file or network access, so
                         it can later be run on turn records adapted from other agents' sessions.
* ``read_chain_records`` raw transcript records -> ``ChainInput`` (dedupe by message.id).
* ``analyze_session``    main transcript + its ``subagents/`` transcripts -> ``SessionChurn``,
                         computed on demand from the source JSONL (nothing is stored).
"""

import bisect
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tracegauge._digest import TurnDigest
from tracegauge.cost import compute_turn_cost, load_price_table
from tracegauge.discovery import SUBAGENTS_DIRNAME

# ---------------------------------------------------------------------------
# Event definition. The values come from a measurement over 96,164 de-duplicated turns of one
# user's 81 sessions (the maintainer's unpublished local measurement): the ratio
# cr_t / C_{t-1} was bimodal, 93,975 of 95,136 eligible pairs at >= 1.0 and 1,086 at < 0.3, none
# between 0.3 and 0.9, so any R in that gap selects the same events. Event counts moved -2.1% /
# +0.2% when all three thresholds were made stricter / looser together.
# ---------------------------------------------------------------------------

#: C_{t-1} (cache_read + cache_creation of the previous turn) must be at least this for a prefix
#: to be worth preserving. Below it a re-write costs cents and the signal is noise.
MIN_PREFIX_TOKENS = 5000
#: The read dropped: cr_t < R * cr_{t-1}. The data is bimodal (see above), 0.5 is mid-gap.
READ_DROP_RATIO = 0.5
#: A large share of this prompt was re-written: cc_t > F * P_t, P_t = input + cc + cr. Rows with big
#: appended content but intact reads (a large file read) fail the read-drop test and are not events.
REWRITE_SHARE = 0.20

# TTLs the API offers (https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
TTL_5M_S = 300.0
TTL_1H_S = 3600.0
#: Seconds added to the TTL before a gap counts as "over the TTL". 0 = the documented boundary.
#: Transcript timestamps are record write times, not request arrival times, so the 5-minute edge
#: is blurred over roughly 300-360 s on real data; a grace of 30 s would move the events with a gap
#: in (300, 330] from TTL_GAP to UNEXPLAINED (the sensitivity is reported, see docs/CACHE_CHURN.md).
TTL_GRACE_S = 0.0
#: The tier in force is inferred from the most recent turn that wrote cache: 1 hour when at least
#: this share of its writes were 1-hour writes, else 5 minutes.
ONE_HOUR_TIER_SHARE = 0.5

#: A TTL gap above this is "idle for more than an hour": not avoidable by a longer TTL.
AVOIDABLE_MAX_GAP_S = TTL_1H_S

CAUSE_COMPACTION = "COMPACTION"
CAUSE_MODEL_SWITCH = "MODEL_SWITCH"
CAUSE_TTL_GAP = "TTL_GAP"
CAUSE_UNEXPLAINED = "UNEXPLAINED"
#: Shipped causes in rule-priority order (first match wins); UNEXPLAINED is the residual.
CAUSES = (CAUSE_COMPACTION, CAUSE_MODEL_SWITCH, CAUSE_TTL_GAP)
ALL_BUCKETS = (*CAUSES, CAUSE_UNEXPLAINED)

AVOIDABLE = "plausibly_avoidable"
UNAVOIDABLE = "unavoidable"
UNKNOWN = "unknown"

KIND_MAIN = "main"
KIND_SUBAGENT = "subagent"

#: Marker kinds that mean "the conversation was compacted here".
MARK_COMPACT_BOUNDARY = "compact_boundary"
MARK_COMPACT_SUMMARY = "compact_summary"

#: How many events a report carries in full (the rest are only counted and summed).
MAX_EVENTS_LISTED = 10

UNKNOWN_TIER_NOTE = (
    "The gap exceeds the 5-minute lifetime, but no turn of this chain records the 5-minute / "
    "1-hour split of its cache writes, so the cache lifetime is unknown; the gap is not called a "
    "TTL gap and is not counted as avoidable."
)

JUDGEMENT_NOTE = (
    "Avoidable vs unavoidable is a judgement, not a measurement: 'plausibly avoidable' is a "
    "TTL gap of 5 minutes to 1 hour on a 5-minute-TTL cache, or a model switch; idle over an hour "
    "and compaction are treated as unavoidable. The cause is a rule applied to markers and "
    "timestamps and has not been validated in a controlled run."
)

# Public setting names, from the saved Claude Code prompt-caching page (checked offline):
# main conversation = promptCacheTtl / CLAUDE_CODE_PROMPT_CACHE_TTL; everything else (subagents)
# = subagentPromptCacheTtl / CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL. Both need Claude Code 2.1.242+.
SETTING_BY_KIND: dict[str, tuple[str, str]] = {
    KIND_MAIN: ("promptCacheTtl", "CLAUDE_CODE_PROMPT_CACHE_TTL"),
    KIND_SUBAGENT: ("subagentPromptCacheTtl", "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"),
}


@dataclass(frozen=True)
class ChurnParams:
    """The detector's tunable constants (defaults are the module constants above)."""

    min_prefix_tokens: int = MIN_PREFIX_TOKENS
    read_drop_ratio: float = READ_DROP_RATIO
    rewrite_share: float = REWRITE_SHARE
    ttl_grace_s: float = TTL_GRACE_S


DEFAULT_PARAMS = ChurnParams()


# ---------------------------------------------------------------------------
# Normalised records (the pure core's input)
# ---------------------------------------------------------------------------


@dataclass
class CacheTurn:
    """One API response (distinct message.id) of a chain, with the usage that billed it.

    ``i_first`` / ``i_last`` are record positions in the transcript (any monotone numbering);
    ``trig_ts`` / ``trig_i`` are the timestamp and position of the last user record seen before
    the first record of this turn (the request's trigger). All times are epoch seconds or None.
    """

    mid: str
    model: str
    inp: int
    cc: int
    cr: int
    cc_1h: int
    i_first: int
    i_last: int
    ts_first: float | None = None
    ts_last: float | None = None
    trig_ts: float | None = None
    trig_i: int | None = None
    speed: str = ""
    geo: str = ""
    #: False when no record of this turn carried the ``usage.cache_creation`` 5m/1h split: the
    #: tier of its writes is then UNKNOWN (not assumed to be 5 minutes).
    has_split: bool = True

    @property
    def unsplit_cc(self) -> int:
        """Cache-write tokens whose tier the transcript does not say (0 when the split exists)."""
        return 0 if self.has_split else self.cc

    @property
    def prompt(self) -> int:
        """Prompt size P = fresh input + cache writes + cache reads."""
        return self.inp + self.cc + self.cr


@dataclass
class ChainInput:
    """One cache lineage: normalised turns plus the compaction markers in record order."""

    kind: str
    label: str
    turns: list[CacheTurn]
    #: (record position, MARK_*) of compaction markers, ascending by position.
    compact_marks: list[tuple[int, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pricing (tracegauge.cost is the single source of price arithmetic)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnRates:
    """USD per token for one turn: cache write at 5 minutes / 1 hour and cache read."""

    w5: float
    w1: float
    rd: float


class RateTable:
    """Per-token cache rates from the price table, via ``tracegauge.cost.compute_turn_cost``.

    Each rate comes from pricing a one-token probe turn, so the per-model cache-read
    multiplier, the long-context tier (chosen by the turn's prompt size), fast mode and
    US-only inference are honoured exactly as ``tes cost`` honours them. A model that does not
    resolve against the table is *unpriced* (``rates`` returns None), never priced at a guess.
    """

    def __init__(self, prices: dict[str, Any] | None = None) -> None:
        self._prices = prices if prices is not None else load_price_table()
        self._memo: dict[tuple[str, bool, str, str], TurnRates | None] = {}

    def _long_threshold(self, model: str) -> int | None:
        from tracegauge.cost import _resolve_model  # noqa: PLC0415

        key, _, _ = _resolve_model(model, self._prices)
        if key is None:
            return None
        tier = self._prices["models"][key].get("long_context")
        if isinstance(tier, dict):
            return int(tier["threshold_prompt_tokens"])
        return None

    def rates(self, turn: CacheTurn) -> TurnRates | None:
        """Rates for ``turn``'s model and prompt size, or None when the model is unpriced."""
        thr = self._long_threshold(turn.model)
        long_tier = thr is not None and turn.prompt > thr
        memo_key = (turn.model, long_tier, turn.speed, turn.geo)
        if memo_key in self._memo:
            return self._memo[memo_key]
        out: TurnRates | None = None
        if turn.model.strip():
            costs = [
                compute_turn_cost(
                    TurnDigest(
                        turn_index=0,
                        role="ai",
                        tool_names=[],
                        content_snippet="",
                        token_count_input=turn.prompt,
                        token_count_output=0,
                        cache_read=1,
                        h2_duplicate=False,
                        cache_creation=1,
                        model=turn.model,
                        cache_creation_1h=one_h,
                        cache_creation_tier_known=1,
                        speed=turn.speed,
                        inference_geo=turn.geo,
                    ),
                    self._prices,
                )
                for one_h in (0, 1)
            ]
            if all(c.priced for c in costs):
                out = TurnRates(
                    w5=costs[0].cache_creation_cost,
                    w1=costs[1].cache_creation_cost,
                    rd=costs[0].cache_read_cost,
                )
        self._memo[memo_key] = out
        return out


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass
class ProofTurn:
    """The numbers of one of the two turns that prove an event (no transcript text)."""

    turn_index: int
    record_index: int
    message_id: str
    model: str
    cache_read: int
    cache_creation: int
    cache_creation_1h: int


@dataclass
class ChurnEvent:
    """One cache-churn event with its proof turns (k-1, k), tokens and dollars."""

    chain_kind: str
    chain_label: str
    cause: str
    prev: ProofTurn
    cur: ProofTurn
    gap_s: float | None
    #: None = the cache tier could not be inferred (no turn of the chain has the 5m/1h split).
    ttl_s: float | None
    marker_index: int | None
    churn_tokens: int
    #: None = the turn's model is not in the price table (unpriced), never 0.0.
    churn_usd: float | None
    avoidability: str
    #: Why the event is what it is when that needs saying (empty otherwise).
    note: str = ""


@dataclass
class ChainResult:
    """Events of one chain plus what a longer TTL would add to its cache-write bill."""

    kind: str
    label: str
    n_turns: int
    events: list[ChurnEvent]
    #: tokens written at the 5-minute tier across the chain (writes of unspecified tier are NOT
    #: included: they are in ``unsplit_write_tokens``).
    write_5m_tokens: int
    #: USD to reprice those writes at the 1-hour rate; None when any such turn is unpriced.
    premium_1h_usd: float | None
    unpriced_write_turns: int
    #: cache-write tokens of turns without the 5m/1h split (tier unknown), excluded from the
    #: 5-minute premium and from the TTL inference.
    unsplit_write_tokens: int = 0


def _tier_ttl(one_hour: bool | None) -> float | None:
    """TTL of a tier; None when the tier is unknown."""
    if one_hour is None:
        return None
    return TTL_1H_S if one_hour else TTL_5M_S


def _writes_one_hour(t: CacheTurn) -> bool:
    return t.cc_1h >= ONE_HOUR_TIER_SHARE * t.cc


def _proof(k: int, t: CacheTurn) -> ProofTurn:
    return ProofTurn(k, t.i_first, t.mid, t.model, t.cr, t.cc, t.cc_1h)


def _starts(turns: list[CacheTurn]) -> list[float | None]:
    """Request start per turn: the trigger (user/tool_result) timestamp, else the record's own.

    Start-to-start gaps are what the TTL clock measures (a request refreshes the entry when it
    arrives). The trigger is only used when it lies after the previous turn's first record, so a
    stale user record is never reused for two turns.
    """
    out: list[float | None] = []
    for k, t in enumerate(turns):
        use_trig = t.trig_ts is not None and (
            k == 0 or (t.trig_i is not None and t.trig_i > turns[k - 1].i_first)
        )
        out.append(t.trig_ts if use_trig else t.ts_first)
    return out


def _classify_avoidability(cause: str, gap_s: float | None, ttl_s: float | None) -> str:
    if cause == CAUSE_MODEL_SWITCH:
        return AVOIDABLE
    if cause == CAUSE_COMPACTION:
        return UNAVOIDABLE
    if cause == CAUSE_TTL_GAP and gap_s is not None and ttl_s is not None:
        if gap_s > AVOIDABLE_MAX_GAP_S:
            return UNAVOIDABLE
        if ttl_s <= TTL_5M_S:
            return AVOIDABLE
        # Unreachable with a 3600 s TTL and no negative grace (a gap over the 1-hour TTL is over
        # AVOIDABLE_MAX_GAP_S), kept so a changed constant fails safe rather than overclaims.
        return UNKNOWN
    return UNKNOWN


def detect_chain(
    chain: ChainInput,
    rates: RateTable,
    params: ChurnParams = DEFAULT_PARAMS,
) -> ChainResult:
    """Find the churn events of one chain. Pure: no I/O, deterministic.

    Event(t), t >= 1, iff all hold: ``C_{t-1} >= min_prefix_tokens`` (C = cr + cc);
    ``cr_{t-1} > 0`` and ``cr_t < read_drop_ratio * cr_{t-1}``; ``cc_t > rewrite_share * P_t``.
    Turn 0 has no predecessor in its own lineage and is never an event. Churn tokens are
    ``min(cc_t, max(0, C_{t-1} - cr_t))`` (growth of the prefix is excluded by construction) and
    churn dollars are those tokens times (the turn's blended write rate - the model's read rate).
    """
    turns = chain.turns
    mark_pos = [m[0] for m in chain.compact_marks]
    starts = _starts(turns)
    events: list[ChurnEvent] = []
    # Tier in force: the most recent earlier turn that wrote cache AND has the 5m/1h split; when
    # none exists yet, the first later such turn of the chain; None when the chain has no split.
    split_writers = [t for t in turns if t.cc > 0 and t.has_split]
    first_split_tier = _writes_one_hour(split_writers[0]) if split_writers else None
    last_1h: bool | None = first_split_tier
    w5_tokens = 0
    unsplit_tokens = 0
    premium = 0.0
    unpriced_write_turns = 0

    for k, t in enumerate(turns):
        unsplit_tokens += t.unsplit_cc
        c5 = (t.cc - t.cc_1h) if t.has_split else 0
        if c5 > 0:
            w5_tokens += c5
            r = rates.rates(t)
            if r is None:
                unpriced_write_turns += 1
            else:
                premium += c5 * (r.w1 - r.w5)
        if k > 0:
            p = turns[k - 1]
            prev_prefix = p.cr + p.cc
            prompt = t.prompt
            is_event = (
                prev_prefix >= params.min_prefix_tokens
                and prompt > 0
                and p.cr > 0
                and t.cr < params.read_drop_ratio * p.cr
                and t.cc > params.rewrite_share * prompt
            )
            if is_event:
                events.append(
                    _make_event(
                        chain, k, p, t, starts, mark_pos, last_1h, prev_prefix, rates, params
                    )
                )
        if t.cc > 0 and t.has_split:
            last_1h = _writes_one_hour(t)

    return ChainResult(
        kind=chain.kind,
        label=chain.label,
        n_turns=len(turns),
        events=events,
        write_5m_tokens=w5_tokens,
        premium_1h_usd=None if unpriced_write_turns else premium,
        unpriced_write_turns=unpriced_write_turns,
        unsplit_write_tokens=unsplit_tokens,
    )


def _make_event(
    chain: ChainInput,
    k: int,
    p: CacheTurn,
    t: CacheTurn,
    starts: list[float | None],
    mark_pos: list[int],
    last_1h: bool | None,
    prev_prefix: int,
    rates: RateTable,
    params: ChurnParams,
) -> ChurnEvent:
    lo = bisect.bisect_right(mark_pos, p.i_last)
    hi = bisect.bisect_left(mark_pos, t.i_first)
    marker = mark_pos[lo] if lo < hi else None
    s_t, s_p = starts[k], starts[k - 1]
    gap = (s_t - s_p) if (s_t is not None and s_p is not None) else None
    ttl = _tier_ttl(last_1h)
    note = ""

    if marker is not None:
        cause = CAUSE_COMPACTION
    elif t.model != p.model:
        cause = CAUSE_MODEL_SWITCH
    elif ttl is None:
        cause = CAUSE_UNEXPLAINED
        if gap is not None and gap > TTL_5M_S + params.ttl_grace_s:
            note = UNKNOWN_TIER_NOTE
    elif gap is not None and gap > ttl + params.ttl_grace_s:
        cause = CAUSE_TTL_GAP
    else:
        cause = CAUSE_UNEXPLAINED

    churn_tokens = min(t.cc, max(0, prev_prefix - t.cr))
    r = rates.rates(t)
    usd: float | None = None
    if r is not None:
        blended = ((t.cc - t.cc_1h) * r.w5 + t.cc_1h * r.w1) / t.cc if t.cc else r.w5
        usd = churn_tokens * max(0.0, blended - r.rd)
    return ChurnEvent(
        chain_kind=chain.kind,
        chain_label=chain.label,
        cause=cause,
        prev=_proof(k - 1, p),
        cur=_proof(k, t),
        gap_s=gap,
        ttl_s=ttl,
        marker_index=marker,
        churn_tokens=churn_tokens,
        churn_usd=usd,
        avoidability=_classify_avoidability(cause, gap, ttl),
        note=note,
    )


# ---------------------------------------------------------------------------
# Raw transcript records -> ChainInput
# ---------------------------------------------------------------------------


def _int(value: Any) -> int:
    """A non-negative int from a usage field; anything unusable is 0."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, int(value))


def parse_ts(value: Any) -> float | None:
    """ISO-8601 timestamp -> epoch seconds, or None when absent or malformed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def read_chain_records(
    records: Iterable[dict[str, Any]],
    *,
    kind: str,
    label: str,
    skip_sidechain: bool = False,
) -> ChainInput:
    """Normalise one transcript's records into a chain.

    Assistant records sharing a ``message.id`` are one API response (per-field maximum over
    them, as ``tracegauge.adapt`` does); ``<synthetic>`` records and records without a usage object are
    not billed requests and are ignored. A main transcript passes ``skip_sidechain=True``
    (sidechain records belong to subagents, as in ``tracegauge.adapt``). Compaction markers are
    ``system`` records with subtype ``compact_boundary`` and ``user`` records flagged
    ``isCompactSummary``.
    """
    groups: dict[str, CacheTurn] = {}
    order: list[CacheTurn] = []
    marks: list[tuple[int, str]] = []
    last_user_ts: float | None = None
    last_user_i: int | None = None
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        rtype = rec.get("type")
        if skip_sidechain and rec.get("isSidechain"):
            continue
        if rtype == "assistant":
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            model = msg.get("model")
            usage = msg.get("usage")
            if model == "<synthetic>" or not isinstance(usage, dict):
                continue
            split = usage.get("cache_creation")
            split = split if isinstance(split, dict) else {}
            has_split = any(
                isinstance(split.get(key), int | float) and not isinstance(split.get(key), bool)
                for key in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
            )
            ts = parse_ts(rec.get("timestamp"))
            key = str(msg.get("id") or rec.get("requestId") or rec.get("uuid") or f"@{i}")
            cc = _int(usage.get("cache_creation_input_tokens"))
            cur = groups.get(key)
            if cur is None:
                cur = CacheTurn(
                    mid=key,
                    model=str(model or ""),
                    inp=0,
                    cc=0,
                    cr=0,
                    cc_1h=0,
                    i_first=i,
                    i_last=i,
                    ts_first=ts,
                    ts_last=ts,
                    trig_ts=last_user_ts,
                    trig_i=last_user_i,
                    has_split=False,
                )
                groups[key] = cur
                order.append(cur)
            cur.inp = max(cur.inp, _int(usage.get("input_tokens")))
            cur.cc = max(cur.cc, cc)
            cur.cr = max(cur.cr, _int(usage.get("cache_read_input_tokens")))
            cur.cc_1h = max(cur.cc_1h, _int(split.get("ephemeral_1h_input_tokens")))
            cur.has_split = cur.has_split or has_split
            cur.i_last = i
            cur.ts_last = ts if ts is not None else cur.ts_last
            if usage.get("speed") == "fast":
                cur.speed = "fast"
            if usage.get("inference_geo") == "us":
                cur.geo = "us"
        elif rtype == "user":
            ts = parse_ts(rec.get("timestamp"))
            last_user_ts = ts if ts is not None else last_user_ts
            last_user_i = i
            if rec.get("isCompactSummary"):
                marks.append((i, MARK_COMPACT_SUMMARY))
        elif rtype == "system" and rec.get("subtype") == "compact_boundary":
            marks.append((i, MARK_COMPACT_BOUNDARY))
    for t in order:
        t.cc_1h = min(t.cc_1h, t.cc)
    return ChainInput(kind=kind, label=label, turns=order, compact_marks=marks)


def drop_seen_turns(chain: ChainInput, seen: set[str]) -> int:
    """Remove turns whose message.id is already in ``seen`` (claiming new ids); returns removed.

    The same API response can be written to two transcript files (a resumed session copies
    history); counting it in one chain keeps the session's churn from being counted twice.
    """
    kept: list[CacheTurn] = []
    removed = 0
    for t in chain.turns:
        if t.mid in seen:
            removed += 1
            continue
        seen.add(t.mid)
        kept.append(t)
    chain.turns = kept
    return removed


# A record the detector can use is one of these three types; every other line (attachments,
# snapshots, titles, ...) is skipped without being parsed. The match cannot miss a wanted record:
# its own "type" key appears unescaped in the line, whatever the spacing. A false positive (the
# text inside a longer line) only costs a parse, and the type is checked again after parsing.
_WANTED_TYPE = re.compile(r'"type"\s*:\s*"(?:assistant|user|system)"')


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Parsed assistant/user/system object lines of a JSONL file; blank or malformed lines (a
    live file may end mid-write) are skipped. Positions are only used for ordering."""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not _WANTED_TYPE.search(line):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


# ---------------------------------------------------------------------------
# Session level
# ---------------------------------------------------------------------------


@dataclass
class CauseTotals:
    """Events, tokens and dollars of one bucket."""

    events: int = 0
    churn_tokens: int = 0
    churn_usd: float = 0.0
    unpriced_events: int = 0


@dataclass
class TtlAdvice:
    """A priced counterfactual for one TTL control; only built when its net saving is > 0."""

    scope: str
    setting: str
    env_var: str
    value: str
    premium_usd: float
    saved_usd: float
    net_usd: float
    events_avoided: int
    chains: int
    assumptions: str


@dataclass
class SessionChurn:
    """Cache churn of one session: main + subagent chains, on demand from the source JSONL."""

    chains: list[ChainResult]
    by_bucket: dict[str, CauseTotals]
    by_kind: dict[str, CauseTotals]
    avoidable_usd: float
    unavoidable_usd: float
    advice: list[TtlAdvice]
    duplicate_turns_dropped: int = 0

    @property
    def unsplit_write_tokens(self) -> int:
        """Cache-write tokens, over all chains, whose 5m/1h tier the transcript does not say."""
        return sum(c.unsplit_write_tokens for c in self.chains)

    @property
    def events(self) -> list[ChurnEvent]:
        return [e for c in self.chains for e in c.events]

    @property
    def total(self) -> CauseTotals:
        out = CauseTotals()
        for b in self.by_bucket.values():
            out.events += b.events
            out.churn_tokens += b.churn_tokens
            out.churn_usd += b.churn_usd
            out.unpriced_events += b.unpriced_events
        return out


_ADVICE_ASSUMPTIONS = (
    "Reprices every 5-minute cache write of the {scope} chain at the 1-hour rate (2.0x input "
    "instead of 1.25x) and assumes each 5-minute-tier TTL gap of 300 s to 1 h in these chains "
    "would then have been a cache read instead of a re-write. Gaps over 1 h, model switches "
    "and compactions are not counted as saved. Second-order effects (other requests refreshing "
    "an entry, sibling subagents sharing it) are not modelled."
)


def _advice_for(kind: str, chains: list[ChainResult]) -> TtlAdvice | None:
    scoped = [c for c in chains if c.kind == kind]
    if not scoped or any(c.premium_1h_usd is None for c in scoped):
        return None  # an unpriced write makes the premium a floor: no net can be claimed
    premium = sum(c.premium_1h_usd or 0.0 for c in scoped)
    saved = 0.0
    avoided = 0
    for c in scoped:
        for e in c.events:
            if (
                e.cause == CAUSE_TTL_GAP
                and e.avoidability == AVOIDABLE
                and e.ttl_s is not None
                and e.ttl_s <= TTL_5M_S
                and e.churn_usd is not None
            ):
                saved += e.churn_usd
                avoided += 1
            elif e.cause == CAUSE_TTL_GAP and e.churn_usd is None:
                return None
    net = saved - premium
    if net <= 0:
        return None
    setting, env = SETTING_BY_KIND[kind]
    return TtlAdvice(
        scope=kind,
        setting=setting,
        env_var=env,
        value="1h",
        premium_usd=premium,
        saved_usd=saved,
        net_usd=net,
        events_avoided=avoided,
        chains=len(scoped),
        assumptions=_ADVICE_ASSUMPTIONS.format(scope=kind),
    )


def summarise(
    chains: list[ChainResult],
    duplicate_turns_dropped: int = 0,
) -> SessionChurn:
    """Aggregate chain results: totals per bucket and per scope, avoidable split, TTL advice."""
    by_bucket = {b: CauseTotals() for b in ALL_BUCKETS}
    by_kind = {KIND_MAIN: CauseTotals(), KIND_SUBAGENT: CauseTotals()}
    avoidable = unavoidable = 0.0
    for c in chains:
        for e in c.events:
            for tot in (by_bucket[e.cause], by_kind.setdefault(c.kind, CauseTotals())):
                tot.events += 1
                tot.churn_tokens += e.churn_tokens
                if e.churn_usd is None:
                    tot.unpriced_events += 1
                else:
                    tot.churn_usd += e.churn_usd
            if e.churn_usd is not None:
                if e.avoidability == AVOIDABLE:
                    avoidable += e.churn_usd
                elif e.avoidability == UNAVOIDABLE:
                    unavoidable += e.churn_usd
    advice = [a for a in (_advice_for(k, chains) for k in (KIND_SUBAGENT, KIND_MAIN)) if a]
    return SessionChurn(
        chains=chains,
        by_bucket=by_bucket,
        by_kind=by_kind,
        avoidable_usd=avoidable,
        unavoidable_usd=unavoidable,
        advice=advice,
        duplicate_turns_dropped=duplicate_turns_dropped,
    )


def subagent_files(session_path: Path) -> list[Path]:
    """The session's subagent transcripts (``<sid>/subagents/agent-*.jsonl``), sorted.

    Flat on purpose, exactly like ``tracegauge.adapt.collect_subagent_usage``, so the churn scope and
    the cost scope are the same set of files (a nested file is in neither).
    """
    sub_dir = session_path.with_suffix("") / SUBAGENTS_DIRNAME
    if not sub_dir.is_dir():
        return []
    return sorted(sub_dir.glob("agent-*.jsonl"))


def analyze_session(
    session_path: Path,
    prices: dict[str, Any] | None = None,
    params: ChurnParams = DEFAULT_PARAMS,
) -> SessionChurn:
    """Cache churn of a session, computed on demand from its source JSONL (nothing stored).

    The main transcript and every subagent transcript are separate chains. A message.id that
    appears in more than one file of the session is counted once (the main file claims it first).
    Raises OSError if the main transcript cannot be read; an unreadable subagent file is skipped.
    """
    table = RateTable(prices)
    chains = [
        read_chain_records(
            _iter_jsonl(session_path), kind=KIND_MAIN, label="main", skip_sidechain=True
        )
    ]
    for f in subagent_files(session_path):
        try:
            chains.append(read_chain_records(_iter_jsonl(f), kind=KIND_SUBAGENT, label=f.stem))
        except OSError:
            continue
    seen: set[str] = set()
    dropped = sum(drop_seen_turns(c, seen) for c in chains)
    return summarise([detect_chain(c, table, params) for c in chains], dropped)


__all__ = [
    "ALL_BUCKETS",
    "AVOIDABLE",
    "CAUSES",
    "CacheTurn",
    "ChainInput",
    "ChainResult",
    "ChurnEvent",
    "ChurnParams",
    "RateTable",
    "SessionChurn",
    "TtlAdvice",
    "UNAVOIDABLE",
    "analyze_session",
    "detect_chain",
    "read_chain_records",
    "summarise",
]
