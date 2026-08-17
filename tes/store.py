from __future__ import annotations

"""tes/store.py — SQLite persistence layer for the TES scoring ledger.

Public API:
    open_db(path)                                          -> sqlite3.Connection
    file_hash(path)                                        -> str
    needs_scoring(conn, session_id, current_hash)          -> bool
    upsert_session(conn, result, source_path, source_mtime, source_hash, turn_count)
    get_session(conn, session_id)                          -> dict | None
    list_sessions(conn, limit, offset)                     -> list[dict]

Schema version 1. Version encoded in PRAGMA user_version — no meta table.
Migration: turn_count column added via ALTER TABLE if absent (added after initial release).
"""

import enum
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from tes.score import ThreeAxisResult


class TrajectoryRenderState(enum.Enum):
    UNAVAILABLE = "unavailable"   # judge_verdict is None (never ran, or errored at scoring time)
    CURRENT = "current"           # verdict present, hash matches (not stale)
    STALE = "stale"               # verdict present, but judge ran against an older file version


def trajectory_render_state(row: dict) -> TrajectoryRenderState:
    """Return the canonical render state for the trajectory axis of a session row.

    Call this instead of writing null-checks inline. Covers all reachable states.
    judge_verdict=None covers both 'never judged' and 'judge errored' — both render as UNAVAILABLE.
    """
    if row["judge_verdict"] is None:
        return TrajectoryRenderState.UNAVAILABLE
    if row["judge_stale"]:
        return TrajectoryRenderState.STALE
    return TrajectoryRenderState.CURRENT


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION: int = 1

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id                    TEXT PRIMARY KEY,
    task_type                     TEXT NOT NULL,
    source_path                   TEXT NOT NULL,
    source_mtime                  REAL NOT NULL,
    source_hash                   TEXT NOT NULL,
    scored_at                     TEXT NOT NULL,
    axes_scored                   TEXT NOT NULL,
    real_tokens                   INTEGER NOT NULL,
    scope_status                  TEXT NOT NULL,
    baseline_available            INTEGER NOT NULL,
    p25                           INTEGER,
    p75                           INTEGER,
    median                        INTEGER,
    band_verdict                  TEXT NOT NULL,
    interpretation                TEXT NOT NULL,
    token_domain_of_validity      TEXT NOT NULL,
    baseline_source               TEXT NOT NULL DEFAULT 'b2_corpus',
    turn_count                    INTEGER,
    judge_verdict                 TEXT,
    judge_score                   REAL,
    judge_reasoning               TEXT,
    trajectory_domain_of_validity TEXT NOT NULL,
    judge_source_hash             TEXT,
    waste_event_count             INTEGER NOT NULL,
    waste_events                  TEXT NOT NULL,
    waste_domain_of_validity      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_scored_at ON sessions(scored_at);
CREATE INDEX IF NOT EXISTS idx_sessions_task_type ON sessions(task_type);
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_DIR = Path.home() / ".tes"
_DEFAULT_DB = _DEFAULT_DIR / "tes.db"


def resolve_db_path(path: Path | str | None = None) -> Path:
    """Canonical DB path resolution: explicit arg → TES_DB_PATH env var → ~/.tes/tes.db."""
    if path is not None:
        return Path(path)
    if env_val := os.environ.get("TES_DB_PATH"):
        return Path(env_val)
    return _DEFAULT_DB


