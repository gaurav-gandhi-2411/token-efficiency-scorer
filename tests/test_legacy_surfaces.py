from __future__ import annotations

"""Legacy rows (scored before 0.15) on `impact`, `patterns` and `ask`: their aggregates leave
those rows out and say how many. Runs on the synthetic mixed store (tests/legacy_store.py)."""

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from tes import cli
from tes.intelligence.chat import _build_user_message, build_chat_context
from tes.legacy import LEGACY_ROW_LABEL
from tes.store import open_db

from tests.legacy_store import MixedStore, build_mixed_store, insert_row

# edit operations: the current rows carry small, known numbers; the legacy rows carry numbers that
# must never show up in a figure
_CURRENT_OPS = json.dumps([{"path": "src/a.py", "additions": 10, "deletions": 2}])
_LEGACY_OPS = json.dumps([{"path": "src/legacy_only.py", "additions": 5000, "deletions": 4000}])


def _run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.argv", ["tes", "--quiet", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def mixed(tmp_path: Path) -> MixedStore:
    s = build_mixed_store(tmp_path)
    conn = sqlite3.connect(s.db)
    for sid in s.current:
        conn.execute(
            "UPDATE sessions SET edit_operations = ? WHERE session_id = ?", (_CURRENT_OPS, sid)
        )
    conn.execute(
        "UPDATE sessions SET edit_operations = ? WHERE adapter_version IS NULL "
        "OR cost_version IS NULL",
        (_LEGACY_OPS,),
    )
    conn.commit()
    conn.close()
    return s


# ----------------------------------------------------------------------------- impact


