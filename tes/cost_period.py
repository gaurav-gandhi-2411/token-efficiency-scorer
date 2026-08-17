from __future__ import annotations

"""tes/cost_period.py — Period-scoped spend report: total, session count, per-project breakdown.

LL3 (GitHub issue #78148): `tes cost --week` / `--month` / `--since <date>`.
A REPORT of real spend already incurred in a period -- distinct from
`tes budget`'s rolling self-trend PROJECTION (budget.py answers "where is my
pace heading"; this module answers "what did I actually spend").

**source_mtime vs scored_at (LL3.3) -- deliberate, and NOT the same choice
budget.py made:**

This module filters on `source_mtime` -- the session FILE's own last-write
time on disk, i.e. when the real Claude Code usage happened -- not
`scored_at` (when the `tes score`/`tes scan` command that computed the cost
happened to run). These are the same instant only under an immediate-scoring
workflow (score right after each session ends). Under a batch-scoring
workflow (run `tes scan` once over a week's worth of sessions), every one of
those sessions gets the SAME `scored_at` timestamp -- a `scored_at`-based
period report would cluster a week of real spend onto one instant, or drop
it outside the requested window entirely, silently misattributing spend
across period boundaries. `source_mtime` reflects when the money was
actually spent, independent of when the user got around to running the
scorer.

**Known, separate, out-of-scope observation:** `tes/budget.py`'s existing
rolling-window projection filters on `scored_at`, not `source_mtime`, for
exactly the reason this module avoids it -- under a batch-scoring workflow,
`compute_budget_projection`'s trend is describing "cost incurred in scoring
runs over the last N days," not "cost incurred by real usage in the last N
days." This is a real, pre-existing characteristic of budget.py, found while
designing this module -- NOT fixed here. LL3 is scoped to the new `tes cost`
command only (one coherent change per PR); this is flagged for a dedicated
follow-up, not silently patched alongside an unrelated feature.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_WEEK_DAYS: int = 7
DEFAULT_MONTH_DAYS: int = 30


@dataclass
class ProjectCostBreakdown:
    project_label: str
    total_usd: float
    session_count: int


@dataclass
class PeriodCostReport:
    period_label: str
    period_start: datetime
    period_end: datetime
    total_usd: float
    session_count: int
    sessions_missing_cost: int
    by_project: list[ProjectCostBreakdown]
    # XX1.3: unpriced coverage -- what fraction of this period's sessions
    # and tokens are priced, and which models caused the gap. token_total
    # includes ALL sessions in the window regardless of pricing (a
    # denominator that only counted priced sessions would make 100%
    # coverage trivially true by construction). unpriced_models is
    # aggregated only from rows that persisted it at score time (XX1.3 /
    # RR1 lesson) -- legacy rows scored before that column existed
    # contribute to the coverage gap but can't name their own model;
    # unpriced_models_incomplete flags that distinction.
    token_total: int = 0
    token_priced: int = 0
    unpriced_models: list[str] = field(default_factory=list)
    unpriced_models_incomplete: bool = False

    @property
    def session_coverage_pct(self) -> float | None:
        total = self.session_count + self.sessions_missing_cost
        return 100.0 * self.session_count / total if total else None

    @property
    def token_coverage_pct(self) -> float | None:
        return 100.0 * self.token_priced / self.token_total if self.token_total else None


def _project_label_from_source_path(source_path: str) -> str:
    """Same derivation as cli.py's `_project_label` (parent directory name,
    last 40 chars) -- reimplemented here rather than imported, to keep this
    module free of a dependency on the CLI layer. Both must agree; enforced
    by tests/test_cost_period.py's cross-check against cli._project_label.
    """
    name = Path(source_path).parent.name
    return name[-40:] if len(name) > 40 else name


def compute_period_cost(
    conn: sqlite3.Connection,
    period_start: datetime,
    period_end: datetime,
    period_label: str = "",
) -> PeriodCostReport:
    """Total spend, session count, and per-project breakdown for
    ``[period_start, period_end)`` by ``source_mtime`` -- see module
    docstring for why ``source_mtime``, not ``scored_at``.

    Boundary is INCLUSIVE of ``period_start``, EXCLUSIVE of ``period_end``
    (LL3.4, cross-boundary case) -- a session sitting exactly on a boundary
    instant belongs to exactly one of two adjacent periods run back-to-back,
    never both, never neither.

    Sessions with ``session_cost_usd IS NULL`` (never scored for cost, or
    scored before the cost axis existed in this DB) are EXCLUDED from
    ``total_usd``/``by_project`` but counted separately in
    ``sessions_missing_cost`` -- silently omitting them from the total
    without reporting the omission would make an incomplete total look
    complete (rule 98a: a control's data fetch must fail closed, never
    silently drop coverage).

    An EMPTY period (LL3.4, zero sessions of any kind) still returns a
    valid, ordinary report with ``total_usd=0.0``, ``session_count=0`` --
    never ``None`` and never an exception; "checked, found nothing" always
    looks different from a raised error, and this function never raises on
    a normal (even empty) query.

    A SINGLE matching session (LL3.4) is handled with no special case at
    all -- ``by_project`` just has one entry with ``session_count=1``.
    """
    rows = conn.execute(
        "SELECT source_path, session_cost_usd, real_tokens, cost_unpriced_models "
        "FROM sessions WHERE source_mtime >= ? AND source_mtime < ?",
        (period_start.timestamp(), period_end.timestamp()),
    ).fetchall()

    priced = [r for r in rows if r["session_cost_usd"] is not None]
    missing = len(rows) - len(priced)

    token_total = sum(r["real_tokens"] or 0 for r in rows)
    token_priced = sum(r["real_tokens"] or 0 for r in priced)

    missing_rows = [r for r in rows if r["session_cost_usd"] is None]
    named_models: set[str] = set()
    for r in missing_rows:
        raw = r["cost_unpriced_models"]
        if raw:
            named_models.update(raw.split(","))
    # A missing-cost row with no cost_unpriced_models value is either a
    # legacy row (scored before that column existed) or a session where
    # cost was never computed at all (e.g. --no-judge scoring that also
    # skipped cost) -- either way, its model can't be named, and that
    # matters: silently showing a "complete" unpriced_models list when some
    # of the gap has no attributable model would misstate coverage.
    unpriced_models_incomplete = any(not r["cost_unpriced_models"] for r in missing_rows)

    totals_by_project: dict[str, list[float]] = {}
    for r in priced:
        label = _project_label_from_source_path(r["source_path"])
        totals_by_project.setdefault(label, []).append(float(r["session_cost_usd"]))

    by_project = sorted(
        (
            ProjectCostBreakdown(project_label=label, total_usd=sum(costs), session_count=len(costs))
            for label, costs in totals_by_project.items()
        ),
        key=lambda b: b.total_usd,
        reverse=True,
    )

    return PeriodCostReport(
        period_label=period_label,
        period_start=period_start,
        period_end=period_end,
        total_usd=sum(c.total_usd for c in by_project),
        session_count=len(priced),
        sessions_missing_cost=missing,
        by_project=by_project,
        token_total=token_total,
        token_priced=token_priced,
        unpriced_models=sorted(named_models),
        unpriced_models_incomplete=unpriced_models_incomplete,
    )


def resolve_period(
    *,
    week: bool = False,
    month: bool = False,
    since: str | None = None,
    _now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    """Resolves ``--week``/``--month``/``--since`` into (period_start,
    period_end, period_label). Exactly one of week/month/since must be
    truthy -- the CLI's mutually-exclusive argparse group enforces this
    before calling here; this function itself raises ValueError if that
    invariant is somehow violated, rather than silently picking one.

    ``--week``/``--month`` are ROLLING N-day windows ending at ``_now``
    (default 7/30 days) -- matching ``budget.py``'s own
    ``DEFAULT_WINDOW_DAYS`` rolling-window precedent, deliberately NOT a
    calendar week/month. A calendar-aligned window needs a user timezone and
    a first-day-of-week convention this tool has no basis to guess, and
    would silently disagree with `tes budget`'s own meaning of "window" for
    the same English words. ``--since <YYYY-MM-DD>`` is an explicit lower
    bound with no upper bound other than ``_now``.
    """
    now = _now if _now is not None else datetime.now(timezone.utc)
    if since is not None:
        try:
            start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise ValueError(f"--since must be YYYY-MM-DD, got {since!r}") from exc
        return start, now, f"since {since}"
    if month:
        return now - timedelta(days=DEFAULT_MONTH_DAYS), now, f"last {DEFAULT_MONTH_DAYS} days"
    if week:
        return now - timedelta(days=DEFAULT_WEEK_DAYS), now, f"last {DEFAULT_WEEK_DAYS} days"
    raise ValueError("resolve_period: exactly one of week/month/since must be given")


__all__ = [
    "DEFAULT_MONTH_DAYS",
    "DEFAULT_WEEK_DAYS",
    "PeriodCostReport",
    "ProjectCostBreakdown",
    "compute_period_cost",
    "resolve_period",
]
