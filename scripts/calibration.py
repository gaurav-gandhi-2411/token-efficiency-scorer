"""
calibration.py — Compute Spearman rho calibration between judge, objective proxy,
and (optionally) LLM provisional ratings and human gold.

Three correlation pairs:
  1. judge_score vs objective_efficiency_proxy  (TARGET A HEADLINE)
  2. judge_score vs llm_provisional_rating/5    (secondary)
  3. llm_provisional_rating/5 vs objective_efficiency_proxy  (sanity)

Kill criterion: headline rho >= 0.55 to proceed.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import spearmanr

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROXY_PATH = ROOT / "data" / "objective_proxy.jsonl"
JUDGE_PATH = ROOT / "data" / "judge_scores.jsonl"
CALIBRATION_DIR = ROOT / "data" / "calibration"

SEED = 42
KILL_CRITERION_RHO = 0.55


def bootstrap_spearman(
    x: np.ndarray, y: np.ndarray, n_boot: int = 2000, seed: int = SEED
) -> tuple[float, float, float]:
    """Return (rho, ci_low, ci_high) at 95% via bootstrap.

    Args:
        x: First variable array.
        y: Second variable array.
        n_boot: Number of bootstrap resamples.
        seed: Random seed for reproducibility.

    Returns:
        Tuple of (rho, ci_low, ci_high).
    """
    rho, _ = spearmanr(x, y)
    rng = np.random.default_rng(seed)
    n = len(x)
    boot_rhos: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        r, _ = spearmanr(x[idx], y[idx])
        boot_rhos.append(float(r))
    ci = np.percentile(boot_rhos, [2.5, 97.5])
    return float(rho), float(ci[0]), float(ci[1])


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load all non-empty lines from a JSONL file."""
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            with contextlib.suppress(json.JSONDecodeError):
                rows.append(json.loads(line))
    return rows


def _load_proxy() -> dict[str, float]:
    """Load objective proxy scores keyed by session_id."""
    return {r["session_id"]: r["objective_efficiency_proxy"] for r in _load_jsonl(PROXY_PATH)}


def _load_judge() -> dict[str, float]:
    """Load judge scores keyed by session_id."""
    return {r["session_id"]: r["judge_score"] for r in _load_jsonl(JUDGE_PATH)}


def _load_llm_ratings(path: Path) -> dict[str, float]:
    """Load LLM provisional ratings normalized to [0,1] by dividing by 5."""
    return {r["session_id"]: r["llm_provisional_rating"] / 5.0 for r in _load_jsonl(path)}


def _load_human_ratings(path: Path) -> dict[str, float]:
    """Load human gold ratings normalized to [0,1] by dividing by 5."""
    return {r["session_id"]: r["efficiency_rating"] / 5.0 for r in _load_jsonl(path)}


def _load_scaffold_map() -> dict[str, str]:
    """Build session_id -> scaffold group ('swe_agent' or 'openhands')."""
    taxonomy_path = ROOT / "data" / "validation-corpus" / "taxonomy" / "task_taxonomy.json"
    taxonomy: list[dict[str, Any]] = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    groups: dict[str, str] = {}
    for row in taxonomy:
        sc = str(row.get("scaffold", ""))
        groups[row["session_id"]] = "swe_agent" if sc == "swe_agent" else "openhands"
    return groups


def _format_rho(rho: float, ci_low: float, ci_high: float) -> str:
    """Format rho with CI for display."""
    return f"rho={rho:.2f} [CI: {ci_low:.2f}, {ci_high:.2f}]"


