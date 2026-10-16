from __future__ import annotations

"""Cache churn on the CLI and `score --json`: shape, honesty (unpriced, no text), quiet when empty."""

import json
import random
from pathlib import Path
from typing import Any

import pytest
import tes.cli as cli
from tes import cache_churn as cc
from tes.json_out import cache_churn_payload
from tes.report import format_cache_churn_lines

from tests.churn_synth import MODEL_A, T0, asst_rec, build_chain, user_rec

SECRET_TEXT = "ZEBRA-PINEAPPLE-7731 do not leak"


def _write(path: Path, recs: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    return path


def _with_text(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for r in recs:
        if "message" not in r:
            continue  # system markers carry no message
        if r["type"] == "user":
            r["message"]["content"] = SECRET_TEXT
        else:
            r["message"]["content"] = [{"type": "text", "text": SECRET_TEXT}]
    return recs


def _session(tmp_path: Path, injections: dict[int, str], model: str = MODEL_A) -> Path:
    recs = build_chain(random.Random(42), 40, injections, prefix="m")
    if model != MODEL_A:
        for r in recs:
            if r["type"] == "assistant":
                r["message"]["model"] = model
    return _write(tmp_path / "proj" / "s.jsonl", _with_text(recs))


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


def test_score_json_reports_events_with_proof_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path, {10: "TTL_GAP", 20: "MODEL_SWITCH", 30: "COMPACTION"})
    _, out, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge", "--json")
    churn = json.loads(out)["cache_churn"]
    assert churn["status"] == "ok" and churn["events"] == 3 and churn["priced"] is True
    assert {
        c: churn["by_cause"][c]["events"] for c in ("TTL_GAP", "MODEL_SWITCH", "COMPACTION")
    } == {
        "TTL_GAP": 1,
        "MODEL_SWITCH": 1,
        "COMPACTION": 1,
    }
    assert churn["by_cause"]["UNEXPLAINED"]["events"] == 0
    assert churn["churn_usd"] == pytest.approx(
        sum(b["churn_usd"] for b in churn["by_cause"].values())
    )
    ev = churn["events_listed"][0]
    assert set(ev) == {
        "scope",
        "chain",
        "cause",
        "avoidability",
        "gap_s",
        "ttl_s",
        "note",
        "marker_index",
        "churn_tokens",
        "churn_usd",
        "priced",
        "previous_turn",
        "current_turn",
    }
    proof = {
        "turn_index",
        "record_index",
        "message_id",
        "model",
        "cache_read",
        "cache_creation",
        "cache_creation_1h",
    }
    assert set(ev["previous_turn"]) == proof == set(ev["current_turn"])
    assert ev["current_turn"]["turn_index"] == ev["previous_turn"]["turn_index"] + 1


def test_no_transcript_text_reaches_json_or_human_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path, {10: "TTL_GAP"})
    _, out_json, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge", "--json")
    churn_text = json.dumps(json.loads(out_json)["cache_churn"])
    assert SECRET_TEXT not in churn_text and "ZEBRA" not in churn_text
    _, out_human, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge")
    assert "CACHE CHURN" in out_human and "ZEBRA" not in out_human


def test_human_report_is_silent_when_there_are_no_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path, {})
    _, out, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge")
    assert "CACHE CHURN" not in out
    assert format_cache_churn_lines(None) == []
    assert format_cache_churn_lines(cache_churn_payload(None)) == []


def test_human_section_lists_main_vs_subagent_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path, {10: "TTL_GAP"})
    sub = build_chain(random.Random(2), 30, {5: "TTL_GAP", 12: "TTL_GAP"}, prefix="s")
    _write(path.with_suffix("") / "subagents" / "agent-x.jsonl", sub)
    _, out, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge")
    assert "Main chain" in out and "subagents" in out and "judgement" in out


def test_unpriced_model_is_never_shown_as_zero_dollars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path, {10: "TTL_GAP"}, model="claude-test-unpriced-9")
    _, out, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge", "--json")
    churn = json.loads(out)["cache_churn"]
    assert churn["events"] == 1 and churn["priced"] is False and churn["unpriced_events"] == 1
    assert churn["events_listed"][0]["churn_usd"] is None
    assert churn["events_listed"][0]["priced"] is False
    _, human, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge")
    assert "unpriced" in human[human.index("CACHE CHURN") :]


def test_unavailable_when_analysis_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path, {10: "TTL_GAP"})

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("gone")

    monkeypatch.setattr(cc, "analyze_session", boom)
    code, out, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge", "--json")
    churn = json.loads(out)["cache_churn"]
    assert code == 0  # churn must never break scoring
    assert churn["status"] == "unavailable" and churn["events"] is None
    assert churn["reason"] == "read_error"
    assert churn["churn_usd"] is None  # unknown, not 0
    assert set(churn) == set(cache_churn_payload(None))


def test_internal_error_is_logged_with_traceback_and_stdout_stays_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = _session(tmp_path, {10: "TTL_GAP"})

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("bug in the analysis")

    monkeypatch.setattr(cc, "analyze_session", boom)
    with caplog.at_level("WARNING", logger="tes.json_out"):
        code, out, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge", "--json")
    churn = json.loads(out)["cache_churn"]  # stdout is still exactly one valid JSON document
    assert code == 0
    assert churn["status"] == "unavailable" and churn["reason"] == "internal_error"
    (rec,) = [r for r in caplog.records if r.name == "tes.json_out"]
    assert rec.levelname == "WARNING" and rec.exc_info is not None
    assert rec.exc_info[0] is RuntimeError  # the traceback is kept, not swallowed


def test_ok_and_missing_source_reasons(tmp_path: Path) -> None:
    from tes.cost import load_price_table
    from tes.json_out import cache_churn_payload_for_path

    path = _session(tmp_path, {})
    good = cache_churn_payload_for_path(path, load_price_table())
    assert good["status"] == "ok" and good["reason"] is None
    assert cache_churn_payload_for_path(tmp_path / "nope.jsonl", {})["reason"] == "source_missing"


def test_payload_lists_at_most_the_largest_events() -> None:
    recs = build_chain(random.Random(9), 120, {3 + 5 * i: "TTL_GAP" for i in range(20)})
    chain = cc.read_chain_records(recs, kind="main", label="main")
    s = cc.summarise([cc.detect_chain(chain, cc.RateTable())])
    payload = cache_churn_payload(s)
    assert payload["events"] == 20 and len(payload["events_listed"]) == cc.MAX_EVENTS_LISTED
    usd = [e["churn_usd"] for e in payload["events_listed"]]
    assert usd == sorted(usd, reverse=True)


def test_events_without_ttl_gap_have_no_cause_claim_in_payload() -> None:
    recs = [
        user_rec(T0),
        asst_rec("a", MODEL_A, cr=99_000, cc=1_000, ts=T0 + 1),
        user_rec(T0 + 30),
        asst_rec("b", MODEL_A, cr=10_000, cc=90_000, ts=T0 + 31),
    ]
    chain = cc.read_chain_records(recs, kind="main", label="main")
    payload = cache_churn_payload(cc.summarise([cc.detect_chain(chain, cc.RateTable())]))
    assert payload["by_cause"]["UNEXPLAINED"]["events"] == 1
    assert payload["events_listed"][0]["cause"] == "UNEXPLAINED"
    assert payload["events_listed"][0]["avoidability"] == "unknown"
    assert payload["avoidable_usd"] == 0.0 and payload["unavoidable_usd"] == 0.0
