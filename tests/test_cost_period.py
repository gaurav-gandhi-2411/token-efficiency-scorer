from __future__ import annotations

"""tests/test_cost_period.py — LL3: `tes cost` period report.

Covers: empty period, single session, cross-boundary (exactly-one-period
membership), per-project breakdown, sessions_missing_cost accounting
(excluded from total but reported, not silently dropped), --week/--month/
--since resolution, and the source_mtime-vs-scored_at decision itself (a
session whose source_mtime is inside the period but scored_at is not, and
vice versa, must be included/excluded by source_mtime only).
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tes.cli import _project_label
from tes.cost_period import (
    _project_label_from_source_path,
    compute_period_cost,
    resolve_period,
)
from tes.store import open_db


def _insert_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    source_path: str = "/fake/proj/sess.jsonl",
    source_mtime: float,
    scored_at: str,
    cost_usd: float | None,
    real_tokens: int = 1000,
    unpriced_models: str | None = None,
) -> None:
    """Insert a minimal-but-valid session row -- mirrors
    tests/test_projection_labeled.py's helper, but exposes source_mtime as a
    real, independently-controllable float (that test's helper hardcodes it
    to 0.0, which is fine there since budget.py never reads that column, but
    this module's whole point is filtering on it).
    """
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, task_type, source_path, source_mtime, source_hash, scored_at,
            axes_scored, real_tokens, scope_status, baseline_available,
            p25, p75, median, band_verdict, interpretation, token_domain_of_validity,
            baseline_source, judge_verdict, judge_score, judge_reasoning,
            trajectory_domain_of_validity, judge_source_hash,
            waste_event_count, waste_events, waste_domain_of_validity,
            turn_count, session_cost_usd, cost_approximate, cost_domain_of_validity,
            cost_unpriced_models
        ) VALUES (
            ?, 'infra-deploy', ?, ?, 'hash', ?,
            '["token"]', ?, 'in_scope', 1,
            NULL, NULL, NULL, 'within_band', '', '',
            'self', NULL, NULL, NULL,
            '', NULL,
            0, '[]', '',
            30, ?, 0, '',
            ?
        )
        """,
        (session_id, source_path, source_mtime, scored_at, real_tokens, cost_usd, unpriced_models),
    )
    conn.commit()


def _now() -> datetime:
    return datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# LL3.4: empty period, single session, cross-boundary
# ---------------------------------------------------------------------------


def test_empty_period_returns_zero_report_not_none_not_an_error(tmp_path: Path):
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    report = compute_period_cost(conn, now - timedelta(days=7), now)

    assert report.total_usd == 0.0
    assert report.session_count == 0
    assert report.sessions_missing_cost == 0
    assert report.by_project == []


def test_single_session_in_period_reports_it_correctly(tmp_path: Path):
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    mid = now - timedelta(days=3)
    _insert_session(
        conn,
        "s1",
        source_path="/fake/C--Users-gaura-ml-projects-adk-tracegauge/s1.jsonl",
        source_mtime=mid.timestamp(),
        scored_at=mid.isoformat(),
        cost_usd=1.50,
    )

    report = compute_period_cost(conn, now - timedelta(days=7), now)

    assert report.total_usd == pytest.approx(1.50)
    assert report.session_count == 1
    assert len(report.by_project) == 1
    assert report.by_project[0].session_count == 1


def test_session_exactly_at_boundary_belongs_to_exactly_one_of_two_adjacent_periods(
    tmp_path: Path,
):
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    boundary = now - timedelta(days=7)
    _insert_session(
        conn, "s1", source_mtime=boundary.timestamp(), scored_at=boundary.isoformat(), cost_usd=2.0
    )

    earlier_period = compute_period_cost(conn, boundary - timedelta(days=7), boundary)
    later_period = compute_period_cost(conn, boundary, boundary + timedelta(days=7))

    # Inclusive of period_start, exclusive of period_end -- the boundary
    # instant belongs to the LATER period only.
    assert earlier_period.session_count == 0
    assert later_period.session_count == 1
    assert later_period.total_usd == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Per-project breakdown and sessions_missing_cost accounting
