from __future__ import annotations

"""tes/web/server.py — Localhost-only Flask dashboard for the TES scoring ledger.

Binds 127.0.0.1 ONLY. Never 0.0.0.0. No external network egress.
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask, abort, g, render_template

from tes.store import (
    TrajectoryRenderState,
    get_session,
    list_sessions,
    open_db,
    trajectory_render_state,
)


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"   # NEVER change to 0.0.0.0 — moat discipline
    port: int = 4747
    db_path: Path | None = None


def create_app(config: ServerConfig) -> Flask:
    """Create and configure the Flask dashboard application.

    All routes are registered inside this factory so each call gets an
    isolated app — safe for testing with separate tmp db_path values.
    """
    app = Flask(__name__, template_folder="templates")

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

    # -----------------------------------------------------------------------
    # Routes
    # -----------------------------------------------------------------------

    @app.route("/")
    def session_list() -> str:
        conn = get_db()
        sessions = list_sessions(conn, limit=100)

        # Compute traj_state for each session in the route handler
        pairs = [(s, trajectory_render_state(s)) for s in sessions]

        # Trend summary
        total_scored = len(sessions)
        task_type_counts: dict[str, int] = {}
        for s in sessions:
            tt = s["task_type"]
            task_type_counts[tt] = task_type_counts.get(tt, 0) + 1

        return render_template(
            "session_list.html",
            pairs=pairs,
            total_scored=total_scored,
            task_type_counts=task_type_counts,
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

    return app


def start_server(config: ServerConfig) -> None:
    """Start the Flask development server (blocking).

    Binds exclusively to 127.0.0.1 — never exposed to external interfaces.
    """
    app = create_app(config)
    app.run(host=config.host, port=config.port, debug=False, use_reloader=False)
