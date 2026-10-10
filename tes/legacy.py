from __future__ import annotations

"""tes/legacy.py -- which stored rows predate the current accounting, and what to do about them.

A row is LEGACY when it was written before usage de-duplication (``adapter_version`` NULL or
< ``tes.adapt.ADAPTER_VERSION``: real_tokens and cost counted ~2.4x too high) or before 1-hour
cache writes were priced (``cost_version`` NULL or < ``tes.adapt.COST_VERSION``). Every store
written by 0.14 or earlier has neither column, so all of its rows are legacy.

LEGACY is DERIVED from those two version columns, never stored as a third flag: a flag could
drift from the versions it summarises, and `tes rescore` clears legacy-ness by writing the
current versions, which a derived predicate picks up for free. Nothing here ever deletes a row
or rewrites a stored number; legacy rows are *excluded from corrected figures* (baselines, alarm
pool, budget, cost/ROI) and counted, so the exclusion is always visible.

A store missing either column fails closed: every row is legacy.
"""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Label used wherever a historical (uncorrected) dollar figure is still shown.
LEGACY_LABEL = "legacy (pre-0.15 accounting, overcounted ~2x)"


def _versions() -> tuple[int, int]:
    from tes.adapt import ADAPTER_VERSION, COST_VERSION  # noqa: PLC0415 -- import-light module

    return ADAPTER_VERSION, COST_VERSION


def _has_version_columns(conn: sqlite3.Connection) -> bool:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    return {"adapter_version", "cost_version"} <= cols


def current_clause(conn: sqlite3.Connection, alias: str = "") -> tuple[str, tuple[int, ...]]:
    """SQL boolean (no leading AND) that is true for rows from the current accounting."""
    if not _has_version_columns(conn):
        return "0", ()
    p = f"{alias}." if alias else ""
    adapter, cost = _versions()
    return f"({p}adapter_version >= ? AND {p}cost_version >= ?)", (adapter, cost)


def legacy_clause(conn: sqlite3.Connection, alias: str = "") -> tuple[str, tuple[int, ...]]:
    """SQL boolean that is true for LEGACY rows (NULL versions included)."""
    if not _has_version_columns(conn):
        return "1", ()
    p = f"{alias}." if alias else ""
    adapter, cost = _versions()
    return f"COALESCE({p}adapter_version >= ? AND {p}cost_version >= ?, 0) = 0", (adapter, cost)


def is_legacy_row(row: Mapping[str, Any]) -> bool:
    """Python twin of :func:`legacy_clause` for an already-fetched row dict."""
    adapter, cost = _versions()
    a, c = row.get("adapter_version"), row.get("cost_version")
    return a is None or c is None or a < adapter or c < cost


def count_legacy(conn: sqlite3.Connection) -> int:
    """Number of legacy rows in the store."""
    sql, params = legacy_clause(conn)
    return int(conn.execute(f"SELECT COUNT(*) FROM sessions WHERE {sql}", params).fetchone()[0])  # noqa: S608


def legacy_sources(conn: sqlite3.Connection) -> list[tuple[str, str | None]]:
    """(session_id, source_path) of every legacy row, most recently written first."""
    sql, params = legacy_clause(conn)
    rows = conn.execute(
        f"SELECT session_id, source_path FROM sessions WHERE {sql} "  # noqa: S608
        "ORDER BY source_mtime DESC, session_id",
        params,
    ).fetchall()
    return [(str(r[0]), r[1]) for r in rows]


def source_readable(source_path: str | None) -> bool:
    """True when the transcript still exists as a readable regular file."""
    if not source_path:
        return False
    try:
        p = Path(source_path)
        if not p.is_file():
            return False
        p.stat()
        with p.open("rb") as fh:
            fh.read(1)
    except OSError:
        return False
    return True


@dataclass(frozen=True)
class LegacyCensus:
    """Counts of legacy rows and whether `tes rescore` can recover them."""

    total: int
    legacy: int
    rescorable: int  # source transcript still readable
    unrecoverable: int  # source transcript gone: stays legacy, excluded from corrected figures


def census(conn: sqlite3.Connection, *, check_sources: bool = True) -> LegacyCensus:
    """Count rows, legacy rows and (optionally) how many legacy rows are rescorable."""
    total = int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
    if not check_sources:
        legacy = count_legacy(conn)
        return LegacyCensus(total, legacy, 0, 0)
    sources = legacy_sources(conn)
    rescorable = sum(1 for _, path in sources if source_readable(path))
    return LegacyCensus(total, len(sources), rescorable, len(sources) - rescorable)


__all__ = [
    "LEGACY_LABEL",
    "LegacyCensus",
    "census",
    "count_legacy",
    "current_clause",
    "is_legacy_row",
    "legacy_clause",
    "legacy_sources",
    "source_readable",
]
