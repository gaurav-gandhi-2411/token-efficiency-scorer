from __future__ import annotations

"""The session page shows the cost breakdown as labelled information and a finding only when an
absolute rule fires: "context dominates" is not a banner any more."""

from pathlib import Path

import pytest
import tes.cli as cli
from flask.testing import FlaskClient
from tes.store import open_db
from tes.web.server import ServerConfig, create_app

from tests.test_cli_lever_hint import PRICED, UNPRICED, _session


def _page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: Path) -> str:
    db = tmp_path / "dash.db"
    monkeypatch.setenv("TES_DB_PATH", str(db))
    monkeypatch.setattr(cli, "is_judge_available", lambda *a, **k: False)
    monkeypatch.setattr("sys.argv", ["tes", "--quiet", "score", str(path), "--no-judge"])
    try:
        cli.main()
    except SystemExit:
        pass
    conn = open_db(db)
    sid = conn.execute("SELECT session_id FROM sessions").fetchone()[0]
    conn.close()
    client: FlaskClient = create_app(ServerConfig(db_path=db)).test_client()
    resp = client.get(f"/session/{sid}")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_context_heavy_session_page_has_the_breakdown_and_no_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _session(
        tmp_path / "p" / "ctx.jsonl", PRICED, input_tokens=100, cache_read=100_000, output=200
    )
    html = _page(tmp_path, monkeypatch, path)
    assert 'id="cost-breakdown"' in html
    assert "informational: where the priced cost went, not a finding" in html
    assert "Context re-send (cache reads)" in html and "Total (priced)" in html
    assert 'id="finding"' not in html
    assert "drove most of the cost" not in html and "/compact" not in html


def test_output_heavy_session_page_shows_the_finding_above_the_breakdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _session(
        tmp_path / "p" / "out.jsonl", PRICED, input_tokens=100, cache_read=0, output=50_000
    )
    html = _page(tmp_path, monkeypatch, path)
    assert 'id="finding"' in html and "Output was" in html
    assert html.index('id="finding"') < html.index('id="cost-breakdown"')


def test_unpriced_session_page_says_not_computed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _session(
        tmp_path / "p" / "unp.jsonl", UNPRICED, input_tokens=100, cache_read=100_000, output=200
    )
    html = _page(tmp_path, monkeypatch, path)
    assert 'id="cost-breakdown"' in html
    assert "Not computed" in html and f"unpriced ({UNPRICED})" in html
    assert 'id="finding"' not in html


def test_dashboard_uses_the_shared_breakdown_function() -> None:
    """No duplicated logic: the page's breakdown IS the one `tes score` prints."""
    import tes.web.server as server
    from tes.takeaway import build_cost_breakdown

    assert server.build_cost_breakdown is build_cost_breakdown
