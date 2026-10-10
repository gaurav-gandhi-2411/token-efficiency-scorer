from __future__ import annotations

"""Tests for scripts/rebuild_baselines.py and the shipped adapter-v2 baseline it produces.

Hermetic: every transcript root is a tmp_path; nothing reads ~/.claude.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rebuild_baselines as rb  # noqa: E402
from tes.adapt import ADAPTER_VERSION  # noqa: E402
from tes.score import _score_session_impl  # noqa: E402


def _entry(
    sid: str, task_type: str, real_tokens: int, turns: int = 100, **kw: Any
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": sid,
        "task_type": task_type,
        "turn_count": turns,
        "real_tokens": real_tokens,
        "size_bytes": 1,
        "mtime_utc": "2026-10-01T00:00:00Z",
        "sha256": "0" * 64,
        "in_phase0_set_b": False,
        "included": True,
        "exclusion_reason": None,
    }
    base.update(kw)
    return base


def _manifest(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "manifest_version": 1,
        "frozen_at": "2026-10-10",
        "adapter_version": ADAPTER_VERSION,
        "sessions": entries,
    }


def test_percentile_rule_is_floor_n_times_q() -> None:
    vals = list(range(100, 200))
    assert rb.percentile_at_index(vals, 0.25) == 125
    assert rb.percentile_at_index(vals, 0.5) == 150
    assert rb.percentile_at_index(vals, 0.75) == 175
    assert rb.percentile_at_index([7], 0.75) == 7


def test_bootstrap_ci_is_deterministic_and_brackets_the_estimate() -> None:
    vals = sorted([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120])
    a = rb.bootstrap_ci(vals, 0.5)
    assert a == rb.bootstrap_ci(vals, 0.5)
    assert a[0] <= rb.percentile_at_index(vals, 0.5) <= a[1]
    assert a[0] >= vals[0] and a[1] <= vals[-1]


def test_wilson_interval_known_values() -> None:
    lo, hi = rb.wilson_interval(36, 50)
    assert lo == pytest.approx(0.5832, abs=0.002)
    assert hi == pytest.approx(0.8253, abs=0.002)
    assert rb.wilson_interval(0, 0) == (0.0, 0.0)
    assert rb.wilson_interval(0, 10)[0] == 0.0
    assert rb.wilson_interval(10, 10)[1] == pytest.approx(1.0)


def test_cell_below_min_n_stays_inactive_and_has_no_gate() -> None:
    entries = [_entry(f"a{i}", "debug-fix", 1000 + i) for i in range(rb.MIN_N - 1)]
    entries += [_entry(f"b{i}", "ml-eval", 2000 + i) for i in range(rb.MIN_N)]
    bands = rb.build_bands(entries)
    assert bands["types"]["debug-fix"] == {"available": False, "n": rb.MIN_N - 1}
    assert bands["scope_gates"]["debug-fix"] == {"p10_turns": None}
    assert bands["types"]["ml-eval"]["available"] is True
    assert set(bands["types"]["ml-eval"]["ci95"]) == {"p25", "median", "p75"}
    assert bands["types"]["feature-build"] == {"available": False, "n": 0}


def test_band_verdict_matches_the_scorer_on_a_synthetic_session() -> None:
    entries = [_entry(f"s{i}", "debug-fix", 1000 * (i + 1), turns=50 + i) for i in range(12)]
    baselines = rb.build_baselines(_manifest(entries))
    for tokens, turns in [(500, 80), (6000, 80), (50000, 80), (6000, 3)]:
        record = {
            "session_id": "x",
            "turn_count": turns,
            "digest": {
                "task_description": "fix the failing test",
                "turns": [
                    {
                        "role": "ai",
                        "token_count_input": tokens,
                        "token_count_output": 0,
                        "cache_read": 0,
                    }
                ],
            },
        }
        res = _score_session_impl(record, baselines)
        assert res.task_type == "debug-fix"
        assert rb.band_verdict("debug-fix", turns, tokens, baselines) == res.band_verdict


def test_build_is_a_pure_function_of_the_manifest() -> None:
    entries = [_entry(f"s{i}", "infra-deploy", 500 + 37 * i) for i in range(15)]
    m = _manifest(entries)
    a = json.dumps(rb.build_baselines(m), sort_keys=True)
    assert a == json.dumps(rb.build_baselines(m), sort_keys=True)
    out = rb.build_baselines(m)
    assert out["provenance"]["manifest_sha256"] == rb.manifest_sha256(m)
    m2 = _manifest(entries[:-1])
    assert rb.build_baselines(m2)["provenance"]["manifest_sha256"] != rb.manifest_sha256(m)
    assert out["provenance"]["single_developer"] is True
    assert out["provenance"]["quality_gated"] is False
    assert out["provenance"]["adapter_version"] == ADAPTER_VERSION
    assert out["total_baseline_sessions"] == 15


def test_excluded_sessions_never_enter_a_band() -> None:
    entries = [_entry(f"s{i}", "debug-fix", 100 + i) for i in range(10)]
    entries.append(_entry("z", "debug-fix", 0, included=False, exclusion_reason="no_usage"))
    bands = rb.build_baselines(_manifest(entries))
    assert bands["types"]["debug-fix"]["n"] == 10
    assert bands["provenance"]["excluded"] == {"no_usage": 1}


def test_leave_one_out_differs_from_in_sample_for_an_outlier() -> None:
    # One extreme session: in-sample it can sit inside the band it helped set,
    # leave-one-out removes that influence.
    entries = [_entry(f"s{i}", "debug-fix", 1000 + i) for i in range(11)]
    entries.append(_entry("big", "debug-fix", 10**6, in_phase0_set_b=True))
    res = rb.evaluate_set(_manifest(entries), {"big"})
    assert res["n_eval"] == 1
    assert res["leave_one_out"]["above_p75"]["n"] == 1
    assert res["disjoint_only"]["above_p75"]["n"] == 1
    # the 11 disjoint sessions are enough for the cell to activate
    assert res["disjoint_cells_active"] == ["debug-fix"]


def test_disjoint_cell_below_min_n_makes_the_eval_session_unavailable() -> None:
    entries = [_entry(f"s{i}", "debug-fix", 1000 + i) for i in range(5)]
    entries += [_entry(f"e{i}", "debug-fix", 1000 + i, in_phase0_set_b=True) for i in range(6)]
    res = rb.evaluate_set(_manifest(entries), {f"e{i}" for i in range(6)})
    assert res["disjoint_only"]["unavailable"]["n"] == 6
    assert res["disjoint_cells_active"] == []


def test_freeze_manifest_excludes_active_and_empty_and_stores_no_paths(tmp_path: Path) -> None:
    import os
    import time

    proj = tmp_path / "C--Users-someone-secretproject"
    proj.mkdir()
    (proj / "sub").mkdir()
    now = time.time()
    specs = {"old1": 1000, "old0": 0, "fresh": 1000}
    for sid in specs:
        (proj / f"{sid}.jsonl").write_text(f"{sid}\n", encoding="utf-8")
    old = now - 10 * 3600
    for sid in ("old1", "old0"):
        os.utime(proj / f"{sid}.jsonl", (old, old))
    # a subagent transcript must not be listed
    sa = proj / "old1" / "subagents"
    sa.mkdir(parents=True)
    (sa / "agent-1.jsonl").write_text("x\n", encoding="utf-8")

    def fake_adapt(path: Path) -> dict[str, Any]:
        rt = specs[path.stem]
        return {
            "session_id": path.stem,
            "turn_count": 5,
            "digest": {
                "task_description": "deploy it",
                "turns": [
                    {
                        "role": "ai",
                        "token_count_input": rt,
                        "token_count_output": 0,
                        "cache_read": 0,
                    }
                ],
            },
        }

    m = rb.freeze_manifest(
        tmp_path, now=now, frozen_at="2026-10-10", eval_ids={"old1"}, adapt=fake_adapt
    )
    by_id = {e["session_id"]: e for e in m["sessions"]}
    assert set(by_id) == {"old1", "old0", "fresh"}
    assert by_id["old1"]["included"] is True and by_id["old1"]["in_phase0_set_b"] is True
    assert by_id["old1"]["task_type"] == "infra-deploy"
    assert by_id["old0"]["exclusion_reason"] == "no_usage"
    assert by_id["fresh"]["exclusion_reason"] == "active_within_quiescent_window"
    assert by_id["fresh"]["sha256"] is None and by_id["old1"]["sha256"] is not None
    blob = json.dumps(m)
    assert "secretproject" not in blob and str(tmp_path) not in blob
    assert rb.verify_sources(m, tmp_path) == {"hash_match": 2, "missing": 0, "hash_mismatch": 0}
    (proj / "old1.jsonl").write_text("changed\n", encoding="utf-8")
    assert rb.verify_sources(m, tmp_path)["hash_mismatch"] == 1
