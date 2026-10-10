from __future__ import annotations

"""Empty-stub legacy rows (0 turns, 0 real tokens, a transcript with no usage) can never be
re-scored; `tes rescore` skips them and stamps them current WITHOUT touching a number, so the
command can exit 0. A usage-zero transcript under a row that DID have turns or tokens, and a
garbage source under a real row, are still failures."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tes import cli
from tes.adapt import ADAPTER_VERSION, COST_VERSION
from tes.legacy import count_legacy
from tes.store import backfill_waste, open_db

from tests.legacy_store import MixedStore, build_mixed_store, insert_row

STUB = "stub-empty-transcript"
STUB_GARBAGE_SOURCE = "stub-garbage-source"
ZERO_USAGE_WITH_TURNS = "zero-usage-80-turns"


@pytest.fixture
def store(tmp_path: Path) -> MixedStore:
    """The standard mixed store plus a stub row and a usage-zero row that had turns."""
    s = build_mixed_store(tmp_path)
    empty = tmp_path / "proj" / "sess-empty.jsonl"
    empty.write_bytes(b"")
    conn = open_db(s.db)
    insert_row(conn, STUB, source=str(empty), tokens=0, cost_usd=0.0, adapter_version=None,
               cost_version=None)  # fmt: skip
    insert_row(conn, ZERO_USAGE_WITH_TURNS, source=str(empty), tokens=0, cost_usd=0.0,
               adapter_version=None, cost_version=None)  # fmt: skip
    conn.execute("UPDATE sessions SET turn_count = 0 WHERE session_id = ?", (STUB,))
    conn.commit()
    conn.close()
    return s


def _row(db: Path, sid: str) -> dict[str, Any]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (sid,)).fetchone()
    conn.close()
    return dict(r)


def test_stub_is_skipped_and_stamped_current_without_touching_a_number(store: MixedStore) -> None:
    before = _row(store.db, STUB)
    summary = backfill_waste(store.db, only_legacy=True)
    after = _row(store.db, STUB)
    assert summary["skipped_stub"] == 1
    assert (after["adapter_version"], after["cost_version"]) == (ADAPTER_VERSION, COST_VERSION)
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"adapter_version", "cost_version"}  # every number is byte-identical


def test_rows_with_turns_or_unreadable_sources_still_fail_and_stay_legacy(
    store: MixedStore,
) -> None:
    summary = backfill_waste(store.db, only_legacy=True)
    # the garbage-source real row and the usage-zero row that had 80 turns
    assert summary["errors"] == 2
    conn = open_db(store.db)
    assert count_legacy(conn) == 3  # unrecoverable + unparseable + zero-usage-with-turns
    assert _row(store.db, ZERO_USAGE_WITH_TURNS)["adapter_version"] is None


def test_dry_run_counts_the_stub_but_writes_nothing(store: MixedStore) -> None:
    before = _row(store.db, STUB)
    summary = backfill_waste(store.db, only_legacy=True, dry_run=True)
    assert summary["skipped_stub"] == 1
    assert _row(store.db, STUB) == before


def test_second_run_is_idempotent_and_stub_is_no_longer_legacy(store: MixedStore) -> None:
    backfill_waste(store.db, only_legacy=True)
    summary = backfill_waste(store.db, only_legacy=True)
    assert summary["skipped_stub"] == 0
    assert summary["legacy_rows"] == 3


def test_cli_exit_zero_when_the_only_other_legacy_rows_are_stubs_or_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    db = tmp_path / "stubs.db"
    conn = open_db(db)
    insert_row(conn, "s1", source=str(empty), tokens=0, cost_usd=0.0, adapter_version=None,
               cost_version=None)  # fmt: skip
    conn.execute("UPDATE sessions SET turn_count = 0")
    conn.commit()
    conn.close()

    monkeypatch.setattr("sys.argv", ["tes", "rescore", "--json", "--db-path", str(db)])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    doc = json.loads(capsys.readouterr().out)
    assert code == 0
    assert (doc["failed"], doc["skipped_empty_stub"], doc["remaining_legacy"]) == (0, 1, 0)

    monkeypatch.setattr("sys.argv", ["tes", "rescore", "--db-path", str(db)])
    try:
        cli.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr().out
    assert code == 0  # a second run: nothing legacy left
    assert "Legacy sessions: 0" in out
