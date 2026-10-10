from __future__ import annotations

"""Golden-shape tests for `--json` on every reporting command (W1A item 6).

Pinned per command: the exact key set (a key added or dropped must be a deliberate edit here and
in docs/JSON_OUTPUT.md, with a SCHEMA_VERSION bump if it is a removal), that stdout is exactly one
JSON document (hints and warnings go to stderr), and the unpriced fields (`priced`,
`unpriced_models`) when a model is missing from the price table.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import tes.cli as cli
from tes.json_out import SCHEMA_VERSION

PRICED = "claude-sonnet-4-6"
UNPRICED = "claude-test-unpriced-9"

COST_KEYS = {
    "schema_version",
    "command",
    "period",
    "total_usd",
    "cost_known",
    "priced",
    "unpriced_models",
    "unpriced_models_incomplete",
    "session_count",
    "sessions_missing_cost",
    "session_coverage_pct",
    "token_coverage_pct",
    "token_total",
    "token_priced",
    "tokens_unpriced",
    "sessions_unpriced",
    "by_project",
    "roi",
}
COST_PROJECT_KEYS = {
    "project",
    "total_usd",
    "session_count",
    "cost_known",
    "priced",
    "unpriced_models",
}
ROI_KEYS = {
    "status",
    "plan_names",
    "plan_cost_usd",
    "api_equivalent_usd",
    "multiple",
    "is_floor",
    "error",
}
BUDGET_KEYS = {
    "schema_version",
    "command",
    "available",
    "window_days",
    "session_count",
    "days_observed",
    "total_usd_so_far",
    "projected_usd_for_window",
    "cost_known",
    "priced",
    "unpriced_models",
    "message",
}
IMPACT_KEYS = {
    "schema_version",
    "command",
    "top_n",
    "sessions_with_data",
    "sessions_legacy",
    "total_operations",
    "total_additions",
    "total_deletions",
    "prior_content_unknown_additions",
    "prior_content_unknown_pct",
    "untested_tool_shape_operations",
    "untested_tool_shape_pct",
    "top_files",
    "top_directories",
}
CHURN_KEYS = {"path", "edits", "additions", "deletions", "sessions_touched"}


def _session(path: Path, model: str, *, edit: bool = False) -> Path:
    records: list[dict[str, Any]] = []
    for i in range(3):
        content: list[dict[str, Any]] = [{"type": "text", "text": f"answer {i}"}]
        if edit and i == 0:
            content.append(
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Edit",
                    "input": {"file_path": "/p/a.py", "old_string": "a\n", "new_string": "b\nc\n"},
                }
            )
        records.append({"type": "user", "message": {"role": "user", "content": f"step {i}"}})
        records.append(
            {
                "type": "assistant",
                "message": {
                    "id": f"m{i}",
                    "role": "assistant",
                    "model": model,
                    "content": content,
                    "usage": {
                        "input_tokens": 100,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 5_000,
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


def _doc(out: str) -> dict[str, Any]:
    """Strict: the WHOLE of stdout must be one JSON document (no banner, no trailing text)."""
    return json.loads(out)


def _score_into_store(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    model: str,
    *,
    edit: bool = False,
) -> None:
    path = _session(tmp_path / "proj" / f"{model}.jsonl", model, edit=edit)
    code, _, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge", "--json")
    assert code == 0


# ----------------------------------------------------------------------------- cost


def test_cost_json_shape_priced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _score_into_store(monkeypatch, capsys, tmp_path, PRICED)
    code, out, _ = _run(monkeypatch, capsys, "cost", "--week", "--json")
    doc = _doc(out)
    assert code == 0
    assert set(doc) == COST_KEYS
    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["command"] == "cost"
    assert doc["priced"] is True
    assert doc["unpriced_models"] == []
    assert doc["session_count"] == 1
    assert doc["total_usd"] > 0
    assert doc["roi"] is None
    assert set(doc["period"]) == {"label", "start", "end"}
    assert set(doc["by_project"][0]) == COST_PROJECT_KEYS


def test_cost_json_unpriced_is_not_a_zero_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _score_into_store(monkeypatch, capsys, tmp_path, UNPRICED)
    _, out, _ = _run(monkeypatch, capsys, "cost", "--week", "--json")
    doc = _doc(out)
    assert doc["priced"] is False
    assert doc["unpriced_models"] == [UNPRICED]
    assert doc["by_project"][0]["priced"] is False
    # The numeric 0.0 is a placeholder for an unknown cost: cost_known says so (verifier 1 #4c).
    assert doc["total_usd"] == 0.0
    assert doc["cost_known"] is False
    assert doc["by_project"][0]["cost_known"] is False
    # Coverage must not claim the unpriced session as priced (verifier 1 #4b).
    assert doc["sessions_unpriced"] == 1
    assert doc["token_priced"] < doc["token_total"]
    assert doc["tokens_unpriced"] == doc["token_total"] - doc["token_priced"] > 0
    assert doc["session_coverage_pct"] == 0.0
    assert doc["token_coverage_pct"] == 0.0


def test_cost_json_empty_period_still_has_the_full_key_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(monkeypatch, capsys, "cost", "--week", "--json")
    doc = _doc(out)
    assert code == 0
    assert set(doc) == COST_KEYS
    assert doc["session_count"] == 0
    assert doc["by_project"] == []


def test_cost_json_roi_block_without_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _score_into_store(monkeypatch, capsys, tmp_path, PRICED)
    missing_plan = str(tmp_path / "no-plan.json")
    _, out, _ = _run(
        monkeypatch, capsys, "cost", "--week", "--roi", "--plan-config", missing_plan, "--json"
    )
    roi = _doc(out)["roi"]
    assert set(roi) == ROI_KEYS
    assert roi["status"] == "no_plan"
    assert roi["multiple"] is None


def test_cost_json_roi_block_with_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _score_into_store(monkeypatch, capsys, tmp_path, PRICED)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {"plans": [{"name": "Test", "monthly_cost_usd": 200, "effective_from": "2026-01-01"}]}
        ),
        encoding="utf-8",
    )
    _, out, _ = _run(
        monkeypatch, capsys, "cost", "--week", "--roi", "--plan-config", str(plan), "--json"
    )
    roi = _doc(out)["roi"]
    assert set(roi) == ROI_KEYS
    assert roi["status"] == "ok"
    assert roi["plan_names"] == ["Test"]
    assert roi["multiple"] is not None
    assert roi["is_floor"] is False


def test_cost_json_error_goes_to_stderr_and_stdout_stays_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_db = tmp_path / "isdir.db"
    bad_db.mkdir()
    code, out, err = _run(monkeypatch, capsys, "cost", "--week", "--json", "--db-path", str(bad_db))
    assert code == 1
    assert out == ""
    assert "Cannot open TES store" in err


# ----------------------------------------------------------------------------- budget


def test_budget_json_shape_with_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _score_into_store(monkeypatch, capsys, tmp_path, PRICED)
    code, out, _ = _run(monkeypatch, capsys, "budget", "--json")
    doc = _doc(out)
    assert code == 0
    assert set(doc) == BUDGET_KEYS
    assert doc["command"] == "budget"
    assert doc["available"] is True
    assert doc["priced"] is True


def test_budget_json_shape_without_data(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(monkeypatch, capsys, "budget", "--json", "--window-days", "3")
    doc = _doc(out)
    assert code == 0
    assert set(doc) == BUDGET_KEYS
    assert doc["available"] is False
    assert doc["window_days"] == 3
    assert doc["projected_usd_for_window"] is None


def test_budget_json_unpriced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _score_into_store(monkeypatch, capsys, tmp_path, PRICED)
    _score_into_store(monkeypatch, capsys, tmp_path, UNPRICED)
    doc = _doc(_run(monkeypatch, capsys, "budget", "--json")[1])
    assert doc["priced"] is False
    assert doc["unpriced_models"] == [UNPRICED]
    assert doc["cost_known"] is True  # one session priced: a floor, not a placeholder


def test_budget_json_all_unpriced_cost_is_flagged_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _score_into_store(monkeypatch, capsys, tmp_path, UNPRICED)
    doc = _doc(_run(monkeypatch, capsys, "budget", "--json")[1])
    assert doc["total_usd_so_far"] == 0.0
    assert doc["cost_known"] is False


# ----------------------------------------------------------------------------- impact


def test_impact_json_shape_with_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _score_into_store(monkeypatch, capsys, tmp_path, PRICED, edit=True)
    code, out, _ = _run(monkeypatch, capsys, "impact", "--json", "--top", "5")
    doc = _doc(out)
    assert code == 0
    assert set(doc) == IMPACT_KEYS
    assert doc["top_n"] == 5
    assert doc["total_operations"] == 1
    assert doc["total_additions"] == 2
    assert set(doc["top_files"][0]) == CHURN_KEYS
    assert doc["top_files"][0]["path"] == "/p/a.py"


def test_impact_json_shape_empty_store(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(monkeypatch, capsys, "impact", "--json")
    doc = _doc(out)
    assert code == 0
    assert set(doc) == IMPACT_KEYS
    assert doc["sessions_with_data"] == 0
    assert doc["top_files"] == []
    assert doc["prior_content_unknown_pct"] is None


# ----------------------------------------------------------------------------- monitor

MONITOR_KEYS = {
    "schema_version",
    "command",
    "status",
    "active",
    "cc_path",
    "source_path",
    "session_id",
    "task_type",
    "live_cost_usd",
    "cost_known",
    "priced",
    "unpriced_models",
    "live_context_tokens",
    "live_resend_ratio",
    "context_resend_dominant",
    "ai_turn_count",
    "domain_of_validity",
    "alarm",
}
ALARM_KEYS = {"message", "resend_pct", "baseline_p75_tokens", "plan_type"}


def _stub_monitor(monkeypatch: pytest.MonkeyPatch, live: Any, baseline: Any) -> None:
    import tes.live_monitor as lm
    import tes.self_baseline as sb

    monkeypatch.setattr(lm, "find_active_session", lambda *a, **k: Path("/fake/active.jsonl"))
    monkeypatch.setattr(lm, "score_live_session", lambda *a, **k: live)
    monkeypatch.setattr(sb, "load_or_compute", lambda *a, **k: baseline)


def test_monitor_json_alarm_fired_exits_3_with_alarm_object(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.test_alarm_measured import _live, _self_baseline_active

    _stub_monitor(monkeypatch, _live(), _self_baseline_active())
    code, out, _ = _run(monkeypatch, capsys, "monitor", "--json")
    doc = _doc(out)
    assert code == 3
    assert set(doc) == MONITOR_KEYS
    assert doc["status"] == "ok"
    assert doc["active"] is True
    assert doc["priced"] is True
    assert set(doc["alarm"]) == ALARM_KEYS
    assert doc["alarm"]["resend_pct"] == 92


def test_monitor_json_no_alarm_exits_0_with_null_alarm(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.test_alarm_measured import _live, _self_baseline_building

    _stub_monitor(monkeypatch, _live(), _self_baseline_building())
    code, out, _ = _run(monkeypatch, capsys, "monitor", "--json")
    doc = _doc(out)
    assert code == 0
    assert set(doc) == MONITOR_KEYS
    assert doc["alarm"] is None
    assert doc["live_cost_usd"] == 8.10


def test_monitor_json_unpriced_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.test_alarm_measured import _live, _self_baseline_building

    live = _live()
    live.live_unpriced_models = [UNPRICED]
    _stub_monitor(monkeypatch, live, _self_baseline_building())
    doc = _doc(_run(monkeypatch, capsys, "monitor", "--json")[1])
    assert doc["priced"] is False
    assert doc["unpriced_models"] == [UNPRICED]
    assert doc["cost_known"] is True  # priced subtotal of 8.10: a floor
    live.live_cost_usd = 0.0
    doc = _doc(_run(monkeypatch, capsys, "monitor", "--json")[1])
    assert doc["cost_known"] is False


def test_monitor_json_no_active_session_has_full_key_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(monkeypatch, capsys, "monitor", "--json", "--cc-path", str(tmp_path))
    doc = _doc(out)
    assert code == 0
    assert set(doc) == MONITOR_KEYS
    assert doc["status"] == "no_active_session"
    assert doc["active"] is False
    assert doc["alarm"] is None
    assert doc["session_id"] is None


def test_monitor_json_insufficient_data(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.test_alarm_measured import _self_baseline_building

    _stub_monitor(monkeypatch, None, _self_baseline_building())
    code, out, _ = _run(monkeypatch, capsys, "monitor", "--json")
    doc = _doc(out)
    assert code == 0
    assert set(doc) == MONITOR_KEYS
    assert doc["status"] == "insufficient_data"
    assert doc["active"] is True
    assert doc["source_path"].endswith("active.jsonl")


# ----------------------------------------------------------------------------- patterns

PATTERNS_KEYS = {
    "schema_version",
    "command",
    "valid",
    "status",
    "n_sessions",
    "domain_of_validity",
    "analysis",
}


def test_patterns_json_not_enough_sessions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(monkeypatch, capsys, "patterns", "--json")
    doc = _doc(out)  # strict: no "Computing session patterns..." banner on stdout
    assert code == 0
    assert set(doc) == PATTERNS_KEYS
    assert doc["valid"] is False
    assert doc["analysis"] is None
    assert doc["n_sessions"] == 0


def test_patterns_json_valid_analysis_is_passed_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import tes.intelligence.cache as cache_mod

    fake = {
        "valid": True,
        "k": 3,
        "n_sessions": 40,
        "status": "ok",
        "domain_of_validity": "dov",
        "archetypes": [],
        "anomaly_count": 2,
    }
    monkeypatch.setattr(cache_mod, "get_or_compute_intelligence", lambda **k: fake)
    code, out, _ = _run(monkeypatch, capsys, "patterns", "--json")
    doc = _doc(out)
    assert code == 0
    assert set(doc) == PATTERNS_KEYS
    assert doc["valid"] is True
    assert doc["analysis"] == fake


def test_patterns_missing_extra_prints_one_line_hint_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tes.patterns_extra import PATTERNS_EXTRA_HINT, PatternsExtraMissing

    def _missing() -> None:
        raise PatternsExtraMissing(PATTERNS_EXTRA_HINT)

    monkeypatch.setattr(cli, "require_patterns_extra", _missing)
    for argv in (["patterns"], ["patterns", "--json"]):
        code, out, err = _run(monkeypatch, capsys, *argv)
        assert code == 1
        assert out == ""
        assert err.strip().count("\n") == 0  # one line
        assert "tracegauge[patterns]" in err


def test_require_patterns_extra_passes_when_deps_import() -> None:
    from tes.patterns_extra import require_patterns_extra

    require_patterns_extra()  # numpy, scikit-learn, scipy are installed in the test env


def test_require_patterns_extra_raises_when_a_dep_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    from tes.patterns_extra import PatternsExtraMissing, require_patterns_extra

    real = importlib.import_module

    def _fake(name: str, *a: Any, **k: Any) -> Any:
        if name == "sklearn":
            raise ImportError("No module named 'sklearn'")
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", _fake)
    with pytest.raises(PatternsExtraMissing, match=r"tracegauge\[patterns\]"):
        require_patterns_extra()


# ----------------------------------------------------------------------------- score


def test_score_json_carries_schema_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path / "proj" / "s.jsonl", PRICED)
    _, out, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge", "--json")
    doc = _doc(out)
    assert list(doc)[0] == "schema_version"
    assert doc["schema_version"] == SCHEMA_VERSION
    assert {"priced", "unpriced_models", "lever_hint", "cost_known"} <= set(doc)
    assert doc["cost_known"] is True


def test_score_json_unpriced_session_cost_is_flagged_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _session(tmp_path / "proj" / "u.jsonl", UNPRICED)
    _, out, _ = _run(monkeypatch, capsys, "score", str(path), "--no-judge", "--json")
    doc = _doc(out)
    assert doc["session_cost_usd"] == 0.0  # stays numeric, documented in JSON_OUTPUT.md
    assert doc["priced"] is False
    assert doc["cost_known"] is False
