from __future__ import annotations

"""Build per-type token-efficiency baselines from the local CC session pool.

Reads an adapted-session pool (JSONL) and a judge-scores file (JSONL), applies the strict
gate (verdict == "MUCH_BETTER"), excludes armand0e/Kimi sessions (incompatible token
accounting), and computes per-task-type token distribution percentiles.

The input paths are explicit arguments: the maintainer's own pool is not in the repository
(the shipped baseline is the maintainer's unpublished local measurement, see eval/manifest.json
for the ids and hashes it was built from). The test suite runs this script over the synthetic
fixtures instead, which is how tests/fixtures/baselines_synthetic.json is produced.

Also runs a Spearman circularity check: tests whether real_tokens is
correlated with judge_score across all scored sessions.

Usage:
    python scripts/build_baselines.py --pool POOL.jsonl --judge-scores SCORES.jsonl \
        --output BASELINES.json [--min-n 10] [--label TEXT]

    # the synthetic baselines used by the tests (min-n 4 because the pool is small):
    python scripts/build_baselines.py --pool tests/fixtures/synthetic_pool.jsonl \
        --judge-scores tests/fixtures/synthetic_judge_scores.jsonl \
        --output tests/fixtures/baselines_synthetic.json --min-n 4 --label synthetic
"""

import argparse
import json
import sys
from pathlib import Path

from scipy.stats import spearmanr  # type: ignore[import-untyped]

# ---------------------------------------------------------------------------
# Make the sibling script importable regardless of cwd
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from task_classifier import TASK_TYPES, classify_session  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GENERATED_DATE = "2026-06-03"
MIN_N = 10
STRICT_GATE_VERDICT = "MUCH_BETTER"

# These sessions use Kimi/no-caching token accounting — incompatible with
# the cache-aware real_tokens formula used for all other CC sessions.
ARMAND_EXCLUDE: frozenset[str] = frozenset(
    {
        "00000000-0000-4000-8000-000000000045",
        "00000000-0000-4000-8000-000000000090",
    }
)

# ---------------------------------------------------------------------------
# Token measure
# ---------------------------------------------------------------------------


def compute_real_tokens(record: dict) -> int:
    """Return real_tokens for an adapted pool session.

    real_tokens = sum over AI turns of (token_count_input - cache_read) + token_count_output

    Excludes cache_read re-accumulation. Only AI turns (role == 'ai') are
    counted; user turns carry zero meaningful token cost in this context.
    """
    total = 0
    turns: list[dict] = record.get("digest", {}).get("turns", [])
    for turn in turns:
        if turn.get("role") != "ai":
            continue
        inp: int = turn.get("token_count_input", 0)
        out: int = turn.get("token_count_output", 0)
        cache: int = turn.get("cache_read", 0)
        total += (inp - cache) + out
    return total


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_pool(path: Path) -> list[dict]:
    """Load all records from an adapted-session pool JSONL file."""
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_judge_scores(path: Path) -> dict[str, dict]:
    """Load judge scores keyed by session_id."""
    scores: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entry = json.loads(line)
                scores[entry["session_id"]] = entry
    return scores


# ---------------------------------------------------------------------------
# Percentile helpers (manual, no numpy)
# ---------------------------------------------------------------------------


def _percentile_at_index(sorted_values: list[int], n: int, fraction: float) -> int:
    """Return the element at floor(n * fraction) of a sorted list."""
    idx = int(n * fraction)
    # Clamp to valid range
    idx = max(0, min(idx, n - 1))
    return sorted_values[idx]


# ---------------------------------------------------------------------------
# Scope gate computation
# ---------------------------------------------------------------------------


def _compute_scope_gates(
    baseline_sessions: list[tuple[str, str, int]],
    pool_index: dict[str, dict],
    min_n: int = MIN_N,
) -> dict[str, dict]:
    """Compute p10 turn count per type from baseline sessions."""
    by_type: dict[str, list[int]] = {t: [] for t in TASK_TYPES}
    for sid, task_type, _ in baseline_sessions:
        if sid in pool_index:
            turns = pool_index[sid].get("turn_count", 0)
            by_type[task_type].append(turns)
    gates: dict[str, dict] = {}
    for t in TASK_TYPES:
        turn_list = sorted(by_type[t])
        n = len(turn_list)
        if n >= min_n:
            p10 = _percentile_at_index(turn_list, n, 0.10)
            gates[t] = {"p10_turns": p10}
        else:
            gates[t] = {"p10_turns": None}
    return gates


# ---------------------------------------------------------------------------
# Circularity check
# ---------------------------------------------------------------------------


def circularity_check(
    pool_index: dict[str, dict],
    judge_scores: dict[str, dict],
) -> dict:
    """Compute Spearman correlation between real_tokens and judge_score.

    Uses all scored sessions (not just strict-gate, not excluding armand0e)
    so the check covers the full range of sessions and scores.
    """
    token_vals: list[float] = []
    score_vals: list[float] = []

    for sid, score_entry in judge_scores.items():
        if sid not in pool_index:
            continue
        js = score_entry.get("judge_score")
        if js is None:
            continue
        rt = compute_real_tokens(pool_index[sid])
        token_vals.append(float(rt))
        score_vals.append(float(js))

    n = len(token_vals)
    result = spearmanr(token_vals, score_vals)
    r = float(result.statistic)
    p = float(result.pvalue)

    if abs(r) > 0.5:
        interpretation = (
            "HIGH CORRELATION — baseline tokens may be a proxy for judge quality; "
            "review before shipping"
        )
    else:
        interpretation = (
            "LOW/MODERATE — baseline tokens and judge scores are sufficiently independent"
        )

    return {
        "spearman_r": round(r, 6),
        "p_value": round(p, 6),
        "n_sessions": n,
        "interpretation": interpretation,
    }