def _compute_correlation_block(
    name: str,
    x: dict[str, float],
    y: dict[str, float],
    scaffold_map: dict[str, str],
) -> dict[str, Any]:
    """Compute overall + per-scaffold Spearman rho for two score dictionaries.

    Args:
        name: Human-readable name for this correlation pair.
        x: First score dict (session_id -> value).
        y: Second score dict (session_id -> value).
        scaffold_map: session_id -> scaffold group.

    Returns:
        Dict with overall rho/CI and per-scaffold breakdown.
    """
    common = sorted(set(x) & set(y))
    if not common:
        return {"name": name, "n": 0, "error": "no overlapping sessions"}

    x_arr = np.array([x[sid] for sid in common])
    y_arr = np.array([y[sid] for sid in common])
    overall_rho, ci_low, ci_high = bootstrap_spearman(x_arr, y_arr)

    # Per-scaffold breakdown
    per_scaffold: dict[str, dict[str, Any]] = {}
    for group in ("swe_agent", "openhands"):
        group_ids = [sid for sid in common if scaffold_map.get(sid) == group]
        if len(group_ids) < 5:
            per_scaffold[group] = {"n": len(group_ids), "rho": None}
            continue
        gx = np.array([x[sid] for sid in group_ids])
        gy = np.array([y[sid] for sid in group_ids])
        g_rho, g_ci_low, g_ci_high = bootstrap_spearman(gx, gy)
        per_scaffold[group] = {
            "n": len(group_ids),
            "rho": round(g_rho, 4),
            "ci_low": round(g_ci_low, 4),
            "ci_high": round(g_ci_high, 4),
        }

    return {
        "name": name,
        "n": len(common),
        "rho": round(overall_rho, 4),
        "ci_low": round(ci_low, 4),
        "ci_high": round(ci_high, 4),
        "per_scaffold": per_scaffold,
    }


def _print_block(label: str, block: dict[str, Any]) -> None:
    """Print a formatted correlation block."""
    if block.get("error"):
        print(f"  {label}: ERROR — {block['error']}")
        return
    print(
        f"  Overall:    {_format_rho(block['rho'], block['ci_low'], block['ci_high'])}"
    )
    for group in ("swe_agent", "openhands"):
        gs = block["per_scaffold"].get(group, {})
        if gs.get("rho") is None:
            print(f"  {group}:  n={gs.get('n', 0)} (too few for CI)")
        else:
            print(
                f"  {group:<14}: {_format_rho(gs['rho'], gs['ci_low'], gs['ci_high'])}"
                f"  (n={gs['n']})"
            )


