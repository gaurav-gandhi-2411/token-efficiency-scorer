from __future__ import annotations

"""tes/web/server.py — Localhost-only Flask dashboard for the TES scoring ledger.

Binds 127.0.0.1 ONLY. Never 0.0.0.0. No external network egress.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from flask import Flask, abort, g, render_template

from tes.adapt import adapt_session
from tes.attribution import AttributionResult, compute_attribution
from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes._digest import reconstruct_digest
from tes.cost import load_price_table
from tes.self_baseline import compute_baseline_cost_band, load_or_compute
from tes.store import (
    TrajectoryRenderState,
    get_session,
    list_sessions,
    open_db,
    trajectory_render_state,
)
from tes.waste import build_waste_entry
from tes.web.cost_format import format_cost_usd, format_cost_pct_vs_baseline, format_price_provenance

# ---------------------------------------------------------------------------
# Attribution helpers
# ---------------------------------------------------------------------------


def _compute_session_attribution(
    session: dict,
    prices: dict,
) -> AttributionResult | None:
    """Try to compute attribution for a session; returns None on any failure."""
    source_path = session.get("source_path")
    if not source_path:
        return None
    src = Path(source_path)
    if not src.exists():
        return None
    try:
        record = adapt_session(src)
        if record is None:
            return None
        digest_turns = record.get("digest", {}).get("turns", [])
        waste_entry = build_waste_entry(record["session_id"], digest_turns)
        digest_obj = record.get("digest")
        if digest_obj is None:
            return None
        digest = reconstruct_digest(digest_obj)
        return compute_attribution(digest, waste_entry, prices)
    except Exception:
        return None


def _build_attribution_takeaway(attr: AttributionResult) -> str:
    """Deterministic one-line takeaway with data-gated actionable hint.

    Hint rules (fires at most one):
      context >= 60% of cost  → context lever hint
      output  >= 40% of cost  → output lever hint (only when context < 60%)
      neither                 → description only, no lever
    """
    total_usd = attr.total_usd
    if total_usd == 0:
        return "No cost data — token bucket counts available in attribution table."

    def pct(v: float) -> int:
        return round(v / total_usd * 100)

    resend_pct = pct(attr.context_resend_usd)
    growth_pct = pct(attr.context_growth_usd)
    output_pct = pct(attr.output_usd)
    context_pct = resend_pct + growth_pct
    waste_usd = attr.rr_waste_usd + attr.rfr_waste_usd

    parts: list[str] = []
    if context_pct > 0:
        parts.append(f"context ({resend_pct}% re-send + {growth_pct}% growth)")
    if output_pct > 0:
        parts.append(f"output ({output_pct}%)")

    cost_desc = "Cost: " + " and ".join(parts) if parts else "Cost: distributed across buckets"
    waste_str = f"; detectable waste ${waste_usd:.2f}" if waste_usd > 0.001 else "; no detectable waste"

    # Data-gated lever hint — fires only when a bucket genuinely dominates
    if context_pct >= 60:
        hint = " — a long context drove most of the cost; checkpointing or /compact mid-session reduces re-send."
    elif output_pct >= 40:
        hint = " — output was a large cost share; shorter responses or fewer regenerations reduce this."
    else:
        hint = ""

    return cost_desc + waste_str + "." + hint


def _build_attribution_rows(attr: AttributionResult) -> list[dict]:
    """Build attribution table rows sorted by cost% descending."""
    tb = attr.total_billed_tokens
    total_usd = attr.total_usd

    def tok_pct(v: int) -> float:
        return round(v / tb * 100, 1) if tb else 0.0

    def cost_pct(v: float) -> float:
        return round(v / total_usd * 100, 1) if total_usd else 0.0

    rows = [
        {
            "label": "Context re-send (cache reads)",
            "bucket": "B3",
            "tokens": attr.context_resend_tokens,
            "tok_pct": tok_pct(attr.context_resend_tokens),
            "usd": attr.context_resend_usd,
            "cost_pct": cost_pct(attr.context_resend_usd),
            "is_waste": False,
        },
        {
            "label": "Output",
            "bucket": "B4",
            "tokens": attr.output_tokens,
            "tok_pct": tok_pct(attr.output_tokens),
            "usd": attr.output_usd,
            "cost_pct": cost_pct(attr.output_usd),
            "is_waste": False,
        },
        {
            "label": "Context growth (cache writes)",
            "bucket": "B6",
            "tokens": attr.context_growth_tokens,
            "tok_pct": tok_pct(attr.context_growth_tokens),
            "usd": attr.context_growth_usd,
            "cost_pct": cost_pct(attr.context_growth_usd),
            "is_waste": False,
        },
        {
            "label": "Fresh input (not attributable to detected waste)",
            "bucket": "B5",
            "tokens": attr.fresh_input_tokens,
            "tok_pct": tok_pct(attr.fresh_input_tokens),
            "usd": attr.fresh_input_usd,
            "cost_pct": cost_pct(attr.fresh_input_usd),
            "is_waste": False,
        },
        {
            "label": "Redundant-read waste",
            "bucket": "B1",
            "tokens": attr.rr_waste_tokens,
            "tok_pct": tok_pct(attr.rr_waste_tokens),
            "usd": attr.rr_waste_usd,
            "cost_pct": cost_pct(attr.rr_waste_usd),
            "is_waste": True,
        },
        {
            "label": "Retry-loop waste",
            "bucket": "B2",
            "tokens": attr.rfr_waste_tokens,
            "tok_pct": tok_pct(attr.rfr_waste_tokens),
            "usd": attr.rfr_waste_usd,
            "cost_pct": cost_pct(attr.rfr_waste_usd),
            "is_waste": True,
        },
    ]
    return sorted(rows, key=lambda r: r["cost_pct"], reverse=True)


def _stored_attribution_line(session: dict) -> str | None:
    """One-line attribution summary from STORED data only (no file I/O).

    Used in session list — shows waste cost breakdown from stored waste_events JSON.
    """
    cost_usd = session.get("session_cost_usd")
    if cost_usd is None:
        return None
    waste_events_raw = session.get("waste_events", "[]")
    try:
        waste_events = json.loads(waste_events_raw) if isinstance(waste_events_raw, str) else (waste_events_raw or [])
    except (json.JSONDecodeError, TypeError):
        waste_events = []

    waste_usd = sum(e.get("wasted_cost_usd") or 0 for e in waste_events)
    n_events = session.get("waste_event_count", 0) or 0

    if n_events > 0:
        return f"${float(cost_usd):.2f} total · waste ${waste_usd:.2f} ({n_events} event{'s' if n_events != 1 else ''})"
    return f"${float(cost_usd):.2f} total · no waste detected"


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
    # Load price table once at app startup — prices don't change between requests.
    _prices = load_price_table()
    _price_provenance = format_price_provenance(_prices)

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

        total_scored = len(sessions)
        task_type_counts: dict[str, int] = {}
        for s in sessions:
            tt = s["task_type"]
            task_type_counts[tt] = task_type_counts.get(tt, 0) + 1

        self_bl = get_self_bl()
        headline = _projected_metrics(conn, self_bl) if self_bl is not None else None

        # Annotate each session dict with cost-vs-baseline framing.
        # Compute cost bands per task type (one DB query per unique type).
        cost_bands: dict[str, tuple[float, float, float] | None] = {}
        for s in sessions:
            tt = s["task_type"]
            if tt not in cost_bands:
                type_bl = self_bl.by_type.get(tt) if self_bl is not None else None
                scope_floor = type_bl.scope_floor if type_bl is not None else 20
                cost_bands[tt] = compute_baseline_cost_band(conn, tt, scope_floor)

            band = cost_bands.get(tt)
            cost_usd = s.get("session_cost_usd")
            if band is not None and cost_usd is not None:
                pct = format_cost_pct_vs_baseline(float(cost_usd), band)
                s["cost_vs_baseline_pct"] = pct
                s["baseline_cost_median"] = band[1]
            else:
                s["cost_vs_baseline_pct"] = None
                s["baseline_cost_median"] = None

        pairs = [(s, trajectory_render_state(s)) for s in sessions]

        # Annotate each session with a stored-data attribution line (no file I/O).
        for s, _ in pairs:
            s["attribution_line"] = _stored_attribution_line(s)

        return render_template(
            "session_list.html",
            pairs=pairs,
            total_scored=total_scored,
            task_type_counts=task_type_counts,
            headline=headline,
            TrajectoryRenderState=TrajectoryRenderState,
            price_provenance=_price_provenance,
        )

    @app.route("/session/<session_id>")
    def session_detail(session_id: str) -> str:
        conn = get_db()
        session = get_session(conn, session_id)
        if session is None:
            abort(404)
        traj_state = trajectory_render_state(session)

        # Compute cost band for this session's task type.
        self_bl = get_self_bl()
        task_type = session.get("task_type", "")
        type_bl = self_bl.by_type.get(task_type) if self_bl is not None else None
        scope_floor = type_bl.scope_floor if type_bl is not None else 20
        cost_band = compute_baseline_cost_band(conn, task_type, scope_floor)

        cost_usd = session.get("session_cost_usd")
        if cost_band is not None and cost_usd is not None:
            pct = format_cost_pct_vs_baseline(float(cost_usd), cost_band)
            cost_vs_baseline_pct = pct
            baseline_cost_median = cost_band[1]
        else:
            cost_vs_baseline_pct = None
            baseline_cost_median = None

        # Attribution — requires source JSONL file; gracefully returns None if unavailable.
        attribution = _compute_session_attribution(session, _prices)
        attribution_takeaway = _build_attribution_takeaway(attribution) if attribution else None
        attribution_rows = _build_attribution_rows(attribution) if attribution else None

        return render_template(
            "session_detail.html",
            session=session,
            traj_state=traj_state,
            TrajectoryRenderState=TrajectoryRenderState,
            price_provenance=_price_provenance,
            cost_band=cost_band,
            cost_vs_baseline_pct=cost_vs_baseline_pct,
            baseline_cost_median=baseline_cost_median,
            attribution=attribution,
            attribution_takeaway=attribution_takeaway,
            attribution_rows=attribution_rows,
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
            return render_template("baseline_status.html", status_rows=[], headline=None,
                                   waste_by_type=[])

        headline = _projected_metrics(conn, self_bl)
        status_rows = _per_type_status(conn, self_bl)

        waste_rows = conn.execute(
            """
            SELECT task_type,
                   COUNT(*) AS total,
                   SUM(CASE WHEN waste_event_count > 0 THEN 1 ELSE 0 END) AS with_waste,
                   SUM(waste_event_count) AS total_events
            FROM sessions
            GROUP BY task_type
            ORDER BY total_events DESC
            """
        ).fetchall()
        waste_by_type = [dict(r) for r in waste_rows]

        return render_template(
            "baseline_status.html",
            status_rows=status_rows,
            headline=headline,
            waste_by_type=waste_by_type,
        )

    return app


def start_server(config: ServerConfig) -> None:
    """Start the Flask development server (blocking).

    Binds exclusively to 127.0.0.1 — never exposed to external interfaces.
    """
    app = create_app(config)
    app.run(host=config.host, port=config.port, debug=False, use_reloader=False, threaded=True)
