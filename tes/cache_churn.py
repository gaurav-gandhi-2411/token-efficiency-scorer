from __future__ import annotations

"""tes/cache_churn.py -- prompt-cache churn: cache the model had to re-write instead of read.

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

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tes._digest import TurnDigest
from tes.cost import compute_turn_cost, load_price_table

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
# Pricing (tes.cost is the single source of price arithmetic)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnRates:
    """USD per token for one turn: cache write at 5 minutes / 1 hour and cache read."""

    w5: float
    w1: float
    rd: float


class RateTable:
    """Per-token cache rates from the price table, via ``tes.cost.compute_turn_cost``.

    Each rate comes from pricing a one-token probe turn, so the per-model cache-read
    multiplier, the long-context tier (chosen by the turn's prompt size), fast mode and
    US-only inference are honoured exactly as ``tes cost`` honours them. A model that does not
    resolve against the table is *unpriced* (``rates`` returns None), never priced at a guess.
    """

    def __init__(self, prices: dict[str, Any] | None = None) -> None:
        self._prices = prices if prices is not None else load_price_table()
        self._memo: dict[tuple[str, bool, str, str], TurnRates | None] = {}

    def _long_threshold(self, model: str) -> int | None:
        from tes.cost import _resolve_model  # noqa: PLC0415

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
    them, as ``tes.adapt`` does); ``<synthetic>`` records and records without a usage object are
    not billed requests and are ignored. A main transcript passes ``skip_sidechain=True``
    (sidechain records belong to subagents, as in ``tes.adapt``). Compaction markers are
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
