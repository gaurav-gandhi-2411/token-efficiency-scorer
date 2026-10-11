from __future__ import annotations

"""The informational COST BREAKDOWN (tes/takeaway.py, `tes score`, `score --json`).

Dollars and share of priced cost per bucket. It is a breakdown, not a finding: the tests pin that
the buckets partition the total, that unpriced models are named instead of read as $0.00, and
that the CLI prints it for every session whose attribution is available.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import tes.cli as cli
from tes.attribution import AttributionResult
from tes.takeaway import BREAKDOWN_LABEL, build_cost_breakdown, format_cost_breakdown_lines

PRICED = "claude-sonnet-4-6"
UNPRICED = "claude-test-unpriced-9"
BREAKDOWN_KEYS = {"label", "total_usd", "priced", "unpriced_models", "buckets", "note"}
BUCKET_KEYS = {"key", "label", "usd", "share_pct", "tokens"}


def _attr(
    *,
    resend: float = 0.0,
    growth: float = 0.0,
    output: float = 0.0,
    fresh: float = 0.0,
    rr: float = 0.0,
    rfr: float = 0.0,
) -> AttributionResult:
    total = resend + growth + output + fresh + rr + rfr
    return AttributionResult(
        session_id="s",
        rr_waste_tokens=11,
        rfr_waste_tokens=22,
        context_resend_tokens=1000,
        output_tokens=50,
        fresh_input_tokens=7,
        context_growth_tokens=100,
        rr_waste_usd=rr,
        rfr_waste_usd=rfr,
        context_resend_usd=resend,
        output_usd=output,
        fresh_input_usd=fresh,
        context_growth_usd=growth,
        total_billed_tokens=0,
        total_usd=total,
        real_tokens=0,
        domain_of_validity="d",
    )


def test_buckets_partition_the_total_and_are_labelled_informational() -> None:
    attr = _attr(resend=5.0, growth=1.0, output=2.0, fresh=1.5, rr=0.4, rfr=0.1)
    bd = build_cost_breakdown(attr)
    assert bd["label"] == BREAKDOWN_LABEL and "not a finding" in bd["label"]
    assert [b["key"] for b in bd["buckets"]] == [
        "context_resend",
        "context_growth",
        "output",
        "fresh_input",
        "waste",
    ]
    assert sum(b["usd"] for b in bd["buckets"]) == pytest.approx(bd["total_usd"])
    assert sum(b["share_pct"] for b in bd["buckets"]) == pytest.approx(100.0, abs=0.2)
    waste = bd["buckets"][-1]
    assert waste["usd"] == pytest.approx(0.5) and waste["tokens"] == 33  # rr + rfr merged
    assert bd["priced"] is True and bd["note"] == ""


def test_unpriced_session_breakdown_says_not_computed() -> None:
    bd = build_cost_breakdown(_attr(), (UNPRICED,))
    assert bd["priced"] is False and bd["total_usd"] == 0
    assert "Not computed" in bd["note"] and f"unpriced ({UNPRICED})" in bd["note"]
    assert all(b["share_pct"] is None for b in bd["buckets"])
    assert format_cost_breakdown_lines(bd) == []  # no row of $0.00 that reads as free


def test_partially_priced_breakdown_says_priced_part_only() -> None:
    bd = build_cost_breakdown(_attr(resend=5.0, output=1.0), (UNPRICED,))
    assert bd["priced"] is False and bd["total_usd"] == 6.0
    assert bd["note"].startswith("Priced part only") and "true cost is higher" in bd["note"]


def test_breakdown_lines_show_dollars_and_shares() -> None:
    text = "\n".join(
        format_cost_breakdown_lines(build_cost_breakdown(_attr(resend=3.0, output=1.0)))
    )
    assert "$3.00" in text and "75.0%" in text and "Total (priced)" in text


# ----------------------------------------------------------------------------- CLI


def _session(path: Path, model: str) -> Path:
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
                        "input_tokens": 100,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 100_000,
                        "output_tokens": 200,
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
) -> str:
    monkeypatch.setattr("sys.argv", ["tes", *argv])
    try:
        cli.main()
    except SystemExit:
        pass
    return capsys.readouterr().out


def test_score_prints_the_breakdown_after_the_cost_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path / "p" / "s.jsonl", PRICED)
    out = _run(monkeypatch, capsys, ["score", str(path), "--no-judge"])
    assert "── COST BREAKDOWN " in out
    assert "informational" in out and "not a finding" in out
    assert "Context re-send (cache reads)" in out and "Total (priced)" in out
    assert out.index("COST ANNOTATION") < out.index("── COST BREAKDOWN ")


def test_score_json_carries_cost_breakdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path / "p" / "s.jsonl", PRICED)
    out = _run(monkeypatch, capsys, ["score", str(path), "--no-judge", "--json"])
    data = json.loads(out[out.index("{") :])
    assert data["schema_version"] == 1
    bd = data["cost_breakdown"]
    assert set(bd) == BREAKDOWN_KEYS and all(set(b) == BUCKET_KEYS for b in bd["buckets"])
    assert bd["priced"] is True and bd["unpriced_models"] == []
    assert sum(b["usd"] for b in bd["buckets"]) == pytest.approx(bd["total_usd"], abs=1e-5)


def test_unpriced_score_breakdown_says_not_computed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path / "p" / "u.jsonl", UNPRICED)
    out = _run(monkeypatch, capsys, ["score", str(path), "--no-judge"])
    assert "── COST BREAKDOWN " in out and "Not computed" in out
    assert f"unpriced ({UNPRICED})" in out and "$0.00" not in out
