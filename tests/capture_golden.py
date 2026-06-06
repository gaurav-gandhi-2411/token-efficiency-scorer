from __future__ import annotations

"""tests/capture_golden.py — One-shot golden fixture generator for behavior-preservation tests.

NOT a pytest test file. Run once from the repo root to generate tests/fixtures/golden_scores.json:

    python tests/capture_golden.py

Selects ~20 sessions from the 181-session pool that together cover all unique
(band_verdict × judge_present × waste_events_present) combinations (up to 2 per cell).
Serialises the full EfficiencyResult plus the raw inputs used, so
test_behavior_preservation.py can reconstruct each call exactly.
"""

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Make scripts/ importable (mirrors pattern in test_waste_detectors.py line 14)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from efficiency_score import EfficiencyResult, load_baselines, score_session  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
POOL_ADAPTED_PATH = REPO_ROOT / "data" / "corpus_pool" / "pool_adapted.jsonl"
JUDGE_SCORES_PATH = REPO_ROOT / "data" / "pool_judge_scores.jsonl"
WASTE_SIGNALS_PATH = REPO_ROOT / "data" / "pool_waste_signals.jsonl"
BASELINES_PATH = REPO_ROOT / "data" / "cc_baselines.json"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
OUTPUT_PATH = FIXTURES_DIR / "golden_scores.json"

MAX_GOLDEN = 20
MAX_PER_GROUP = 2


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------


def _load_jsonl_index(path: Path) -> dict[str, dict]:
    """Load a JSONL file and return a dict keyed by session_id."""
    index: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entry = json.loads(line)
                index[entry["session_id"]] = entry
    return index


def _load_pool_index() -> dict[str, dict]:
    """Load pool_adapted.jsonl and return a dict keyed by session_id."""
    return _load_jsonl_index(POOL_ADAPTED_PATH)


# ---------------------------------------------------------------------------
# Serialise EfficiencyResult to a plain dict (JSON-safe)
# ---------------------------------------------------------------------------


def _result_to_dict(
    result: EfficiencyResult,
    judge_entry: dict | None,
    waste_entry: dict | None,
) -> dict:
    """Serialise an EfficiencyResult plus its raw call inputs to a JSON-safe dict."""
    return {
        "session_id": result.session_id,
        "task_type": result.task_type,
        "real_tokens": result.real_tokens,
        "scope_status": result.scope_status,
        "baseline_available": result.baseline_available,
        "p25": result.p25,
        "p75": result.p75,
        "median": result.median,
        "band_verdict": result.band_verdict,
        "interpretation": result.interpretation,
        "judge_verdict": result.judge_verdict,
        "judge_score": result.judge_score,
        "judge_reasoning": result.judge_reasoning,
        "waste_event_count": result.waste_event_count,
        "waste_events": result.waste_events,
        # Raw inputs stored so tests can reconstruct the call exactly
        "_input_judge_entry": judge_entry,
        "_input_waste_entry": waste_entry,
    }


# ---------------------------------------------------------------------------
# Group key for stratified sampling
# ---------------------------------------------------------------------------


def _group_key(result: EfficiencyResult) -> tuple[str, bool, bool]:
    """Return a (band_verdict, judge_present, waste_present) tuple for stratification."""
    return (
        result.band_verdict,
        result.judge_verdict is not None,
        result.waste_event_count > 0,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Generate tests/fixtures/golden_scores.json from the 181-session pool."""
    print("Loading data files …")
    pool_index = _load_pool_index()
    judge_index = _load_jsonl_index(JUDGE_SCORES_PATH) if JUDGE_SCORES_PATH.exists() else {}
    waste_index = _load_jsonl_index(WASTE_SIGNALS_PATH) if WASTE_SIGNALS_PATH.exists() else {}
    baselines = load_baselines(BASELINES_PATH)

    print(f"  pool sessions : {len(pool_index)}")
    print(f"  judge entries : {len(judge_index)}")
    print(f"  waste entries : {len(waste_index)}")

    # Score all sessions and collect (result, judge_entry, waste_entry)
    all_results: list[tuple[EfficiencyResult, dict | None, dict | None]] = []
    for session_id, record in pool_index.items():
        je = judge_index.get(session_id)
        we = waste_index.get(session_id)
        result = score_session(record, baselines, judge_entry=je, waste_entry=we)
        all_results.append((result, je, we))

    # Stratified sampling: up to MAX_PER_GROUP per (band_verdict × judge × waste) cell
    group_counts: dict[tuple[str, bool, bool], int] = {}
    selected: list[dict] = []

    for result, je, we in all_results:
        if len(selected) >= MAX_GOLDEN:
            break
        key = _group_key(result)
        count = group_counts.get(key, 0)
        if count < MAX_PER_GROUP:
            selected.append(_result_to_dict(result, je, we))
            group_counts[key] = count + 1

    # Write fixture
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(selected, indent=2), encoding="utf-8")
    print(f"\nWrote {len(selected)} golden entries to {OUTPUT_PATH}")

    # Coverage summary
    print("\nCoverage summary (band_verdict × judge_present × waste_present):")
    header = f"  {'band_verdict':<14}  {'judge_present':<14}  {'waste_present':<14}  count"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for key, count in sorted(group_counts.items()):
        band, judge_p, waste_p = key
        print(f"  {band:<14}  {str(judge_p):<14}  {str(waste_p):<14}  {count}")

    print(f"\nTotal unique cells covered: {len(group_counts)}")


if __name__ == "__main__":
    main()
