from __future__ import annotations

"""tes/budget.py — Rolling-window spend tracking + honest self-trend projection.

Design: research/13_coach_alarm_honesty_design.md (reviewed and approved).

The projection is the user's OWN trend over the trailing window, linearly
extrapolated to the window's end, and ALWAYS labeled with its N (sessions,
days observed) and the non-forecast caveat. Never "you will spend $X" — the
message is always framed as "trending toward," never a promise.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DEFAULT_WINDOW_DAYS: int = 7
_MIN_DAYS_OBSERVED: float = 1.0 / 24  # floor at 1 hour — avoids divide-by-near-zero on a single fresh session


@dataclass
class BudgetProjection:
    window_days: int
    session_count: int
    days_observed: float
    total_usd_so_far: float
    projected_usd_for_window: float
    message: str


def compute_budget_projection(
    conn: sqlite3.Connection,
    window_days: int = DEFAULT_WINDOW_DAYS,
    _now: datetime | None = None,
) -> BudgetProjection | None:
    """Linear self-trend projection over the trailing window_days.

    Returns None when there are no sessions with cost data in the window —
    silence rather than a fabricated $0 projection (nothing to project).
    """
    now = _now if _now is not None else datetime.now(timezone.utc)
    window_start = now - timedelta(days=window_days)

    rows = conn.execute(
        "SELECT scored_at, session_cost_usd FROM sessions "
        "WHERE session_cost_usd IS NOT NULL AND scored_at >= ? "
        "ORDER BY scored_at ASC",
        (window_start.isoformat(),),
    ).fetchall()

    if not rows:
        return None

    total_usd = sum(float(r["session_cost_usd"]) for r in rows)
    session_count = len(rows)

    first_ts = datetime.fromisoformat(str(rows[0]["scored_at"]))
    days_observed = max((now - first_ts).total_seconds() / 86400.0, _MIN_DAYS_OBSERVED)

    daily_rate = total_usd / days_observed
    projected = daily_rate * window_days

    message = (
        f"At this pace (~${total_usd:.2f} so far across {session_count} session"
        f"{'s' if session_count != 1 else ''}, {days_observed:.1f} of {window_days} days) "
        f"you're trending toward ~${projected:.2f} over a {window_days}-day window — "
        f"based on your last {days_observed:.1f} days, not a forecast of future work; "
        "work volume varies."
    )

    return BudgetProjection(
        window_days=window_days,
        session_count=session_count,
        days_observed=round(days_observed, 2),
        total_usd_so_far=round(total_usd, 4),
        projected_usd_for_window=round(projected, 4),
        message=message,
    )


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "BudgetProjection",
    "compute_budget_projection",
]
