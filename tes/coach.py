from __future__ import annotations

"""tes/coach.py — Habit coach: top FIXABLE habits ranked by MEASURED $ impact.

Design: research/13_coach_alarm_honesty_design.md (reviewed and approved).

Ships H1-H3 only. H4 (compaction-timing habit) is DEFERRED — no confirmed
signal for "when did the user run /compact" exists in CC's transcript format
(see the design doc's gap #2). Silence over a fabricated detection.

Every habit below is grounded in the user's OWN stored/computed data, states
its sample size (N), and always carries CAVEAT_SUFFIX. A habit whose supporting
pattern doesn't clear MIN_N_FOR_HABIT produces no entry at all — never an entry
with an unreliably small N.

H1 — high context re-send ratio (per task_type): computed on demand by
     re-adapting each session's source_path and running the frozen attribution
     function (same pattern as tes.store.backfill_cost/backfill_waste — the
     store doesn't persist the 6-bucket split per session, so this recomputes
     it read-only rather than mutating the schema for a first cut).
H2 — recurring waste events (RR/RFR): uses waste_event_count/waste_events
     already persisted — zero new computation.
H3 — sessions scored above the user's own baseline band: uses band_verdict
     already persisted, only fires once a REAL self-baseline is active
     (source == "self") for that task_type.
"""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from tes._digest import reconstruct_digest
from tes.adapt import adapt_session
from tes.attribution import compute_attribution
from tes.self_baseline import SelfBaselineState
from tes.waste import build_waste_entry

MIN_N_FOR_HABIT: int = 5
CAVEAT_SUFFIX: str = "(measured across your own sessions — not a guarantee for future sessions)"
DEFAULT_TOP_N: int = 3


@dataclass
class HabitResult:
    habit_id: str            # "H1" | "H2" | "H3"
    task_type: str | None    # None when not scoped to a single task_type
    message: str
    measured_n: int
    impact_usd: float        # ranking key — total measured $ this habit accounts for


