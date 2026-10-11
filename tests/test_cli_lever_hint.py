from __future__ import annotations

"""`tes score` prints a neutral COST BREAKDOWN and, rarely, an absolute LEVER finding.

Synthetic sessions with a controlled token mix drive the outcomes: a context-heavy session (the
usual case) gets the breakdown and NO lever; an output-heavy one fires the output finding; the
waste finding fires through the real attribution and waste pipeline; an unpriced model gets a
breakdown that says it was not computed instead of reading as "no lever". The JSON keys are
`lever_hint` (null unless a finding fires) and `cost_breakdown` (additive).
"""

import json
from pathlib import Path
from typing import Any

import pytest
import tes.cli as cli

PRICED = "claude-sonnet-4-6"
UNPRICED = "claude-test-unpriced-9"

BREAKDOWN_KEYS = {"label", "total_usd", "priced", "unpriced_models", "buckets", "note"}
BUCKET_KEYS = {"key", "label", "usd", "share_pct", "tokens"}


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


def test_context_heavy_session_gets_a_breakdown_and_no_lever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(
        tmp_path / "p" / "ctx.jsonl", PRICED, input_tokens=100, cache_read=100_000, output=200
    )
    out, _ = _run(monkeypatch, capsys, ["score", str(path), "--no-judge"])
    assert "── COST BREAKDOWN " in out
    assert "informational" in out and "not a finding" in out
    assert "Context re-send (cache reads)" in out and "Total (priced)" in out
    assert out.index("COST ANNOTATION") < out.index("── COST BREAKDOWN ")
    # context is ~96% of this session's cost and that is NOT presented as a lever
    assert "LEVER" not in out
    assert "checkpointing" not in out and "/compact" not in out
    assert "drove most of the cost" not in out


def test_json_context_heavy_session_has_null_lever_hint_and_a_breakdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(
        tmp_path / "p" / "ctx.jsonl", PRICED, input_tokens=100, cache_read=100_000, output=200
    )
    data = _json(_run(monkeypatch, capsys, ["score", str(path), "--no-judge", "--json"])[0])
    assert data["schema_version"] == 1
    assert data["lever_hint"] is None
    bd = data["cost_breakdown"]
    assert set(bd) == BREAKDOWN_KEYS
    assert all(set(b) == BUCKET_KEYS for b in bd["buckets"])
    assert [b["key"] for b in bd["buckets"]] == [
        "context_resend",
        "context_growth",
        "output",
        "fresh_input",
        "waste",
    ]
    assert bd["priced"] is True and bd["unpriced_models"] == []
    assert sum(b["usd"] for b in bd["buckets"]) == pytest.approx(bd["total_usd"], abs=1e-5)
    assert bd["buckets"][0]["share_pct"] > 90  # shown, not flagged


def test_output_heavy_session_fires_the_output_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(
        tmp_path / "p" / "out.jsonl", PRICED, input_tokens=100, cache_read=0, output=50_000
    )
    out, _ = _run(monkeypatch, capsys, ["score", str(path), "--no-judge"])
    assert "── LEVER " in out and "Output was" in out
    assert out.index("── COST BREAKDOWN ") < out.index("── LEVER ")
    data = _json(_run(monkeypatch, capsys, ["score", str(path), "--no-judge", "--json"])[0])
    assert data["lever_hint"].startswith("Output was")


def test_quiet_balanced_session_prints_no_lever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(
        tmp_path / "p" / "quiet.jsonl", PRICED, input_tokens=2000, cache_read=5000, output=200
    )
    out, _ = _run(monkeypatch, capsys, ["score", str(path), "--no-judge"])
    assert "LEVER" not in out
    data = _json(_run(monkeypatch, capsys, ["score", str(path), "--no-judge", "--json"])[0])
    assert data["lever_hint"] is None and data["cost_breakdown"] is not None


def test_waste_finding_fires_through_the_real_pipeline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bundled sample has one retry loop worth ~$0.04: below the shipped threshold (quiet),
    above a lowered one (finding with dollars), so the wiring from waste events to the finding
    is exercised end to end without hand-building a $0.50 loop."""
    import tes.takeaway as takeaway

    out, _ = _run(monkeypatch, capsys, ["quickstart"])
    assert "LEVER" not in out  # shipped thresholds: quiet

    monkeypatch.setattr(takeaway, "_WASTE_ABS_USD", 0.01)
    out, _ = _run(monkeypatch, capsys, ["quickstart"])
    assert "── LEVER " in out
    assert "in detectable waste" in out and "proof turns" in out
    assert out.index("── COST BREAKDOWN ") < out.index("── LEVER ")


def test_unpriced_session_breakdown_says_not_computed_and_there_is_no_lever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(
        tmp_path / "p" / "unp.jsonl", UNPRICED, input_tokens=100, cache_read=100_000, output=200
    )
    out, _ = _run(monkeypatch, capsys, ["score", str(path), "--no-judge"])
    assert "── COST BREAKDOWN " in out
    assert "Not computed" in out and f"unpriced ({UNPRICED})" in out
    assert "LEVER" not in out
    assert "$0.00" not in out
    data = _json(_run(monkeypatch, capsys, ["score", str(path), "--no-judge", "--json"])[0])
    assert data["lever_hint"] is None
    assert data["cost_breakdown"]["priced"] is False
    assert data["cost_breakdown"]["unpriced_models"] == [UNPRICED]
    assert all(b["share_pct"] is None for b in data["cost_breakdown"]["buckets"])