# ---------------------------------------------------------------------------


def test_per_project_breakdown_groups_and_sums_correctly(tmp_path: Path):
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    ts = (now - timedelta(days=2)).timestamp()
    iso = (now - timedelta(days=2)).isoformat()
    _insert_session(
        conn, "a1", source_path="/fake/proj-a/x.jsonl", source_mtime=ts, scored_at=iso, cost_usd=1.0
    )
    _insert_session(
        conn, "a2", source_path="/fake/proj-a/y.jsonl", source_mtime=ts, scored_at=iso, cost_usd=2.0
    )
    _insert_session(
        conn, "b1", source_path="/fake/proj-b/z.jsonl", source_mtime=ts, scored_at=iso, cost_usd=5.0
    )

    report = compute_period_cost(conn, now - timedelta(days=7), now)

    assert report.total_usd == pytest.approx(8.0)
    assert report.session_count == 3
    by_label = {b.project_label: b for b in report.by_project}
    assert by_label["proj-a"].total_usd == pytest.approx(3.0)
    assert by_label["proj-a"].session_count == 2
    assert by_label["proj-b"].total_usd == pytest.approx(5.0)
    # Sorted descending by spend -- highest-spend project first.
    assert report.by_project[0].project_label == "proj-b"


def test_sessions_missing_cost_excluded_from_total_but_counted_separately(tmp_path: Path):
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    ts = (now - timedelta(days=1)).timestamp()
    iso = (now - timedelta(days=1)).isoformat()
    _insert_session(conn, "priced", source_mtime=ts, scored_at=iso, cost_usd=3.0)
    _insert_session(conn, "unpriced", source_mtime=ts, scored_at=iso, cost_usd=None)

    report = compute_period_cost(conn, now - timedelta(days=7), now)

    assert report.total_usd == pytest.approx(3.0)  # NOT treated as $0 contribution
    assert report.session_count == 1
    assert report.sessions_missing_cost == 1


# ---------------------------------------------------------------------------
# LL3.3: source_mtime, not scored_at, is the filtering column
# ---------------------------------------------------------------------------


def test_filters_by_source_mtime_not_scored_at(tmp_path: Path):
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    period_start, period_end = now - timedelta(days=7), now

    # Batch-scored long after the fact: source_mtime is INSIDE the window
    # (real usage happened this week), scored_at is OUTSIDE it (scored a
    # month later). Must be INCLUDED -- proves source_mtime governs.
    _insert_session(
        conn,
        "batch-scored-late",
        source_mtime=(now - timedelta(days=3)).timestamp(),
        scored_at=(now + timedelta(days=30)).isoformat(),
        cost_usd=4.0,
    )
    # The inverse: scored_at falls inside the window (someone re-ran `tes
    # score` on an old file this week), but the real usage (source_mtime)
    # was months ago. Must be EXCLUDED -- a scored_at-based filter would
    # wrongly include this in "this week's" spend.
    _insert_session(
        conn,
        "old-usage-rescored-this-week",
        source_mtime=(now - timedelta(days=90)).timestamp(),
        scored_at=(now - timedelta(days=1)).isoformat(),
        cost_usd=99.0,
    )

    report = compute_period_cost(conn, period_start, period_end)

    assert report.session_count == 1
    assert report.total_usd == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# resolve_period
# ---------------------------------------------------------------------------


def test_resolve_period_week_is_rolling_seven_days():
    now = _now()
    start, end, label = resolve_period(week=True, _now=now)
    assert end == now
    assert start == now - timedelta(days=7)
    assert "7" in label


def test_resolve_period_month_is_rolling_thirty_days_not_calendar_month():
    now = _now()
    start, end, label = resolve_period(month=True, _now=now)
    assert start == now - timedelta(days=30)
    assert "30" in label


def test_resolve_period_since_parses_explicit_date():
    now = _now()
    start, end, label = resolve_period(since="2026-08-01", _now=now)
    assert start == datetime(2026, 8, 1, tzinfo=UTC)
    assert end == now
    assert "2026-08-01" in label


def test_resolve_period_since_rejects_bad_format():
    with pytest.raises(ValueError):
        resolve_period(since="not-a-date", _now=_now())


