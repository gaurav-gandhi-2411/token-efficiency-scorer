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
