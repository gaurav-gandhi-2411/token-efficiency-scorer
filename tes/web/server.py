from __future__ import annotations

"""tes/web/server.py — Localhost-only Flask dashboard for the TES scoring ledger.

Binds 127.0.0.1 ONLY. Never 0.0.0.0. No external network egress.
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask, abort, g, render_template

from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.self_baseline import load_or_compute
from tes.store import (
    TrajectoryRenderState,
    get_session,
    list_sessions,
    open_db,
    trajectory_render_state,
)

# Historical anchor: B2-era scored sessions among content sessions (turn_count > 0).
# At P4 activation (2026-06-08): 545 total unavailable, 509 zero-turn stubs,
# 36 content sessions OOS under B2 scope gates → 174/210 content sessions scored (82.9%).
_B2_ERA_CONTENT_SCORED: int = 174
_B2_ERA_CONTENT_SCORED_PCT: float = 82.9
_B2_ERA_CONTENT_TOTAL: int = 210


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"   # NEVER change to 0.0.0.0 — moat discipline
    port: int = 4747
    db_path: Path | None = None
    cc_baselines_path: Path | None = None


def _projected_metrics(conn: sqlite3.Connection, self_bl_state) -> dict:
    """Compute coverage metrics using content-session denominator.

    Does NOT re-score sessions — computes from stored turn_counts and band_verdicts.
    Splits sessions into content (turn_count > 0) vs empty stubs (turn_count = 0/NULL).
    Empty stubs are never scorable by anything — they're excluded from the coverage %.
    """
    total: int = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    content_sessions: int = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE turn_count > 0"
    ).fetchone()[0]
    empty_stubs: int = total - content_sessions
    content_self_scored: int = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE turn_count > 0 AND band_verdict != 'unavailable'"
    ).fetchone()[0]

    def pct_of_total(n: int) -> float:
        return round(n / max(1, total) * 100, 1)

    def pct_of_content(n: int) -> float:
        return round(n / max(1, content_sessions) * 100, 1)

    return {
        "total": total,
        "content_sessions": content_sessions,
        "empty_stubs": empty_stubs,
        "empty_stubs_pct": pct_of_total(empty_stubs),
        "content_self_scored": content_self_scored,
        "content_self_pct": pct_of_content(content_self_scored),
        "b2_content_scored": _B2_ERA_CONTENT_SCORED,
        "b2_content_scored_pct": _B2_ERA_CONTENT_SCORED_PCT,
        "b2_content_total": _B2_ERA_CONTENT_TOTAL,
    }


def _per_type_status(conn: sqlite3.Connection, self_bl_state) -> list[dict]:
    """Build per-type status rows for the baseline-status panel."""
    rows = []
    for task_type, type_bl in sorted(self_bl_state.by_type.items()):
        sf = type_bl.scope_floor
        total_type: int = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE task_type = ?", (task_type,)
        ).fetchone()[0]
        in_scope: int = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE task_type = ? AND turn_count >= ?",
            (task_type, sf),
        ).fetchone()[0]
        rows.append({
            "task_type": task_type,
            "total": total_type,
            "in_scope": in_scope,
            "waste_free_n": type_bl.waste_free_n,
            "lean_n": type_bl.lean_n,
            "scope_floor": sf,
            "source": type_bl.source,
            "sessions_needed": type_bl.sessions_needed,
            "p25": type_bl.p25,
            "median": type_bl.median,
            "p75": type_bl.p75,
            "domain_of_validity": type_bl.domain_of_validity,
        })
    return rows


def create_app(config: ServerConfig) -> Flask:
    """Create and configure the Flask dashboard application."""
    app = Flask(__name__, template_folder="templates")

    baselines_path = config.cc_baselines_path or BUNDLED_BASELINES_PATH
    _b2 = load_baselines(baselines_path)

    # -----------------------------------------------------------------------
    # Database connection helpers
    # -----------------------------------------------------------------------

    def get_db() -> sqlite3.Connection:
        if "_db" not in g:
            g._db = open_db(config.db_path)
        return g._db

    @app.teardown_appcontext
    def close_db(exc: BaseException | None = None) -> None:
        db = g.pop("_db", None)
        if db is not None:
            db.close()

    def get_self_bl():
        if config.db_path is not None:
            return load_or_compute(config.db_path, _b2)
        return None

    # -----------------------------------------------------------------------
    # Routes
    # -----------------------------------------------------------------------

    @app.route("/")
    def session_list() -> str:
        conn = get_db()
        sessions = list_sessions(conn, limit=100)
        pairs = [(s, trajectory_render_state(s)) for s in sessions]

        total_scored = len(sessions)
        task_type_counts: dict[str, int] = {}
        for s in sessions:
            tt = s["task_type"]
            task_type_counts[tt] = task_type_counts.get(tt, 0) + 1

        self_bl = get_self_bl()
        headline = _projected_metrics(conn, self_bl) if self_bl is not None else None

        return render_template(
            "session_list.html",
            pairs=pairs,
            total_scored=total_scored,
            task_type_counts=task_type_counts,
            headline=headline,
            TrajectoryRenderState=TrajectoryRenderState,
        )

    @app.route("/session/<session_id>")
    def session_detail(session_id: str) -> str:
        conn = get_db()
        session = get_session(conn, session_id)
        if session is None:
            abort(404)
        traj_state = trajectory_render_state(session)
        return render_template(
            "session_detail.html",
            session=session,
            traj_state=traj_state,
            TrajectoryRenderState=TrajectoryRenderState,
        )

    @app.route("/trends")
    def trends() -> str:
        conn = get_db()

        task_type_rows = conn.execute(
            "SELECT task_type, COUNT(*) AS cnt FROM sessions GROUP BY task_type ORDER BY cnt DESC"
        ).fetchall()
        band_verdict_rows = conn.execute(
            "SELECT band_verdict, COUNT(*) AS cnt FROM sessions GROUP BY band_verdict ORDER BY cnt DESC"
        ).fetchall()
        waste_over_time_rows = conn.execute(
            """
            SELECT scored_at, session_id, waste_event_count
            FROM sessions
            ORDER BY scored_at DESC
            LIMIT 50
            """
        ).fetchall()

        return render_template(
            "trends.html",
            task_type_rows=[dict(r) for r in task_type_rows],
            band_verdict_rows=[dict(r) for r in band_verdict_rows],
            waste_over_time_rows=[dict(r) for r in waste_over_time_rows],
        )

    @app.route("/baseline-status")
    def baseline_status() -> str:
        conn = get_db()
        self_bl = get_self_bl()
        if self_bl is None:
            return render_template("baseline_status.html", status_rows=[], headline=None)

        headline = _projected_metrics(conn, self_bl)
        status_rows = _per_type_status(conn, self_bl)

        return render_template(
            "baseline_status.html",
            status_rows=status_rows,
            headline=headline,
        )

    return app


def start_server(config: ServerConfig) -> None:
    """Start the Flask development server (blocking).

    Binds exclusively to 127.0.0.1 — never exposed to external interfaces.
    """
    app = create_app(config)
    app.run(host=config.host, port=config.port, debug=False, use_reloader=False, threaded=True)
