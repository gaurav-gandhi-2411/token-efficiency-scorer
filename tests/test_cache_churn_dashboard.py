from __future__ import annotations

"""Dashboard session page: the cache-churn card in its three states (populated, empty, unavailable)."""

import json
import random
from pathlib import Path
from typing import Any

import pytest
import tracegauge.cli as cli
from tracegauge.web.server import ServerConfig, create_app

from tests.churn_synth import build_chain


def _write(path: Path, recs: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return path


def _score(monkeypatch: pytest.MonkeyPatch, db: Path, path: Path) -> None:
    monkeypatch.setenv("TES_DB_PATH", str(db))
    monkeypatch.setattr("sys.argv", ["tes", "score", str(path), "--no-judge"])
    try:
        cli.main()
    except SystemExit as exc:
        assert not exc.code


def _page(db: Path, session_id: str) -> str:
    app = create_app(ServerConfig(db_path=db))
    with app.test_client() as client:
        resp = client.get(f"/session/{session_id}")
        assert resp.status_code == 200
        return resp.get_data(as_text=True)


def test_card_populated_empty_and_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "tes.db"
    rng = random.Random(42)
    hot = _write(
        tmp_path / "p" / "hot.jsonl",
        build_chain(rng, 40, {10: "TTL_GAP", 25: "COMPACTION"}, one_hour=True, prefix="h"),
    )
    cold = _write(tmp_path / "p" / "cold.jsonl", build_chain(rng, 30, {}, prefix="c"))
    gone = _write(tmp_path / "p" / "gone.jsonl", build_chain(rng, 30, {8: "TTL_GAP"}, prefix="g"))
    for p in (hot, cold, gone):
        _score(monkeypatch, db, p)
    capsys.readouterr()
    gone.unlink()  # a transcript the store still points at, removed since it was scored

    populated = _page(db, "hot")
    assert 'id="cache-churn"' in populated and "event(s) re-wrote cached context" in populated
    assert "TTL_GAP" in populated and "COMPACTION" in populated
    assert "Plausibly avoidable (a judgement)" in populated

    empty = _page(db, "cold")
    assert "No cache churn detected: 0 events" in empty

    unavailable = _page(db, "gone")
    card = unavailable[unavailable.index('id="cache-churn"') :]
    assert "UNAVAILABLE" in card[:800] and "$0.00" not in card[:800]