def main() -> None:
    """Entry point for calibration computation."""
    parser = argparse.ArgumentParser(description="Calibration: Spearman rho across signals.")
    parser.add_argument(
        "--target-a-file",
        default=str(PROXY_PATH),
        metavar="PATH",
        help="Objective proxy JSONL (default: data/objective_proxy.jsonl)",
    )
    parser.add_argument(
        "--target-b-file",
        default=None,
        metavar="PATH",
        help="LLM provisional ratings JSONL (optional; default: data/llm_provisional_ratings.jsonl)",  # noqa: E501
    )
    parser.add_argument(
        "--llm-ratings",
        default=None,
        metavar="PATH",
        help="Alias for --target-b-file.",
    )
    parser.add_argument(
        "--human-ratings",
        default=None,
        metavar="PATH",
        help="Human gold ratings JSONL (optional; activates human-gold comparisons)",
    )
    args = parser.parse_args()

    # Resolve LLM ratings path
    llm_ratings_path: Path | None = None
    llm_path_str = args.target_b_file or args.llm_ratings
    if llm_path_str is None:
        default_llm = ROOT / "data" / "llm_provisional_ratings.jsonl"
        if default_llm.exists():
            llm_ratings_path = default_llm
    else:
        llm_ratings_path = Path(llm_path_str)

    proxy_path = Path(args.target_a_file)
    human_path: Path | None = Path(args.human_ratings) if args.human_ratings else None

    # Validate required inputs
    if not proxy_path.exists():
        print(f"ERROR: objective proxy file not found: {proxy_path}", file=sys.stderr)
        print("Run scripts/objective_proxy.py first.", file=sys.stderr)
        sys.exit(1)
    if not JUDGE_PATH.exists():
        print(f"ERROR: judge scores not found: {JUDGE_PATH}", file=sys.stderr)
        print("Run scripts/layer2_judge.py first.", file=sys.stderr)
        sys.exit(1)

    proxy = _load_proxy() if proxy_path == PROXY_PATH else {
        r["session_id"]: r["objective_efficiency_proxy"]
        for r in _load_jsonl(proxy_path)
    }
    judge = _load_judge()
    llm: dict[str, float] = (
        _load_llm_ratings(llm_ratings_path)
        if (llm_ratings_path and llm_ratings_path.exists())
        else {}
    )
    human: dict[str, float] = (
        _load_human_ratings(human_path) if human_path and human_path.exists() else {}
    )
    scaffold_map = _load_scaffold_map()

    n_all = len(set(judge) & set(proxy))
    n_human = len(set(judge) & set(proxy) & set(human)) if human else 0

    # ---- Three core correlations -------------------------------------------
    block_a = _compute_correlation_block(
        "judge vs objective proxy", judge, proxy, scaffold_map
    )
    block_b = _compute_correlation_block(
        "judge vs llm_provisional", judge, llm, scaffold_map
    ) if llm else None
    block_c = _compute_correlation_block(
        "llm_provisional vs objective proxy", llm, proxy, scaffold_map
    ) if llm else None

    # ---- Print ---------------------------------------------------------------
    print()
    print("=== CALIBRATION RESULTS ===")
    print(
        f"N sessions with all signals: {n_all}  "
        f"(judge: {len(judge)}, proxy: {len(proxy)}, "
        f"llm_rating: {len(llm) if llm else 0})"
    )
    print()
    print("TARGET A HEADLINE — judge vs objective proxy")
    _print_block("judge vs proxy", block_a)
    print()

    if block_b is not None:
        print("SECONDARY — judge vs LLM provisional (source=llm_provisional)")
        _print_block("judge vs llm", block_b)
        print()

    if block_c is not None:
        print("SANITY — LLM provisional vs objective proxy")
        _print_block("llm vs proxy", block_c)
        print()

    if human:
        block_human_judge = _compute_correlation_block(
            "judge vs human gold", judge, human, scaffold_map
        )
        block_human_proxy = _compute_correlation_block(
            "proxy vs human gold", proxy, human, scaffold_map
        )
        print(f"HUMAN GOLD — N={n_human} sessions")
        print("  judge vs human gold:")
        _print_block("judge vs human", block_human_judge)
        print("  proxy vs human gold:")
        _print_block("proxy vs human", block_human_proxy)
        print()

    headline_rho = block_a.get("rho", 0.0) or 0.0
    print(f"KILL CRITERION: headline judge-vs-proxy rho >= {KILL_CRITERION_RHO} to proceed.")
    if headline_rho >= KILL_CRITERION_RHO:
        print(f"  PASS: rho={headline_rho:.2f} >= {KILL_CRITERION_RHO}")
    else:
        print(
            f"  FAIL: rho={headline_rho:.2f} < {KILL_CRITERION_RHO}"
            " — escalate before proceeding."
        )

    # ---- Save JSON output ----------------------------------------------------
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    datestamp = datetime.now(UTC).strftime("%Y%m%d")
    out_path = CALIBRATION_DIR / f"calibration_{datestamp}.json"

    output: dict[str, Any] = {
        "computed_at": datetime.now(UTC).isoformat(),
        "n_judge": len(judge),
        "n_proxy": len(proxy),
        "n_llm_ratings": len(llm),
        "n_human_ratings": len(human),
        "kill_criterion_rho": KILL_CRITERION_RHO,
        "headline_pass": headline_rho >= KILL_CRITERION_RHO,
        "target_a_headline": block_a,
        "secondary_judge_vs_llm": block_b,
        "sanity_llm_vs_proxy": block_c,
    }
    if human:
        output["human_gold_judge"] = block_human_judge  # type: ignore[possibly-undefined]
        output["human_gold_proxy"] = block_human_proxy  # type: ignore[possibly-undefined]

    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nCalibration JSON written to {out_path}")


if __name__ == "__main__":
    main()
