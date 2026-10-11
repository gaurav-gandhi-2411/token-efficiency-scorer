from __future__ import annotations

"""`budget` and `cost` leave LEGACY rows out of corrected spend and say how many they left out.

Per command: budget drops them (a pace built on overcounted dollars never happened) and reports
`legacy_rows_excluded`; cost drops them from every total/ROI figure and shows them on one
separate, labelled history line, never summed in."""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from tes import cli
from tes.budget import compute_budget_projection, legacy_rows_excluded_in_window
from tes.cost_period import compute_period_cost
from tes.legacy import LEGACY_LABEL
from tes.store import open_db

from tests.legacy_store import build_mixed_store

UTC = timezone.utc  # datetime.UTC is 3.11+; this package supports 3.10
NOW = 2_000_000_000.0


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


def test_budget_counts_only_current_rows_and_reports_the_rest(tmp_path: Path) -> None:
    conn = open_db(build_mixed_store(tmp_path, now=NOW).db)
    now = _dt(NOW + 3600)
    projection = compute_budget_projection(conn, window_days=7, _now=now)
    assert projection is not None
    assert projection.session_count == 2  # the two current rows
    assert projection.total_usd_so_far == pytest.approx(21.0)  # 10 + 11, not + 4 * 99
    assert legacy_rows_excluded_in_window(conn, 7, _now=now) == 4
    # outside the window nothing is counted
    assert legacy_rows_excluded_in_window(conn, 7, _now=now + timedelta(days=30)) == 0


def test_budget_of_an_all_legacy_store_is_silent_not_a_legacy_projection(tmp_path: Path) -> None:
    s = build_mixed_store(tmp_path, now=NOW)
    conn = open_db(s.db)
    conn.execute("DELETE FROM sessions WHERE session_id IN (?, ?)", s.current)
    conn.commit()
    now = _dt(NOW + 3600)
    assert compute_budget_projection(conn, window_days=7, _now=now) is None
    assert legacy_rows_excluded_in_window(conn, 7, _now=now) == 4


def test_cost_total_excludes_legacy_and_reports_it_separately(tmp_path: Path) -> None:
    conn = open_db(build_mixed_store(tmp_path, now=NOW).db)
    report = compute_period_cost(conn, _dt(NOW - 86400), _dt(NOW + 86400))
    assert report.total_usd == pytest.approx(21.0)
    assert report.session_count == 2
    assert report.token_total == 100_000 + 100_001  # legacy tokens (999_999 each) not counted
    assert report.legacy_rows_excluded == 4
    assert report.legacy_total_usd == pytest.approx(396.0)


def _run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.argv", ["tes", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def recent_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    s = build_mixed_store(tmp_path, now=time.time() - 3600)
    monkeypatch.setenv("TES_DB_PATH", str(s.db))
    return s.db


def test_cli_json_carries_legacy_rows_excluded(
    recent_store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(monkeypatch, capsys, "budget", "--json")
    doc: dict[str, Any] = json.loads(out)
    assert code == 0 and doc["legacy_rows_excluded"] == 4 and doc["session_count"] == 2
    code, out, _ = _run(monkeypatch, capsys, "cost", "--week", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["legacy_rows_excluded"] == 4
    assert doc["total_usd"] == pytest.approx(21.0)
    assert doc["legacy"] == {
        "label": LEGACY_LABEL,
        "session_count": 4,
        "total_usd": pytest.approx(396.0),
        "included_in_total_usd": False,
    }
    assert doc["schema_version"] == 1  # keys were only added


def test_cli_text_shows_a_separate_legacy_line_and_a_budget_note(
    recent_store: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = _run(monkeypatch, capsys, "cost", "--week")
    assert "Total: $21.00" in out
    assert f"{LEGACY_LABEL}: $396.00 across 4 sessions -- NOT included in the total" in out
    _, out, _ = _run(monkeypatch, capsys, "budget")
    assert "4 legacy sessions in this window left out" in out
    assert "$21.00" in out and "$417" not in out