def _fetch_sessions(conn: sqlite3.Connection, task_type: str | None) -> list[dict]:
    if task_type is not None:
        rows = conn.execute(
            "SELECT session_id, task_type, source_path, session_cost_usd, "
            "waste_event_count, waste_events, band_verdict "
            "FROM sessions WHERE task_type = ? AND session_cost_usd IS NOT NULL",
            (task_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT session_id, task_type, source_path, session_cost_usd, "
            "waste_event_count, waste_events, band_verdict "
            "FROM sessions WHERE session_cost_usd IS NOT NULL"
        ).fetchall()
    return [dict(r) for r in rows]


def _task_types(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT task_type FROM sessions").fetchall()
    return [r[0] for r in rows]


def _compute_h1(conn: sqlite3.Connection, prices: dict, task_type: str) -> HabitResult | None:
    """High context re-send ratio: sessions with >60% re-send cost more, on average."""
    sessions = _fetch_sessions(conn, task_type)
    if len(sessions) < MIN_N_FOR_HABIT:
        return None

    high_resend_costs: list[float] = []
    other_costs: list[float] = []

    for s in sessions:
        source_path = s.get("source_path")
        if not source_path or not Path(source_path).exists():
            continue
        try:
            record = adapt_session(Path(source_path))
            digest_dict = record.get("digest", {})
            turns = digest_dict.get("turns", [])
            if not turns:
                continue
            waste_entry = build_waste_entry(s["session_id"], turns)
            digest = reconstruct_digest(digest_dict)
            attr = compute_attribution(digest, waste_entry, prices)
        except Exception:
            continue

        if attr.total_billed_tokens == 0:
            continue

        cost = float(s["session_cost_usd"])
        resend_ratio = attr.context_resend_tokens / attr.total_billed_tokens
        if resend_ratio > 0.60:
            high_resend_costs.append(cost)
        else:
            other_costs.append(cost)

    n_high = len(high_resend_costs)
    n_total = n_high + len(other_costs)

    # Silent unless BOTH groups clear the floor — otherwise the comparison isn't honest.
    if n_high < MIN_N_FOR_HABIT or not other_costs:
        return None

    avg_high = sum(high_resend_costs) / n_high
    avg_other = sum(other_costs) / len(other_costs)
    if avg_other <= 0:
        return None

    excess = max(0.0, avg_high - avg_other)
    message = (
        f"In {n_high} of your last {n_total} `{task_type}` sessions, context re-send was "
        f">60% of billed tokens (measured). Those sessions cost ~${avg_high:.2f} on average "
        f"vs. ~${avg_other:.2f} for the rest {CAVEAT_SUFFIX}. "
        "Action: run `/compact` earlier in long sessions of this type."
    )
    return HabitResult(
        habit_id="H1", task_type=task_type, message=message,
        measured_n=n_high, impact_usd=excess * n_high,
    )


def _compute_h2(conn: sqlite3.Connection) -> HabitResult | None:
    """Recurring waste events (RR/RFR) — uses already-persisted waste_events."""
    sessions = _fetch_sessions(conn, None)
    n_total = len(sessions)
    if n_total < MIN_N_FOR_HABIT:
        return None

    n_with_waste = 0
    total_waste_usd = 0.0
    for s in sessions:
        raw_events = s.get("waste_events") or "[]"
        try:
            events = json.loads(raw_events) if isinstance(raw_events, str) else raw_events
        except (json.JSONDecodeError, TypeError):
            events = []
        if events:
            n_with_waste += 1
            total_waste_usd += sum(e.get("wasted_cost_usd") or 0.0 for e in events)

    if n_with_waste < MIN_N_FOR_HABIT:
        return None

    message = (
        f"REPEATED-FAILED-RETRY / REDUNDANT-READ waste fired in {n_with_waste} of your last "
        f"{n_total} sessions, ~${total_waste_usd:.2f} total wasted cost (measured, from the "
        f"waste events already detected) {CAVEAT_SUFFIX}. "
        "Action: when a command fails twice with the same error, or a file is re-read with no "
        "edit in between, stop — the session detail page shows the exact proof turns each time "
        "this repeats."
    )
    return HabitResult(
        habit_id="H2", task_type=None, message=message,
        measured_n=n_with_waste, impact_usd=total_waste_usd,
    )


_ABOVE_BAND_VERDICTS: frozenset[str] = frozenset({"above_p75"})


def _compute_h3(
    conn: sqlite3.Connection,
    self_bl: SelfBaselineState,
    task_type: str,
) -> HabitResult | None:
    """Sessions scored above the user's own baseline band cost more, on average."""
    type_bl = self_bl.by_type.get(task_type)
    if type_bl is None or type_bl.source != "self":
        return None  # data-gated: only once a real self-baseline is active

    rows = _fetch_sessions(conn, task_type)
    above = [float(r["session_cost_usd"]) for r in rows if r["band_verdict"] in _ABOVE_BAND_VERDICTS]
    rest = [float(r["session_cost_usd"]) for r in rows if r["band_verdict"] not in _ABOVE_BAND_VERDICTS]

    n_above = len(above)
    if n_above < MIN_N_FOR_HABIT or not rest:
        return None

    avg_above = sum(above) / n_above
    avg_rest = sum(rest) / len(rest)
    if avg_rest <= 0:
        return None

    excess = max(0.0, avg_above - avg_rest)
    n_total = n_above + len(rest)
    message = (
        f"{n_above} of your last {n_total} `{task_type}` sessions scored ABOVE your own "
        f"baseline band; those cost ~${avg_above:.2f} on average vs. ~${avg_rest:.2f} for "
        f"in-band-or-below sessions {CAVEAT_SUFFIX}. No single action attached — see the "
        "session detail pages for what made those heavier."
    )
    return HabitResult(
        habit_id="H3", task_type=task_type, message=message,
        measured_n=n_above, impact_usd=excess * n_above,
    )


def get_habits(
    conn: sqlite3.Connection,
    self_bl: SelfBaselineState,
    prices: dict,
    *,
    top_n: int = DEFAULT_TOP_N,
) -> list[HabitResult]:
    """Compute all supported habits, keep those clearing the N-gate, rank by $ impact.

    Returns an empty list when nothing clears the gate — silence over a made-up tip.
    """
    candidates: list[HabitResult] = []

    h2 = _compute_h2(conn)
    if h2 is not None:
        candidates.append(h2)

    for task_type in _task_types(conn):
        h1 = _compute_h1(conn, prices, task_type)
        if h1 is not None:
            candidates.append(h1)
        h3 = _compute_h3(conn, self_bl, task_type)
        if h3 is not None:
            candidates.append(h3)

    candidates.sort(key=lambda h: h.impact_usd, reverse=True)
    return candidates[:top_n]


__all__ = [
    "MIN_N_FOR_HABIT",
    "CAVEAT_SUFFIX",
    "DEFAULT_TOP_N",
    "HabitResult",
    "get_habits",
]
