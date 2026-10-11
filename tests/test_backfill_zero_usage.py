from __future__ import annotations

"""Regression: `backfill-waste` (and anything sharing its refresh) must never overwrite a stored
row from a zero-usage or unreadable transcript. `adapt_session` tolerates such a file by
returning zero usage, so a refresh that trusted it wrote zeros over the stored numbers and
stamped the row current. Same rule as `tes rescore`: count it as failed, leave the row alone."""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tes import cli
from tes.store import backfill_cost, backfill_waste, open_db

from tests.legacy_store import MixedStore, build_mixed_store, insert_row


def _dump(db: Path) -> dict[str, tuple[Any, ...]]:
    conn = sqlite3.connect(db)
    rows = {r[0]: r for r in conn.execute("SELECT * FROM sessions ORDER BY session_id")}
    conn.close()
    return rows


@pytest.fixture
def mixed(tmp_path: Path) -> MixedStore:
    return build_mixed_store(tmp_path)


def test_backfill_waste_leaves_a_row_with_an_unreadable_transcript_unchanged(
    mixed: MixedStore,
) -> None:
    before = _dump(mixed.db)
    summary = backfill_waste(mixed.db)
    after = _dump(mixed.db)
    assert after[mixed.unparseable] == before[mixed.unparseable]
    assert summary["errors"] == 1
    assert summary["refreshed"] == 2  # the two rows with a real transcript
    for sid in (mixed.rescorable_pre_dedupe, mixed.rescorable_cost_only):
        assert after[sid] != before[sid]  # still refreshed from the readable transcript


@pytest.mark.parametrize("content", [b"", b"\x00\x01 not a transcript\n", b"{}\n", b'{"a": 1}\n\n'])
def test_a_current_row_is_not_rewritten_from_a_zero_usage_transcript(
    tmp_path: Path, content: bytes
) -> None:
    """A row already at the current versions: nothing to refresh, but the waste write must not
    replace its waste columns with the (empty) result of a transcript that has no usage."""
    src = tmp_path / "now-empty.jsonl"
    src.write_bytes(content)
    db = tmp_path / "c.db"
    conn = open_db(db)
    insert_row(conn, "cur", source=str(src), tokens=123_456, cost_usd=7.5)
    conn.execute("UPDATE sessions SET waste_event_count = 3, waste_events = '[{\"k\": 1}]'")
    conn.commit()
    conn.close()
    before = _dump(db)
    summary = backfill_waste(db)
    assert _dump(db) == before
    assert summary["errors"] == 1
    assert summary["no_waste"] == 0 and summary["updated"] == 0


def test_backfill_waste_cli_counts_rescored_skipped_failed_and_exits_4(
    mixed: MixedStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["tes", "backfill-waste", "--db-path", str(mixed.db)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    out = capsys.readouterr().out
    assert exc.value.code == 4  # docs/EXIT_CODES.md: a readable row failed (same as score/rescore)
    assert "rescored: 2" in out and "skipped (source missing): 3" in out and "failed: 1" in out


def test_backfill_waste_cli_exits_0_when_nothing_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "ok.db"
    conn = open_db(db)
    insert_row(conn, "gone", source=str(tmp_path / "gone.jsonl"))
    conn.close()
    monkeypatch.setattr("sys.argv", ["tes", "backfill-waste", "--db-path", str(db)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert "failed: 0" in capsys.readouterr().out


def test_backfill_cost_does_not_write_a_zero_cost_from_an_unreadable_transcript(
    tmp_path: Path,
) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b"\x00\x01 not a transcript\n")
    db = tmp_path / "bc.db"
    conn = open_db(db)
    insert_row(conn, "nocost", source=str(bad), cost_usd=None)
    conn.close()
    summary = backfill_cost(db)
    conn = open_db(db)
    row = conn.execute("SELECT session_cost_usd FROM sessions WHERE session_id='nocost'").fetchone()
    assert row["session_cost_usd"] is None  # unknown stays unknown, never a placeholder 0.0
    assert summary["errors"] == 1 and summary["updated"] == 0
