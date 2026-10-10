from __future__ import annotations

"""Synthetic stores for the legacy-row (upgrade path) tests. No real data: the transcript is the
committed synthetic fixture and every number is hard-coded."""

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from tes.adapt import ADAPTER_VERSION, COST_VERSION
from tes.store import open_db

FIXTURE = Path(__file__).parent / "fixtures" / "usage_dedupe" / "multi_block_session.jsonl"

_INSERT = (
    "INSERT INTO sessions (session_id, task_type, source_path, source_mtime, source_hash, "
    "scored_at, axes_scored, real_tokens, scope_status, baseline_available, band_verdict, "
    "interpretation, token_domain_of_validity, trajectory_domain_of_validity, "
    "waste_event_count, waste_events, waste_domain_of_validity, turn_count, "
    "session_cost_usd, adapter_version, cost_version) "
    "VALUES (?, ?, ?, ?, 'h', 't', '[]', ?, 'in_scope', 1, 'within_band', 'i', 'd', 'd', "
    "0, '[]', 'd', 80, ?, ?, ?)"
)


def insert_row(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    source: str = "gone.jsonl",
    tokens: int = 100_000,
    cost_usd: float | None = 10.0,
    adapter_version: int | None = ADAPTER_VERSION,
    cost_version: int | None = COST_VERSION,
    mtime: float = 1_000.0,
    task_type: str = "ml-eval",
) -> None:
    """Insert one scored row with explicit accounting versions (None = pre-column row)."""
    conn.execute(
        _INSERT,
        (session_id, task_type, source, mtime, tokens, cost_usd, adapter_version, cost_version),
    )
    conn.commit()


@dataclass(frozen=True)
class MixedStore:
    """A store holding every kind of row the upgrade path must handle."""

    db: Path
    transcript: Path  # a real, parseable transcript (shared by the rescorable rows)

    # session ids by kind
    rescorable_pre_dedupe: str = "pre-dedupe-with-source"
    rescorable_cost_only: str = "cost-stale-with-source"  # adapter current, cost_version NULL
    unrecoverable: str = "pre-dedupe-source-gone"
    unparseable: str = "pre-dedupe-bad-source"
    current: tuple[str, ...] = ("current-1", "current-2")

    # stored (deliberately wrong) values of the legacy rows
    legacy_tokens: int = 999_999
    legacy_cost_usd: float = 99.0


def build_mixed_store(tmp_path: Path, *, now: float = 2_000_000_000.0) -> MixedStore:
    """Create `tmp_path/mixed.db` with 2 current, 2 rescorable, 1 unparseable, 1 gone-source rows.

    All rows were last written at ``now`` (so they fall inside any recent window).
    """
    transcript = tmp_path / "proj" / "sess-real.jsonl"
    transcript.parent.mkdir(exist_ok=True)
    shutil.copy(FIXTURE, transcript)
    bad = tmp_path / "proj" / "sess-bad.jsonl"
    bad.write_bytes(b"\x00\x01 not a transcript\n")

    s = MixedStore(db=tmp_path / "mixed.db", transcript=transcript)
    conn = open_db(s.db)
    insert_row(
        conn,
        s.rescorable_pre_dedupe,
        source=str(transcript),
        tokens=s.legacy_tokens,
        cost_usd=s.legacy_cost_usd,
        adapter_version=None,
        cost_version=None,
        mtime=now,
    )
    insert_row(
        conn,
        s.rescorable_cost_only,
        source=str(transcript),
        tokens=s.legacy_tokens,
        cost_usd=s.legacy_cost_usd,
        adapter_version=ADAPTER_VERSION,
        cost_version=None,
        mtime=now,
    )
    insert_row(
        conn,
        s.unrecoverable,
        source=str(tmp_path / "expired" / "sess.jsonl"),
        tokens=s.legacy_tokens,
        cost_usd=s.legacy_cost_usd,
        adapter_version=None,
        cost_version=None,
        mtime=now,
    )
    insert_row(
        conn,
        s.unparseable,
        source=str(bad),
        tokens=s.legacy_tokens,
        cost_usd=s.legacy_cost_usd,
        adapter_version=None,
        cost_version=None,
        mtime=now,
    )
    for i, sid in enumerate(s.current):
        insert_row(conn, sid, tokens=100_000 + i, cost_usd=10.0 + i, mtime=now)
    conn.close()
    return s
