from __future__ import annotations

"""export-contribution must never write a legacy (pre-0.15, overcounted ~2x) row into a
contribution file, and must say how many it left out. Synthetic mixed store only."""

import json
from pathlib import Path

import pytest
import tes.cli as cli
from tes.contribution import build_contribution_payload
from tes.store import open_db

from tests.legacy_store import MixedStore, build_mixed_store, insert_row


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr("sys.argv", ["tes", *argv])
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    with pytest.raises(SystemExit):
        cli.main()


def _count_legacy(s: MixedStore) -> int:
    return 4  # rescorable x2, unrecoverable, unparseable (see build_mixed_store)


def test_payload_excludes_legacy_rows_and_counts_them(tmp_path: Path) -> None:
    s = build_mixed_store(tmp_path)
    conn = open_db(s.db)
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)
    conn.close()
    assert payload.manifest.row_count == len(s.current) == 2
    assert payload.manifest.legacy_rows_excluded == _count_legacy(s)
    # the legacy stored value must not appear anywhere in the payload
    assert all(r["real_tokens"] != s.legacy_tokens for r in payload.rows)


def test_cli_preview_and_file_leave_legacy_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    s = build_mixed_store(tmp_path)
    out = tmp_path / "c.jsonl"
    _run(
        monkeypatch,
        ["export-contribution", "--anonymous", "--db-path", str(s.db), "--output", str(out)],
    )
    text = capsys.readouterr().out
    assert "4 legacy session(s) left out" in text
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert all(r["real_tokens"] != s.legacy_tokens for r in rows)


def test_cli_all_legacy_store_exports_nothing_and_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "old.db"
    conn = open_db(db)
    insert_row(conn, "old-1", adapter_version=None, cost_version=None)
    conn.close()
    out = tmp_path / "c.jsonl"
    _run(
        monkeypatch,
        ["export-contribution", "--anonymous", "--db-path", str(db), "--output", str(out)],
    )
    assert "1 legacy session(s) were left out" in capsys.readouterr().out
    assert not out.exists()