def test_impact_json_leaves_legacy_rows_out_and_counts_them(
    mixed: MixedStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(monkeypatch, capsys, "impact", "--json", "--db-path", str(mixed.db))
    doc = json.loads(out)
    assert code == 0
    assert doc["legacy_rows_excluded"] == 4
    assert doc["sessions_with_data"] == 2
    assert doc["total_operations"] == 2
    assert doc["total_additions"] == 20 and doc["total_deletions"] == 4
    assert [f["path"] for f in doc["top_files"]] == ["src/a.py"]
    assert doc["schema_version"] == 1


def test_impact_text_says_how_many_legacy_rows_were_left_out(
    mixed: MixedStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = _run(monkeypatch, capsys, "impact", "--db-path", str(mixed.db))
    assert "4 legacy sessions left out" in out
    assert LEGACY_ROW_LABEL in out
    assert "legacy_only.py" not in out


def test_impact_all_legacy_store_reports_nothing_but_the_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "all_legacy.db"
    conn = open_db(db)
    insert_row(conn, "old", adapter_version=None, cost_version=None)
    conn.execute("UPDATE sessions SET edit_operations = ?", (_LEGACY_OPS,))
    conn.commit()
    conn.close()
    _, out, _ = _run(monkeypatch, capsys, "impact", "--json", "--db-path", str(db))
    doc = json.loads(out)
    assert doc["legacy_rows_excluded"] == 1 and doc["sessions_with_data"] == 0
    _, text, _ = _run(monkeypatch, capsys, "impact", "--db-path", str(db))
    assert "1 legacy session left out" in text


# ----------------------------------------------------------------------------- patterns


def _pattern_store(tmp_path: Path, n_current: int = 34, n_legacy: int = 3) -> Path:
    """n_current rows with persisted attribution fractions, plus legacy rows with extreme ones."""
    db = tmp_path / "patterns.db"
    conn = open_db(db)
    for i in range(n_current):
        insert_row(conn, f"cur-{i:03d}", tokens=100_000 + 1_000 * i, task_type="ml-eval")
        conn.execute(
            "UPDATE sessions SET context_resend_pct = ?, context_growth_pct = ?, output_pct = ?, "
            "waste_pct = 0.0 WHERE session_id = ?",
            (0.5 + 0.01 * (i % 7), 0.1 + 0.005 * (i % 5), 0.05 + 0.002 * (i % 3), f"cur-{i:03d}"),
        )
    for i in range(n_legacy):
        insert_row(conn, f"old-{i}", tokens=999_999, adapter_version=None, cost_version=None)
        conn.execute(
            "UPDATE sessions SET context_resend_pct = 0.99, context_growth_pct = 0.0, "
            "output_pct = 0.0, waste_pct = 0.0 WHERE session_id = ?",
            (f"old-{i}",),
        )
    conn.commit()
    conn.close()
    return db


def test_patterns_json_excludes_legacy_rows_from_the_clustering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _pattern_store(tmp_path)
    code, out, _ = _run(monkeypatch, capsys, "patterns", "--json", "--db-path", str(db))
    doc = json.loads(out)
    assert code == 0
    assert doc["legacy_rows_excluded"] == 3
    assert doc["valid"] is True
    assert doc["n_sessions"] == 34  # the 3 legacy rows are not in the analysis
    assert doc["analysis"]["legacy_rows_excluded"] == 3
    # the legacy rows' extreme resend share (0.99) must not shape any archetype
    assert all(a["centroid"]["context_resend_pct"] < 0.9 for a in doc["analysis"]["archetypes"])


def test_patterns_text_says_how_many_legacy_rows_were_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _pattern_store(tmp_path)
    _, out, _ = _run(monkeypatch, capsys, "patterns", "--db-path", str(db))
    assert "3 legacy sessions left out" in out and LEGACY_ROW_LABEL in out


def test_patterns_below_floor_still_reports_the_count(
    mixed: MixedStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = _run(monkeypatch, capsys, "patterns", "--json", "--db-path", str(mixed.db))
    doc = json.loads(out)
    assert doc["valid"] is False and doc["legacy_rows_excluded"] == 4
    assert "legacy sessions left out" in doc["status"]


def test_a_cache_built_before_the_count_was_recorded_is_recomputed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _pattern_store(tmp_path)
    _run(monkeypatch, capsys, "patterns", "--json", "--db-path", str(db))
    cache_file = db.parent / f"{db.stem}.intelligence_cache.json"
    stale = json.loads(cache_file.read_text(encoding="utf-8"))
    del stale["legacy_rows_excluded"]
    cache_file.write_text(json.dumps(stale), encoding="utf-8")
    _, out, _ = _run(monkeypatch, capsys, "patterns", "--json", "--db-path", str(db))
    assert json.loads(out)["legacy_rows_excluded"] == 3


# ----------------------------------------------------------------------------- ask


def test_ask_context_numbers_come_from_current_rows_only(mixed: MixedStore) -> None:
    ctx = build_chat_context("how much did I spend?", db_path=str(mixed.db))
    corpus = ctx["corpus_stats"]
    assert corpus["legacy_rows_excluded"] == 4
    assert corpus["current_sessions"] == 2 and corpus["content_sessions"] == 2
    assert corpus["cost_usd"]["total"] == 21.0  # 10.0 + 11.0; the legacy 99.0 rows are out
    assert corpus["real_tokens"]["median"] == 100_000  # not the legacy 999,999
    message = _build_user_message(ctx)
    assert "4 further sessions in the store are LEGACY" in message
    assert LEGACY_ROW_LABEL in message


def test_ask_labels_a_legacy_session_the_user_names(tmp_path: Path) -> None:
    db = tmp_path / "named.db"
    sid = str(uuid.UUID(int=7))
    conn = open_db(db)
    insert_row(conn, sid, adapter_version=None, cost_version=None, tokens=999_999)
    insert_row(conn, "cur", tokens=100_000)
    conn.close()
    ctx = build_chat_context(f"what happened in {sid}?", db_path=str(db))
    assert ctx["session"]["legacy"] is True
    assert "LEGACY SESSION" in _build_user_message(ctx)


def test_ask_cli_note_counts_the_excluded_sessions(
    mixed: MixedStore, capsys: pytest.CaptureFixture[str]
) -> None:
    cli._print_ask_legacy_note(str(mixed.db))
    assert "4 legacy sessions left out from the numbers above" in capsys.readouterr().out
