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
    judge_verdict                 TEXT,
    judge_score                   REAL,
    judge_reasoning               TEXT,
    trajectory_domain_of_validity TEXT NOT NULL,
    judge_source_hash             TEXT,
    waste_event_count             INTEGER NOT NULL,
    waste_events                  TEXT NOT NULL,
    waste_domain_of_validity      TEXT NOT NULL,
    turn_count                    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_sessions_scored_at ON sessions(scored_at);
CREATE INDEX IF NOT EXISTS idx_sessions_task_type ON sessions(task_type);
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_DIR = Path.home() / ".tes"
_DEFAULT_DB = _DEFAULT_DIR / "tes.db"


def open_db(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (or create) the TES database.

    Path resolution: explicit arg → TES_DB_PATH env var → ~/.tes/tes.db.
    Raises RuntimeError on PermissionError or schema version mismatch.
    """
    if path is not None:
        db_path = Path(path)
    elif env_val := os.environ.get("TES_DB_PATH"):
        db_path = Path(env_val)
    else:
        db_path = _DEFAULT_DB

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
                token_domain_of_validity,
                judge_verdict, judge_score, judge_reasoning,
                trajectory_domain_of_validity, judge_source_hash,
                waste_event_count, waste_events, waste_domain_of_validity,
                turn_count
            ) VALUES (
                ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?
            )
            """,
            (
                result.session_id, result.task_type,
                source_path, source_mtime, source_hash, scored_at, axes_json,
                result.real_tokens, result.scope_status, int(result.baseline_available),
                result.p25, result.p75, result.median, result.band_verdict,
                result.interpretation, result.token_domain_of_validity,
                result.judge_verdict, result.judge_score, result.judge_reasoning,
                result.trajectory_domain_of_validity,
                source_hash if has_judge else None,
                result.waste_event_count, json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
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
                judge_verdict = ?, judge_score = ?, judge_reasoning = ?,
                trajectory_domain_of_validity = ?, judge_source_hash = ?,
                waste_event_count = ?, waste_events = ?, waste_domain_of_validity = ?,
                turn_count = ?
            WHERE session_id = ?
            """,
            (
                result.task_type,
                source_path, source_mtime, source_hash, scored_at, axes_json,
                result.real_tokens, result.scope_status, int(result.baseline_available),
                result.p25, result.p75, result.median, result.band_verdict,
                result.interpretation, result.token_domain_of_validity,
                result.judge_verdict, result.judge_score, result.judge_reasoning,
                result.trajectory_domain_of_validity, source_hash,
                result.waste_event_count, json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
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
                waste_event_count = ?, waste_events = ?, waste_domain_of_validity = ?,
                turn_count = ?
            WHERE session_id = ?
            """,
            (
                result.task_type,
                source_path, source_mtime, source_hash, scored_at, axes_json,
                result.real_tokens, result.scope_status, int(result.baseline_available),
                result.p25, result.p75, result.median, result.band_verdict,
                result.interpretation, result.token_domain_of_validity,
                result.waste_event_count, json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
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
                judge_verdict = ?, judge_score = ?, judge_reasoning = ?,
                trajectory_domain_of_validity = ?, judge_source_hash = ?,
                waste_event_count = ?, waste_events = ?, waste_domain_of_validity = ?,
                turn_count = ?
            WHERE session_id = ?
            """,
            (
                result.task_type,
                source_path, source_mtime, source_hash, scored_at, axes_json,
                result.real_tokens, result.scope_status, int(result.baseline_available),
                result.p25, result.p75, result.median, result.band_verdict,
                result.interpretation, result.token_domain_of_validity,
                None, None, None,
                result.trajectory_domain_of_validity, None,
                result.waste_event_count, json.dumps(result.waste_events),
                result.waste_domain_of_validity,
                turn_count,
                result.session_id,
            ),
        )

    conn.commit()


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
    return d


def get_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Return full session row as dict, or None if not found."""
    row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    return _deserialize_row(row)


def list_sessions(
    conn: sqlite3.Connection,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Return sessions ordered by scored_at DESC."""
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY scored_at DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [_deserialize_row(r) for r in rows]


__all__ = [
    "SCHEMA_VERSION",
    "TrajectoryRenderState",
    "trajectory_render_state",
    "open_db",
    "file_hash",
    "needs_scoring",
    "upsert_session",
    "get_session",
    "list_sessions",
]