def test_resolve_period_requires_exactly_one_of_week_month_since():
    with pytest.raises(ValueError):
        resolve_period(_now=_now())


# ---------------------------------------------------------------------------
# project-label derivation must agree with cli.py's own version
# ---------------------------------------------------------------------------


def test_project_label_derivation_matches_cli_module():
    path = "/fake/C--Users-gaura-ml-projects-token-efficiency-scorer/abc123.jsonl"
    assert _project_label_from_source_path(path) == _project_label(Path(path))


# ---------------------------------------------------------------------------
# XX1.3: unpriced coverage -- sessions AND tokens, unpriced model names
# ---------------------------------------------------------------------------


def test_full_coverage_when_everything_priced(tmp_path: Path):
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    ts, iso = (now - timedelta(days=1)).timestamp(), (now - timedelta(days=1)).isoformat()
    _insert_session(conn, "a", source_mtime=ts, scored_at=iso, cost_usd=3.0, real_tokens=500)

    report = compute_period_cost(conn, now - timedelta(days=7), now)

    assert report.session_coverage_pct == pytest.approx(100.0)
    assert report.token_coverage_pct == pytest.approx(100.0)
    assert report.unpriced_models == []
    assert report.unpriced_models_incomplete is False


def test_coverage_fractions_reflect_missing_sessions_and_tokens(tmp_path: Path):
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    ts, iso = (now - timedelta(days=1)).timestamp(), (now - timedelta(days=1)).isoformat()
    _insert_session(conn, "priced", source_mtime=ts, scored_at=iso, cost_usd=3.0, real_tokens=300)
    _insert_session(
        conn,
        "unpriced",
        source_mtime=ts,
        scored_at=iso,
        cost_usd=None,
        real_tokens=700,
        unpriced_models="claude-future-9",
    )

    report = compute_period_cost(conn, now - timedelta(days=7), now)

    assert report.session_coverage_pct == pytest.approx(50.0)  # 1 of 2 sessions
    assert report.token_coverage_pct == pytest.approx(30.0)  # 300 of 1000 tokens
    assert report.unpriced_models == ["claude-future-9"]
    assert report.unpriced_models_incomplete is False


def test_unpriced_models_deduplicated_and_sorted(tmp_path: Path):
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    ts, iso = (now - timedelta(days=1)).timestamp(), (now - timedelta(days=1)).isoformat()
    _insert_session(
        conn, "a", source_mtime=ts, scored_at=iso, cost_usd=None, unpriced_models="zeta-model"
    )
    _insert_session(
        conn, "b", source_mtime=ts, scored_at=iso, cost_usd=None, unpriced_models="alpha-model"
    )
    _insert_session(
        conn, "c", source_mtime=ts, scored_at=iso, cost_usd=None, unpriced_models="zeta-model"
    )

    report = compute_period_cost(conn, now - timedelta(days=7), now)

    assert report.unpriced_models == ["alpha-model", "zeta-model"]


def test_legacy_unpriced_session_with_no_model_name_flags_incomplete(tmp_path: Path):
    """A missing-cost row scored before cost_unpriced_models existed (or
    where cost was never computed) has unpriced_models=NULL -- the coverage
    gap is real but the model can't be named. Must be flagged, not silently
    absorbed into an unpriced_models list that would then look complete."""
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    ts, iso = (now - timedelta(days=1)).timestamp(), (now - timedelta(days=1)).isoformat()
    _insert_session(
        conn, "legacy", source_mtime=ts, scored_at=iso, cost_usd=None, unpriced_models=None
    )
    _insert_session(
        conn, "named", source_mtime=ts, scored_at=iso, cost_usd=None, unpriced_models="some-model"
    )

    report = compute_period_cost(conn, now - timedelta(days=7), now)

    assert report.unpriced_models == ["some-model"]
    assert report.unpriced_models_incomplete is True


def test_empty_period_has_no_coverage_percentage_not_a_zero_division(tmp_path: Path):
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    report = compute_period_cost(conn, now - timedelta(days=7), now)

    assert report.session_coverage_pct is None
    assert report.token_coverage_pct is None
