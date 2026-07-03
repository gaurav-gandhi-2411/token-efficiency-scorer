from __future__ import annotations

"""tests/test_projection_labeled.py — Budget projection is a labeled self-trend, never a forecast.

Covers: silence when no sessions in window, presence of the non-forecast caveat
and window/N, and that no false-certainty phrasing ("you will spend") ever appears.
"""

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tes.budget import compute_budget_projection
from tes.store import open_db


def _insert_session(
    conn: sqlite3.Connection,
    session_id: str,
    scored_at: str,
    cost_usd: float | None,
) -> None:
    """Insert a minimal-but-valid session row — only scored_at/cost vary across tests."""
    conn.execute(
        """
        INSERT INTO sessions (
            session_id, task_type, source_path, source_mtime, source_hash, scored_at,
            axes_scored, real_tokens, scope_status, baseline_available,
            p25, p75, median, band_verdict, interpretation, token_domain_of_validity,
            baseline_source, judge_verdict, judge_score, judge_reasoning,
            trajectory_domain_of_validity, judge_source_hash,
            waste_event_count, waste_events, waste_domain_of_validity,
            turn_count, session_cost_usd, cost_approximate, cost_domain_of_validity
        ) VALUES (
            ?, 'infra-deploy', '/fake/path.jsonl', 0.0, 'hash', ?,
            '["token"]', 1000, 'in_scope', 1,
            NULL, NULL, NULL, 'within_band', '', '',
            'self', NULL, NULL, NULL,
            '', NULL,
            0, '[]', '',
            30, ?, 0, ''
        )
        """,
        (session_id, scored_at, cost_usd),
    )
    conn.commit()


def _now() -> datetime:
    return datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Silence when there's nothing to project
# ---------------------------------------------------------------------------


def test_none_when_no_sessions_in_window(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    assert compute_budget_projection(conn, window_days=7, _now=_now()) is None


def test_none_when_sessions_exist_but_outside_window(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    old_ts = (_now() - timedelta(days=30)).isoformat()
    _insert_session(conn, "old-session", old_ts, 5.0)
    assert compute_budget_projection(conn, window_days=7, _now=_now()) is None


# ---------------------------------------------------------------------------
# Honest labeling when a projection IS produced
# ---------------------------------------------------------------------------


def test_projection_carries_n_window_and_nonforecast_caveat(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    _insert_session(conn, "s1", (now - timedelta(days=3)).isoformat(), 4.0)
    _insert_session(conn, "s2", (now - timedelta(days=1)).isoformat(), 6.0)

    projection = compute_budget_projection(conn, window_days=7, _now=now)
    assert projection is not None
    assert projection.session_count == 2
    assert projection.window_days == 7
    assert "2 sessions" in projection.message
    assert "based on your last" in projection.message
    assert "not a forecast of future work" in projection.message
    assert "trending toward" in projection.message


def test_projection_never_states_false_certainty(tmp_path: Path) -> None:
    """Regex guard: the message must never claim a bare future spend as certain fact."""
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    _insert_session(conn, "s1", (now - timedelta(hours=6)).isoformat(), 10.0)

    projection = compute_budget_projection(conn, window_days=7, _now=now)
    assert projection is not None
    # Must never say "you will spend $X" or "you'll spend $X" — a false-certainty forecast.
    assert not re.search(r"you('ll| will) spend", projection.message, re.IGNORECASE)
    # "trending toward" (labeled estimate) must accompany every dollar figure claim.
    assert "trending toward" in projection.message


def test_projection_handles_single_very_recent_session_without_crashing(tmp_path: Path) -> None:
    """days_observed floor (1 hour) prevents a divide-by-near-zero blowup."""
    conn = open_db(tmp_path / "tes.db")
    now = _now()
    _insert_session(conn, "s1", (now - timedelta(seconds=5)).isoformat(), 2.0)

    projection = compute_budget_projection(conn, window_days=7, _now=now)
    assert projection is not None
    assert projection.projected_usd_for_window >= 0
    assert projection.days_observed > 0