# ---------------------------------------------------------------------------
# Baseline computation
# ---------------------------------------------------------------------------


def build_baselines(
    pool: list[dict],
    judge_scores: dict[str, dict],
    min_n: int = MIN_N,
    label: str | None = None,
) -> dict:
    """Compute per-type baseline statistics for MUCH_BETTER local sessions.

    Excludes ARMAND_EXCLUDE sessions (incompatible token accounting).
    Types with n < min_n get available=False. A non-empty label is recorded in the output
    (the synthetic test baselines carry label "synthetic").
    """
    pool_index: dict[str, dict] = {r["session_id"]: r for r in pool}

    # Strict-gate: MUCH_BETTER only, armand0e excluded
    baseline_sessions: list[tuple[str, str, int]] = []  # (session_id, task_type, real_tokens)

    for sid, score_entry in judge_scores.items():
        if score_entry.get("verdict") != STRICT_GATE_VERDICT:
            continue
        if sid in ARMAND_EXCLUDE:
            continue
        if sid not in pool_index:
            continue
        record = pool_index[sid]
        task_type = classify_session(record)
        rt = compute_real_tokens(record)
        baseline_sessions.append((sid, task_type, rt))

    total_baseline = len(baseline_sessions)

    # Group by type
    by_type: dict[str, list[int]] = {t: [] for t in TASK_TYPES}
    for _, task_type, rt in baseline_sessions:
        by_type[task_type].append(rt)

    # Build per-type output
    types_output: dict[str, dict] = {}
    for task_type in TASK_TYPES:
        token_list = sorted(by_type[task_type])
        n = len(token_list)
        if n < min_n:
            types_output[task_type] = {"available": False, "n": n}
        else:
            median = _percentile_at_index(token_list, n, 0.5)
            p25 = _percentile_at_index(token_list, n, 0.25)
            p75 = _percentile_at_index(token_list, n, 0.75)
            types_output[task_type] = {
                "available": True,
                "n": n,
                "median": median,
                "p25": p25,
                "p75": p75,
            }

    # Circularity check uses ALL scored sessions (full range, no exclusions)
    circ = circularity_check(pool_index, judge_scores)

    scope_gates = _compute_scope_gates(baseline_sessions, pool_index, min_n)

    result = {
        "generated": GENERATED_DATE,
        "token_measure": (
            "real_tokens = sum_ai_turns(token_count_input - cache_read + token_count_output)"
        ),
        "strict_gate": "MUCH_BETTER only",
        "baseline_population": (
            "local Claude CC sessions only "
            "(armand0e/Kimi excluded: no-cache token accounting incompatible)"
        ),
        "total_baseline_sessions": total_baseline,
        "circularity": circ,
        "scope_gates": scope_gates,
        "types": types_output,
    }
    if label:
        result["label"] = label
    return result


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------


def print_summary(baselines: dict) -> None:
    """Print a human-readable summary of the baselines to stdout."""
    print("=== CC BASELINES SUMMARY ===")
    print()
    print(f"Generated:            {baselines['generated']}")
    print(f"Token measure:        {baselines['token_measure']}")
    print(f"Strict gate:          {baselines['strict_gate']}")
    print(f"Total baseline sess.: {baselines['total_baseline_sessions']}")
    print()

    circ = baselines["circularity"]
    print("Circularity check (Spearman, full scored population):")
    print(f"  r={circ['spearman_r']:.4f}  p={circ['p_value']:.4f}  n={circ['n_sessions']}")
    print(f"  {circ['interpretation']}")
    print()

    col_w = 16
    print(f"  {'Type':<{col_w}} {'n':>5}  {'p25':>8}  {'median':>8}  {'p75':>8}  {'available':>10}")
    print("  " + "-" * (col_w + 48))
    for task_type, info in baselines["types"].items():
        if info["available"]:
            print(
                f"  {task_type:<{col_w}} {info['n']:>5}  {info['p25']:>8,}  "
                f"{info['median']:>8,}  {info['p75']:>8,}  {'yes':>10}"
            )
        else:
            print(
                f"  {task_type:<{col_w}} {info['n']:>5}  {'—':>8}  {'—':>8}  {'—':>8}  {'no':>10}"
            )
    print()

    print("Scope gates (p10 turns per type):")
    for t, gate in baselines.get("scope_gates", {}).items():
        p10 = gate.get("p10_turns")
        print(f"  {t:<16} p10_turns = {p10}")
    print()
    print("=== DONE ===")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """Build baselines from explicit input paths and write them to --output."""
    parser = argparse.ArgumentParser(description="Build per-type token-efficiency baselines.")
    parser.add_argument("--pool", type=Path, required=True, help="adapted-session pool (JSONL)")
    parser.add_argument(
        "--judge-scores", type=Path, required=True, help="judge scores keyed by session_id (JSONL)"
    )
    parser.add_argument("--output", type=Path, required=True, help="baselines JSON to write")
    parser.add_argument(
        "--min-n",
        type=int,
        default=MIN_N,
        help=f"minimum strict-gate sessions for a type to get a band (default {MIN_N})",
    )
    parser.add_argument("--label", default=None, help="free-text label stored in the output")
    args = parser.parse_args(argv)

    pool = _load_pool(args.pool)
    judge_scores = _load_judge_scores(args.judge_scores)

    baselines = build_baselines(pool, judge_scores, min_n=args.min_n, label=args.label)

    # LF on every platform, like the committed file
    with args.output.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(baselines, indent=2) + "\n")
    print(f"Written: {args.output}")
    print()
    print_summary(baselines)


if __name__ == "__main__":
    main()
