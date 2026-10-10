from __future__ import annotations

"""A model missing from the price table must read `unpriced (<model>)`, never `$0.00` (D7).

Uses a fake model id that can never be in prices.json, so this stays valid as prices are added.
Covers fully priced / fully unpriced / partially priced across the CLI (`score`, `--json`,
`cost`, `monitor`), the budget projection and the dashboard.
"""

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
import tes.cli as cli
from tes._digest import reconstruct_digest
from tes.adapt import adapt_session
from tes.budget import compute_budget_projection
from tes.cost import compute_session_cost, load_price_table
from tes.live_monitor import score_live_session
from tes.store import list_sessions, open_db
from tes.web.cost_format import format_cost_display

PRICED = "claude-sonnet-4-6"
UNPRICED = "claude-test-unpriced-9"


def _msg(i: int, model: str) -> list[dict[str, Any]]:
    return [
        {"type": "user", "message": {"role": "user", "content": f"task step {i}"}},
        {
            "type": "assistant",
            "message": {
                "id": f"m{i}-{model}",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": f"answer {i}"}],
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 5_000,
                    "output_tokens": 200,
                },
            },
        },
    ]


def _session(path: Path, models: list[str]) -> Path:
    records: list[dict[str, Any]] = []
    for i, model in enumerate(models):
        records += _msg(i, model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- formatter


def test_format_cost_display_cases() -> None:
    assert format_cost_display(1.5, []) == "$1.50"
    assert format_cost_display(None, []) == "—"
    assert format_cost_display(0.0, [UNPRICED]) == f"unpriced ({UNPRICED})"
    assert format_cost_display(None, [UNPRICED]) == f"unpriced ({UNPRICED})"
    assert format_cost_display(1.5, [UNPRICED]) == f"$1.50 + unpriced ({UNPRICED})"
    assert format_cost_display(1.5, ["b", "a", "a"]) == "$1.50 + unpriced (a, b)"
    assert format_cost_display(1.5, [], 3, True) == "~$1.500"


# --------------------------------------------------------------------------- engine


@pytest.mark.parametrize(
    ("models", "expected_unpriced", "priced"),
    [
        ([PRICED, PRICED], [], True),
        ([UNPRICED, UNPRICED], [UNPRICED], False),
        ([PRICED, UNPRICED], [UNPRICED], False),
    ],
)
def test_session_cost_unpriced_models(
    tmp_path: Path, models: list[str], expected_unpriced: list[str], priced: bool
) -> None:
    rec = adapt_session(_session(tmp_path / "p" / "s.jsonl", models))
    cost = compute_session_cost(reconstruct_digest(rec["digest"]), load_price_table())
    assert cost.unpriced_models == expected_unpriced
    assert cost.priced is priced
    if models == [UNPRICED, UNPRICED]:
        assert cost.total_usd == 0.0
    else:
        assert cost.total_usd > 0.0


def test_zero_token_synthetic_turn_does_not_make_a_session_unpriced(tmp_path: Path) -> None:
    path = _session(tmp_path / "p" / "s.jsonl", [PRICED])
    synthetic = {
        "type": "assistant",
        "message": {
            "id": "syn",
            "role": "assistant",
            "model": "<synthetic>",
            "content": [{"type": "text", "text": "No response requested."}],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(synthetic) + "\n")
    rec = adapt_session(path)
    cost = compute_session_cost(reconstruct_digest(rec["digest"]), load_price_table())
    assert cost.priced


# --------------------------------------------------------------------------- CLI


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "tes.db"
    monkeypatch.setenv("TES_DB_PATH", str(db))
    monkeypatch.setattr(cli, "is_judge_available", lambda *a, **k: False)
    monkeypatch.setattr(cli, "detect_env_api_key", lambda *a, **k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return db


def _cli(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr("sys.argv", ["tes", *argv])
    try:
        cli.main()
    except SystemExit:
        pass


def _score_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], path: Path
) -> dict[str, Any]:
    _cli(monkeypatch, ["score", str(path), "--json", "--no-judge"])
    out = capsys.readouterr().out
    return json.loads(out[out.index("{") :])


def test_cli_score_fully_priced(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _session(tmp_path / "proj" / "priced.jsonl", [PRICED] * 3)
    _cli(monkeypatch, ["score", str(path), "--no-judge"])
    out = capsys.readouterr().out
    assert "unpriced" not in out
    assert "Cost:  $" in out
    data = _score_json(monkeypatch, capsys, path)
    assert data["priced"] is True
    assert data["unpriced_models"] == []


def test_cli_score_fully_unpriced(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _session(tmp_path / "proj" / "unpriced.jsonl", [UNPRICED] * 3)
    _cli(monkeypatch, ["score", str(path), "--no-judge"])
    out = capsys.readouterr().out
    assert f"Cost:  unpriced ({UNPRICED})" in out
    assert "$0.00" not in out
    data = _score_json(monkeypatch, capsys, path)
    assert data["priced"] is False
    assert data["unpriced_models"] == [UNPRICED]
    # back-compat keys are still present with their old semantics
    assert data["session_cost_usd"] == 0.0
    assert data["cost_unpriced_models"] == UNPRICED
    assert data["cost_approximate"] is True  # 100% of turns unresolved > the 25% threshold


def test_cli_score_partially_priced(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 1 of 4 turns unresolved = 25%: NOT above the legacy 25% threshold, so cost_approximate
    # stays False -- the new `priced` flag is what catches it.
    path = _session(tmp_path / "proj" / "partial.jsonl", [PRICED] * 3 + [UNPRICED])
    _cli(monkeypatch, ["score", str(path), "--no-judge"])
    out = capsys.readouterr().out
    assert f"+ unpriced ({UNPRICED})" in out
    assert "Cost:  $" in out
    data = _score_json(monkeypatch, capsys, path)
    assert data["priced"] is False
    assert data["unpriced_models"] == [UNPRICED]
    assert data["session_cost_usd"] > 0
    assert data["cost_approximate"] is False


def test_cli_cost_period_names_unpriced_models(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name, models in (("a", [UNPRICED] * 2), ("b", [PRICED] * 2)):
        path = _session(tmp_path / "proj" / f"{name}.jsonl", models)
        now = time.time()
        os.utime(path, (now, now))
        _cli(monkeypatch, ["score", str(path), "--no-judge", "--json"])
    capsys.readouterr()
    _cli(monkeypatch, ["cost", "--week"])
    out = capsys.readouterr().out
    assert f"unpriced ({UNPRICED})" in out
    assert "priced subtotal only" in out
    assert "Priced coverage: 50% of sessions" in out  # 1 of 2 sessions fully priced
    assert "Priced coverage: 100%" not in out


def test_cli_cost_period_fully_unpriced_total_is_not_zero_dollars(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _session(tmp_path / "proj" / "only.jsonl", [UNPRICED] * 2)
    _cli(monkeypatch, ["score", str(path), "--no-judge", "--json"])
    capsys.readouterr()
    _cli(monkeypatch, ["cost", "--week"])
    out = capsys.readouterr().out
    assert f"Total: unpriced ({UNPRICED})" in out
    assert "$0.00" not in out
    # Coverage is truthful: the only session is unpriced, so nothing is covered (was 100%/100%).
    assert "Priced coverage: 0% of sessions, 0% of tokens" in out
    assert "Priced coverage: 100%" not in out


# --------------------------------------------------------------------------- budget / live


def test_budget_projection_unpriced(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _session(tmp_path / "proj" / "u.jsonl", [UNPRICED] * 2)
    _cli(monkeypatch, ["score", str(path), "--no-judge", "--json"])
    conn = open_db(cli_env)
    proj = compute_budget_projection(conn)
    assert proj is not None
    assert proj.unpriced_models == [UNPRICED]
    assert not proj.priced
    assert f"unpriced ({UNPRICED})" in proj.message
    assert "$0.00" not in proj.message
    assert proj.projected_usd_for_window == 0.0
    conn.close()


def test_live_monitor_state_carries_unpriced_models(tmp_path: Path) -> None:
    path = _session(tmp_path / "proj" / "live.jsonl", [PRICED, UNPRICED])
    live = score_live_session(path)
    assert live is not None
    assert live.live_unpriced_models == [UNPRICED]
    assert not live.live_priced


# --------------------------------------------------------------------------- dashboard


def test_dashboard_renders_unpriced(
    cli_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tes.web.server import ServerConfig, create_app

    full = _session(tmp_path / "proj" / "full.jsonl", [UNPRICED] * 3)
    part = _session(tmp_path / "proj" / "part.jsonl", [PRICED] * 3 + [UNPRICED])
    ok = _session(tmp_path / "proj" / "ok.jsonl", [PRICED] * 3)
    for p in (full, part, ok):
        _cli(monkeypatch, ["score", str(p), "--no-judge", "--json"])
    capsys.readouterr()

    conn = open_db(cli_env)
    rows = {r["session_id"]: r for r in list_sessions(conn, limit=10)}
    conn.close()
    assert rows["full"]["priced"] is False
    assert rows["full"]["unpriced_models"] == [UNPRICED]
    assert rows["ok"]["priced"] is True

    app = create_app(ServerConfig(db_path=cli_env))
    app.config["TESTING"] = True
    with app.test_client() as c:
        listing = c.get("/").get_data(as_text=True)
        assert f"unpriced ({UNPRICED})" in listing
        assert f"+ unpriced ({UNPRICED})" in listing  # the partially priced row
        detail = c.get("/session/full").get_data(as_text=True)
        assert f"unpriced ({UNPRICED})" in detail
        assert "$0.00" not in detail
        ok_detail = c.get("/session/ok").get_data(as_text=True)
        assert "unpriced" not in ok_detail
        budget = c.get("/budget").get_data(as_text=True)
        assert f"unpriced ({UNPRICED})" in budget
