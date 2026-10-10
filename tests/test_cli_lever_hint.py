from __future__ import annotations

"""`tes score` prints the dashboard's data-gated lever hint (W1A item 4).

Synthetic sessions with a controlled token mix drive the three outcomes: a lever fires, none
fires (quiet, nothing printed), and an unpriced model (the $-levers cannot be evaluated and the
output says so instead of reading as "no lever"). The JSON key `lever_hint` is null when quiet.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import tes.cli as cli

PRICED = "claude-sonnet-4-6"
UNPRICED = "claude-test-unpriced-9"


def _session(path: Path, model: str, *, input_tokens: int, cache_read: int, output: int) -> Path:
    """Three identical turns with the given per-turn usage (distinct message ids)."""
    records: list[dict[str, Any]] = []
    for i in range(3):
        records.append({"type": "user", "message": {"role": "user", "content": f"step {i}"}})
        records.append(
            {
                "type": "assistant",
                "message": {
                    "id": f"m{i}",
                    "role": "assistant",
                    "model": model,
                    "content": [{"type": "text", "text": f"answer {i}"}],
                    "usage": {
                        "input_tokens": input_tokens,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": cache_read,
                        "output_tokens": output,
                    },
                },
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TES_DB_PATH", str(tmp_path / "tes.db"))
    monkeypatch.setattr(cli, "is_judge_available", lambda *a, **k: False)
    monkeypatch.setattr(cli, "detect_env_api_key", lambda *a, **k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def _run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> tuple[str, str]:
    monkeypatch.setattr("sys.argv", ["tes", *argv])
    try:
        cli.main()
    except SystemExit:
        pass
    captured = capsys.readouterr()
    return captured.out, captured.err


def _json(out: str) -> dict[str, Any]:
    return json.loads(out[out.index("{") :])


def test_lever_fires_in_human_output_after_the_cost_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(
        tmp_path / "p" / "ctx.jsonl", PRICED, input_tokens=100, cache_read=100_000, output=200
    )
    out, _ = _run(monkeypatch, capsys, ["score", str(path), "--no-judge"])
    assert "── LEVER " in out
    assert "checkpointing or /compact" in out
    assert "re-send" in out  # the impact (share of cost) travels with the fix
    assert out.index("COST ANNOTATION") < out.index("── LEVER ")


def test_lever_does_not_fire_prints_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(
        tmp_path / "p" / "quiet.jsonl", PRICED, input_tokens=2000, cache_read=5000, output=200
    )
    out, _ = _run(monkeypatch, capsys, ["score", str(path), "--no-judge"])
    assert "LEVER" not in out
    data = _json(_run(monkeypatch, capsys, ["score", str(path), "--no-judge", "--json"])[0])
    assert data["lever_hint"] is None


def test_unpriced_session_says_cost_levers_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(
        tmp_path / "p" / "unp.jsonl", UNPRICED, input_tokens=100, cache_read=100_000, output=200
    )
    out, _ = _run(monkeypatch, capsys, ["score", str(path), "--no-judge"])
    assert "Cost levers unavailable" in out
    assert f"unpriced ({UNPRICED})" in out
    assert "$0.00" not in out


def test_json_carries_lever_hint_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(
        tmp_path / "p" / "ctx.jsonl", PRICED, input_tokens=100, cache_read=100_000, output=200
    )
    out, _ = _run(monkeypatch, capsys, ["score", str(path), "--no-judge", "--json"])
    data = _json(out)
    assert "lever_hint" in data
    assert "checkpointing or /compact" in data["lever_hint"]