def open_db(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (or create) the TES database.

    Path resolution: explicit arg → TES_DB_PATH env var → ~/.tes/tes.db.
    Raises RuntimeError on PermissionError or schema version mismatch.
    """
    db_path = resolve_db_path(path)

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise RuntimeError(
            f"Cannot create {db_path.parent} — check permissions or set TES_DB_PATH. ({exc})"
        ) from exc

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    existing_version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if existing_version == 0:
        # New database — apply schema and stamp version.
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    elif existing_version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"DB schema version {existing_version} is newer than this tool's "
            f"SCHEMA_VERSION={SCHEMA_VERSION}. Upgrade token-efficiency-scorer."
        )
    # existing_version == SCHEMA_VERSION → run additive migrations only.

    # Additive migration: turn_count was added after the initial schema shipped.
    # ALTER TABLE is safe to re-run guard: check column presence first.
    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
    }
    if "turn_count" not in existing_cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN turn_count INTEGER")
        conn.commit()

    if "baseline_source" not in existing_cols:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN baseline_source TEXT NOT NULL DEFAULT 'b2_corpus'"
        )
        conn.commit()

    cost_cols = {
        "session_cost_usd": "ALTER TABLE sessions ADD COLUMN session_cost_usd REAL",
        "cost_approximate": "ALTER TABLE sessions ADD COLUMN cost_approximate INTEGER",
        "cost_domain_of_validity": "ALTER TABLE sessions ADD COLUMN cost_domain_of_validity TEXT",
    }
    for col_name, alter_sql in cost_cols.items():
        if col_name not in existing_cols:
            conn.execute(alter_sql)
            conn.commit()

    # RR1: attribution fractions, persisted at score time so tes.intelligence
    # can cluster ANY scored session without re-reading its source JSONL --
    # see tes.attribution.attribution_fractions. NULL for every row scored
    # before this migration (additive, same pattern as the cost columns above);
    # tes.intelligence.features falls back to on-demand extraction for those.
    attribution_cols = {
        "context_resend_pct": "ALTER TABLE sessions ADD COLUMN context_resend_pct REAL",
        "context_growth_pct": "ALTER TABLE sessions ADD COLUMN context_growth_pct REAL",
        "output_pct": "ALTER TABLE sessions ADD COLUMN output_pct REAL",
        "waste_pct": "ALTER TABLE sessions ADD COLUMN waste_pct REAL",
    }
    for col_name, alter_sql in attribution_cols.items():
        if col_name not in existing_cols:
            conn.execute(alter_sql)
            conn.commit()

    return conn


def file_hash(path: Path | str) -> str:
    """Return sha256 hex digest of the file at path."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def needs_scoring(conn: sqlite3.Connection, session_id: str, current_hash: str) -> bool:
    """Return True if session_id is absent from the ledger or its hash has changed."""
    row = conn.execute(
        "SELECT source_hash FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return True
    return row["source_hash"] != current_hash


def upsert_session(
    conn: sqlite3.Connection,
    result: ThreeAxisResult,
    source_path: str,
    source_mtime: float,
    source_hash: str,
    turn_count: int = 0,
) -> None:
    """Write or update a session row using merge semantics.

    Merge rules:
    1. No existing row → INSERT all fields.
    2. New has judge → full UPDATE (judge_source_hash = source_hash).
    3. New has no judge AND existing has judge → partial UPDATE: preserve all
       judge fields (judge_verdict, judge_score, judge_reasoning,
       trajectory_domain_of_validity, judge_source_hash); update everything else.
    4. New has no judge AND existing has no judge → full UPDATE.

    turn_count: from the adapted record (adapt_session returns it at top level).
    """
    from datetime import datetime, timezone

    scored_at = datetime.now(timezone.utc).isoformat()
    has_judge = result.judge_verdict is not None

    axes: list[str] = ["token", "waste"]
    if has_judge:
        axes.append("judge")
    axes_json = json.dumps(axes)

    existing = conn.execute(
        "SELECT judge_verdict, judge_source_hash FROM sessions WHERE session_id = ?",
        (result.session_id,),
    ).fetchone()

    if existing is None:
        # Case 1: INSERT
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, task_type,
                source_path, source_mtime, source_hash, scored_at, axes_scored,
                real_tokens, scope_status, baseline_available,
                p25, p75, median, band_verdict, interpretation,
                token_domain_of_validity, baseline_source,
                judge_verdict, judge_score, judge_reasoning,
                trajectory_domain_of_validity, judge_source_hash,
                waste_event_count, waste_events, waste_domain_of_validity,
                turn_count,
                session_cost_usd, cost_approximate, cost_domain_of_validity,
                context_resend_pct, context_growth_pct, output_pct, waste_pct
            ) VALUES (
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?,
                ?, ?, ?,
                ?, ?, ?, ?
            )
            """,
            (
                result.session_id, result.task_type,
                source_path, source_mtime, source_hash, scored_at, axes_json,
                result.real_tokens, result.scope_status, int(result.baseline_available),
                result.p25, result.p75, result.median, result.band_verdict,
                result.interpretation, result.token_domain_of_validity,
                result.baseline_source,
                result.judge_verdict, result.judge_score, result.judge_reasoning,
                result.trajectory_domain_of_validity,
                source_hash if has_judge else None,
                result.waste_event_count, json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
                result.session_cost_usd, int(result.cost_approximate),
                result.cost_domain_of_validity or "",
                result.context_resend_pct, result.context_growth_pct,
                result.output_pct, result.waste_pct,
            ),
        )

    elif has_judge:
        # Case 2: full UPDATE — new judge data wins entirely.
        conn.execute(
            """
            UPDATE sessions SET
                task_type = ?,
                source_path = ?, source_mtime = ?, source_hash = ?,
                scored_at = ?, axes_scored = ?,
                real_tokens = ?, scope_status = ?, baseline_available = ?,
                p25 = ?, p75 = ?, median = ?, band_verdict = ?,
                interpretation = ?, token_domain_of_validity = ?,
                baseline_source = ?,
                judge_verdict = ?, judge_score = ?, judge_reasoning = ?,
                trajectory_domain_of_validity = ?, judge_source_hash = ?,
                waste_event_count = ?, waste_events = ?, waste_domain_of_validity = ?,
                turn_count = ?,
                session_cost_usd = ?, cost_approximate = ?, cost_domain_of_validity = ?,
                context_resend_pct = ?, context_growth_pct = ?, output_pct = ?, waste_pct = ?
            WHERE session_id = ?
            """,
            (
                result.task_type,
                source_path, source_mtime, source_hash, scored_at, axes_json,
                result.real_tokens, result.scope_status, int(result.baseline_available),
                result.p25, result.p75, result.median, result.band_verdict,
                result.interpretation, result.token_domain_of_validity,
                result.baseline_source,
                result.judge_verdict, result.judge_score, result.judge_reasoning,
                result.trajectory_domain_of_validity, source_hash,
                result.waste_event_count, json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
                result.session_cost_usd, int(result.cost_approximate),
                result.cost_domain_of_validity or "",
                result.context_resend_pct, result.context_growth_pct,
                result.output_pct, result.waste_pct,
                result.session_id,
            ),
        )

    elif existing["judge_verdict"] is not None:
        # Case 3: partial UPDATE — preserve existing judge columns.
        conn.execute(
            """
            UPDATE sessions SET
                task_type = ?,
                source_path = ?, source_mtime = ?, source_hash = ?,
                scored_at = ?, axes_scored = ?,
                real_tokens = ?, scope_status = ?, baseline_available = ?,
                p25 = ?, p75 = ?, median = ?, band_verdict = ?,
                interpretation = ?, token_domain_of_validity = ?,
                baseline_source = ?,
                waste_event_count = ?, waste_events = ?, waste_domain_of_validity = ?,
                turn_count = ?,
                session_cost_usd = ?, cost_approximate = ?, cost_domain_of_validity = ?,
                context_resend_pct = ?, context_growth_pct = ?, output_pct = ?, waste_pct = ?
            WHERE session_id = ?
            """,
            (
                result.task_type,
                source_path, source_mtime, source_hash, scored_at, axes_json,
                result.real_tokens, result.scope_status, int(result.baseline_available),
                result.p25, result.p75, result.median, result.band_verdict,
                result.interpretation, result.token_domain_of_validity,
                result.baseline_source,
                result.waste_event_count, json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
                result.session_cost_usd, int(result.cost_approximate),
                result.cost_domain_of_validity or "",
                result.context_resend_pct, result.context_growth_pct,
                result.output_pct, result.waste_pct,
                result.session_id,
            ),
        )

    else:
        # Case 4: full UPDATE, no judge on either side.
        conn.execute(
            """
            UPDATE sessions SET
                task_type = ?,
                source_path = ?, source_mtime = ?, source_hash = ?,
                scored_at = ?, axes_scored = ?,
                real_tokens = ?, scope_status = ?, baseline_available = ?,
                p25 = ?, p75 = ?, median = ?, band_verdict = ?,
                interpretation = ?, token_domain_of_validity = ?,
                baseline_source = ?,
                judge_verdict = ?, judge_score = ?, judge_reasoning = ?,
                trajectory_domain_of_validity = ?, judge_source_hash = ?,
                waste_event_count = ?, waste_events = ?, waste_domain_of_validity = ?,
                turn_count = ?,
                session_cost_usd = ?, cost_approximate = ?, cost_domain_of_validity = ?,
                context_resend_pct = ?, context_growth_pct = ?, output_pct = ?, waste_pct = ?
            WHERE session_id = ?
            """,
            (
                result.task_type,
                source_path, source_mtime, source_hash, scored_at, axes_json,
                result.real_tokens, result.scope_status, int(result.baseline_available),
                result.p25, result.p75, result.median, result.band_verdict,
                result.interpretation, result.token_domain_of_validity,
                result.baseline_source,
                None, None, None,
                result.trajectory_domain_of_validity, None,
                result.waste_event_count, json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
                result.session_cost_usd, int(result.cost_approximate),
                result.cost_domain_of_validity or "",
                result.context_resend_pct, result.context_growth_pct,
                result.output_pct, result.waste_pct,
                result.session_id,
            ),
        )

    conn.commit()


def _count_turns_from_jsonl(source_path: str) -> int | None:
    """Return turn count for a session JSONL file, or None if unreadable.

    Counts assistant messages and substantive user messages (either plain-text
    or tool-result content), ignoring sidechain messages.  Mirrors the logic
    used in adapt_session() but is inlined here to avoid coupling to adapt.py.
    """
    import json as _json

    p = Path(source_path)
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        turn_count = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                msg = _json.loads(line)
            except Exception:
                continue
            if msg.get("isSidechain"):
                continue
            msg_type = msg.get("type", "")
            if msg_type == "assistant":
                turn_count += 1
            elif msg_type == "user":
                content = msg.get("message", {}).get("content", "")
                if isinstance(content, str) and content.strip():
                    turn_count += 1
                elif isinstance(content, list):
                    has_tr = any(
                        isinstance(x, dict) and x.get("type") == "tool_result"
                        for x in content
                    )
                    if has_tr:
                        turn_count += 1
        return turn_count
    except Exception:
        return None


def backfill_turn_counts(db_path: Path | str) -> dict[str, int]:
    """Populate turn_count for all sessions where it is currently NULL.

    Reads each session's source JSONL file and counts turns using the same
    logic as adapt_session().  Returns a summary dict:
        {"updated": N, "missing_source": M, "errors": E}

    UU2: db_path is required, not defaulted. Currently unreferenced by any
    caller (found during the UU2 write-path audit) -- hardened for
    consistency with the rest of that audit rather than left as the one
    exception, in case a future caller reaches for it.
    """
    conn = open_db(db_path)
    rows = conn.execute(
        "SELECT session_id, source_path FROM sessions WHERE turn_count IS NULL AND source_path IS NOT NULL"
    ).fetchall()

    updated = 0
    missing = 0
    errors = 0

    for session_id, source_path in rows:
        tc = _count_turns_from_jsonl(source_path)
        if tc is None:
            missing += 1
        else:
            try:
                conn.execute(
                    "UPDATE sessions SET turn_count = ? WHERE session_id = ?",
                    (tc, session_id),
                )
                updated += 1
            except Exception:
                errors += 1

    conn.commit()
    conn.close()
    return {"updated": updated, "missing_source": missing, "errors": errors}


def backfill_waste(
    db_path: Path | str | None = None,
    prices: dict | None = None,
) -> dict[str, int]:
    """Re-run frozen detectors on all accessible sessions; embed per-event costs.

    Safe to call repeatedly (hash-independent; fixes the stale-zeros bug where sessions
    scored before waste detection was wired show waste_event_count=0 in the store).

    Returns summary: {"updated": N, "no_waste": M, "missing_source": K, "errors": E}
    where "updated" = sessions that had >= 1 waste event written, "no_waste" = sessions
    processed with 0 detected events, "missing_source" = source file not accessible.
    """
    from tes.adapt import adapt_session
    from tes.cost import compute_session_cost, load_price_table
    from tes._digest import reconstruct_digest
    from tes.waste import annotate_waste_costs, build_waste_entry

    if prices is None:
        prices = load_price_table()

    conn = open_db(db_path)
    rows = conn.execute(
        "SELECT session_id, source_path FROM sessions WHERE source_path IS NOT NULL"
    ).fetchall()

    updated = 0
    no_waste = 0
    missing = 0
    errors = 0

    for row in rows:
        session_id: str = row["session_id"]
        p = Path(row["source_path"])
        if not p.exists():
            missing += 1
            continue
        try:
            record = adapt_session(p)
            turns: list[dict] = record.get("digest", {}).get("turns", [])
            waste_entry = build_waste_entry(session_id, turns)

            per_turn_cost: dict[int, float] = {}
            try:
                digest = reconstruct_digest(record.get("digest", {}))
                sc = compute_session_cost(digest, prices)
                per_turn_cost = {tc.turn_index: tc.total_usd for tc in sc.turn_costs}
            except Exception:
                pass  # cost failure → wasted_cost_usd will be 0 for all events

            waste_events = waste_entry["waste_events"]
            annotate_waste_costs(waste_events, per_turn_cost)

            count = len(waste_events)
            conn.execute(
                "UPDATE sessions SET waste_event_count = ?, waste_events = ? WHERE session_id = ?",
                (count, json.dumps(waste_events), session_id),
            )
            conn.commit()

            if count > 0:
                updated += 1
            else:
                no_waste += 1
        except Exception:
            errors += 1

    conn.close()
    return {"updated": updated, "no_waste": no_waste, "missing_source": missing, "errors": errors}


def backfill_cost(
    db_path: Path | str | None = None,
    prices: dict | None = None,
) -> dict[str, int]:
    """Populate session_cost_usd for sessions currently missing it.

    Re-adapts from source JSONL (not stored digest) so model strings and
    cache_creation tokens are available. Returns summary dict:
      {"updated": N, "missing_source": M, "errors": E, "approximate": A}
    """
    from tes.adapt import adapt_session
    from tes.cost import compute_session_cost, load_price_table
    from tes._digest import reconstruct_digest

    if prices is None:
        prices = load_price_table()

    conn = open_db(db_path)
    rows = conn.execute(
        "SELECT session_id, source_path FROM sessions "
        "WHERE session_cost_usd IS NULL AND source_path IS NOT NULL"
    ).fetchall()

    updated = missing = errors = approximate = 0

    for session_id, source_path in rows:
        p = Path(source_path)
        if not p.exists():
            missing += 1
            continue
        try:
            record = adapt_session(p)
            digest = reconstruct_digest(record["digest"])
            session_cost = compute_session_cost(digest, prices)
            conn.execute(
                "UPDATE sessions SET "
                "  session_cost_usd = ?, cost_approximate = ?, cost_domain_of_validity = ? "
                "WHERE session_id = ?",
                (
                    session_cost.total_usd,
                    int(session_cost.approximate),
                    session_cost.domain_of_validity,
                    session_id,
                ),
            )
            conn.commit()
            updated += 1
            if session_cost.approximate:
                approximate += 1
        except Exception:
            errors += 1

    conn.close()
    return {"updated": updated, "missing_source": missing, "errors": errors, "approximate": approximate}


def _deserialize_row(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict with JSON fields decoded."""
    d = dict(row)
    d["waste_events"] = json.loads(d["waste_events"])
    d["axes_scored"] = json.loads(d["axes_scored"])
    d["baseline_available"] = bool(d["baseline_available"])
    d["judge_stale"] = bool(
        d["judge_verdict"]
        and d["judge_source_hash"]
        and d["judge_source_hash"] != d["source_hash"]
    )
    d["cost_approximate"] = bool(d.get("cost_approximate", 0))
    return d


def get_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Return full session row as dict, or None if not found."""
    row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    return _deserialize_row(row)


# Mapping from public sort-key names (used in ?sort= query params) to DB column names.
# ONLY these columns are allowed — prevents SQL injection via user-controlled sort params.
_SORT_COLUMN_WHITELIST: dict[str, str] = {
    "date": "scored_at",
    "cost": "session_cost_usd",
    "waste": "waste_event_count",
    "tokens": "real_tokens",
    "verdict": "band_verdict",
}


def list_sessions(
    conn: sqlite3.Connection,
    limit: int = 100,
    offset: int = 0,
    order_by: str = "date",
    direction: str = "DESC",
) -> list[dict]:
    """Return sessions ordered by the requested column.

    order_by must be a key in _SORT_COLUMN_WHITELIST; unknown keys fall back to 'date'.
    direction must be 'ASC' or 'DESC'; anything else falls back to 'DESC'.
    """
    col = _SORT_COLUMN_WHITELIST.get(order_by, "scored_at")
    dir_safe = "ASC" if direction.upper() == "ASC" else "DESC"
    rows = conn.execute(
        f"SELECT * FROM sessions ORDER BY {col} {dir_safe} NULLS LAST LIMIT ? OFFSET ?",  # noqa: S608
        (limit, offset),
    ).fetchall()
    return [_deserialize_row(r) for r in rows]


__all__ = [
    "SCHEMA_VERSION",
    "TrajectoryRenderState",
    "trajectory_render_state",
    "resolve_db_path",
    "open_db",
    "file_hash",
    "needs_scoring",
    "upsert_session",
    "backfill_turn_counts",
    "backfill_waste",
    "backfill_cost",
    "get_session",
    "list_sessions",
]
