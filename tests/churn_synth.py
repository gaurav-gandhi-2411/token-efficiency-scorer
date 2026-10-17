# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Deterministic synthetic transcripts for the cache-churn tests (no real session content).

``clean_chain`` builds a chain whose cache is always read back (no event); ``inject`` plants
known TTL gaps, model switches and compactions at given positions. Everything is numbers and
ids, generated from a seeded ``random.Random``.
"""

import random
from typing import Any

T0 = 1_760_000_000.0  # fixed epoch origin so timestamps are reproducible
MODEL_A = "claude-sonnet-5-5"
MODEL_B = "claude-opus-5-5"


def iso(epoch: float) -> str:
    """Epoch seconds -> the ISO-8601 'Z' form Claude Code writes."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def user_rec(ts: float | None) -> dict[str, Any]:
    rec: dict[str, Any] = {"type": "user", "message": {"role": "user", "content": "x"}}
    if ts is not None:
        rec["timestamp"] = iso(ts)
    return rec


def asst_rec(
    mid: str,
    model: str,
    *,
    cr: int,
    cc: int,
    cc_1h: int = 0,
    inp: int = 3,
    ts: float | None = None,
    split: bool = True,
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "input_tokens": inp,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
        "output_tokens": 50,
    }
    if split:
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": cc - cc_1h,
            "ephemeral_1h_input_tokens": cc_1h,
        }
    rec: dict[str, Any] = {
        "type": "assistant",
        "message": {"id": mid, "role": "assistant", "model": model, "content": [], "usage": usage},
    }
    if ts is not None:
        rec["timestamp"] = iso(ts)
    return rec


def compact_boundary(ts: float | None = None) -> dict[str, Any]:
    rec: dict[str, Any] = {"type": "system", "subtype": "compact_boundary"}
    if ts is not None:
        rec["timestamp"] = iso(ts)
    return rec


def summary_rec(ts: float | None = None) -> dict[str, Any]:
    rec = user_rec(ts)
    rec["isCompactSummary"] = True
    return rec


def build_chain(
    rng: random.Random,
    n_turns: int,
    injections: dict[int, str],
    *,
    one_hour: bool = False,
    prefix: str = "m",
) -> list[dict[str, Any]]:
    """Records of a chain of ``n_turns`` turns; ``injections`` maps turn index -> cause.

    Causes: ``TTL_GAP`` (idle gap just past the tier's TTL, cache read collapses to a small
    constant prefix), ``MODEL_SWITCH`` (other model, short gap), ``COMPACTION`` (compaction
    marker, prefix shrinks). All other turns read the whole previous prefix back within the TTL.
    """
    recs: list[dict[str, Any]] = []
    t = T0
    model = MODEL_A
    prefix_tokens = rng.randint(20_000, 60_000)  # C_{t-1}
    system_prefix = 9_000
    for k in range(n_turns):
        cause = injections.get(k)
        gap = float(rng.randint(5, 120))
        cur_model = model
        if cause == "TTL_GAP":
            gap = float(rng.randint(3_700, 20_000) if one_hour else rng.randint(400, 3_000))
        elif cause == "MODEL_SWITCH":
            cur_model = MODEL_B if model == MODEL_A else MODEL_A
        t += gap
        recs.append(user_rec(t))
        if cause == "COMPACTION":
            recs.append(compact_boundary(t))
            recs.append(summary_rec(t))
        growth = rng.randint(200, 900)
        if k == 0:
            cr, cc = 0, prefix_tokens
        elif cause == "TTL_GAP":
            cr, cc = system_prefix, prefix_tokens - system_prefix + growth
        elif cause == "MODEL_SWITCH":
            cr, cc = 4_000, prefix_tokens - 4_000 + growth
        elif cause == "COMPACTION":
            new_prefix = rng.randint(40_000, 60_000)
            cr, cc = 6_000, new_prefix - 6_000 + growth
            prefix_tokens = new_prefix
        else:
            cr, cc = prefix_tokens, growth
        cc_1h = cc if one_hour else 0
        recs.append(asst_rec(f"{prefix}{k}", cur_model, cr=cr, cc=cc, cc_1h=cc_1h, ts=t + 2.0))
        model = cur_model
        prefix_tokens = cr + cc
    return recs


def clean_chain(
    rng: random.Random, n_turns: int, *, one_hour: bool = False, prefix: str = "m"
) -> list[dict[str, Any]]:
    """A chain with no event at all."""
    return build_chain(rng, n_turns, {}, one_hour=one_hour, prefix=prefix)


#: Modes of ``build_hard_chain``: borderline cases the clear-cut generator above never makes.
HARD_SMEAR_GAP = "smear_gap"  # 5m chain, gap in the 300-360 s zone where timestamps blur the edge
HARD_READ_BELOW = "read_below"  # read falls to 0.40-0.499 of the previous read (R = 0.5)
HARD_READ_ABOVE = "read_above"  # read falls to 0.50-0.60 of the previous read
HARD_MODES = (HARD_SMEAR_GAP, HARD_READ_BELOW, HARD_READ_ABOVE)


def build_hard_chain(
    rng: random.Random, n_turns: int, positions: list[int], mode: str, *, prefix: str = "h"
) -> list[dict[str, Any]]:
    """Records of a 5-minute-tier chain with a borderline partial cache loss at ``positions``.

    Unlike ``build_chain`` these are NOT clear-cut: ``HARD_SMEAR_GAP`` loses the cache after an
    idle gap drawn from 300-360 s (the zone where record-write timestamps blur the TTL edge);
    ``HARD_READ_BELOW`` / ``HARD_READ_ABOVE`` keep a fraction of the previous read just below /
    above the detector's read-drop ratio, with the re-written remainder large enough to pass the
    rewrite-share test. The constants are written out here (0.5 etc.), not imported from the
    detector, so the cases do not move when a threshold is tuned.
    """
    recs: list[dict[str, Any]] = []
    t = T0
    prefix_tokens = rng.randint(20_000, 60_000)
    prev_cr = 0
    for k in range(n_turns):
        injected = k in positions
        gap = float(rng.randint(5, 120))
        if injected and mode == HARD_SMEAR_GAP:
            gap = rng.uniform(300.0, 360.0)
        t += gap
        recs.append(user_rec(t))
        growth = rng.randint(200, 900)
        if k == 0:
            cr, cc = 0, prefix_tokens
        elif injected and mode == HARD_SMEAR_GAP:
            cr, cc = 9_000, prefix_tokens - 9_000 + growth
        elif injected:
            frac = rng.uniform(0.40, 0.499) if mode == HARD_READ_BELOW else rng.uniform(0.5, 0.6)
            cr = int(frac * prev_cr)
            cc = prefix_tokens - cr + growth
        else:
            cr, cc = prefix_tokens, growth
        recs.append(asst_rec(f"{prefix}{k}", MODEL_A, cr=cr, cc=cc, ts=t + 2.0))
        prefix_tokens = cr + cc
        prev_cr = cr
    return recs
