from __future__ import annotations

"""Pins for the shipped adapter-v2 baseline: it must be exactly what eval/manifest.json builds."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rebuild_baselines as rb  # noqa: E402
from tes.adapt import ADAPTER_VERSION  # noqa: E402
from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines  # noqa: E402


def test_shipped_baseline_is_exactly_what_the_manifest_builds() -> None:
    """Reproducibility pin: the bundled JSON is a pure function of eval/manifest.json."""
    manifest = json.loads((ROOT / "eval" / "manifest.json").read_text(encoding="utf-8"))
    shipped = load_baselines(BUNDLED_BASELINES_PATH)
    assert shipped == rb.build_baselines(manifest)


def test_shipped_baseline_provenance_and_schema_compatibility() -> None:
    shipped = load_baselines(BUNDLED_BASELINES_PATH)
    prov = shipped["provenance"]
    assert prov["adapter_version"] == ADAPTER_VERSION == 2
    assert prov["single_developer"] is True and prov["quality_gated"] is False
    assert shipped["total_baseline_sessions"] == sum(prov["n_per_task_type"].values())
    for key in ("generated", "token_measure", "scope_gates", "types", "baseline_population"):
        assert key in shipped
    for cell in shipped["types"].values():
        if cell["available"]:
            assert cell["n"] >= rb.MIN_N
            assert cell["p25"] <= cell["median"] <= cell["p75"]
            for name in ("p25", "median", "p75"):
                lo, hi = cell["ci95"][name]
                assert lo <= cell[name] <= hi
        else:
            assert cell["n"] < rb.MIN_N and "p25" not in cell


def test_manifest_holds_no_transcript_text_or_paths() -> None:
    m = json.loads((ROOT / "eval" / "manifest.json").read_text(encoding="utf-8"))
    raw = json.dumps(m["sessions"])
    assert "\\" not in raw and ".claude" not in raw and "Users" not in raw
    allowed = {
        "session_id",
        "task_type",
        "turn_count",
        "real_tokens",
        "size_bytes",
        "mtime_utc",
        "sha256",
        "in_phase0_set_b",
        "included",
        "exclusion_reason",
    }
    assert all(set(e) == allowed for e in m["sessions"])
