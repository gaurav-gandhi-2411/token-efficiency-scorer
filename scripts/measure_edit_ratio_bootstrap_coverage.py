from __future__ import annotations

"""scripts/measure_edit_ratio_bootstrap_coverage.py — ZZ1: coverage
measurement for the cost-per-edit / cost-per-100-lines ratio-of-sums
bootstrap CI, BEFORE adopting any constant for it.

**Why this exists, and why adk-tracegauge's own confidence=0.98/n_boot=
10,000/min_n=30 are NOT inherited here (ZZ1.1):** adk-tracegauge's
_regression.py validates a two-sample DIFFERENCE-IN-MEANS estimator
(mean(current) - mean(baseline)), resampling each side independently. XX2's
estimator is a RATIO-OF-SUMS (sum(cost) / sum(edits)) over a single sample
of sessions, resampled as (cost_i, edits_i) PAIRS so the real session-level
correlation between cost and edit count survives resampling. A ratio
estimator is a fundamentally different, and typically more skewed, sampling
distribution than a difference of means -- the percentile bootstrap's known
weak spot is exactly a skewed statistic at small n (Efron & Tibshirani
1993), which is precisely the shape here. Nothing about adk-tracegauge's
own validated constants transfers without being re-measured for this
estimator; this script does that measurement.

**Generator**: synthetic (cost_i, edits_i) session pairs with a KNOWN true
ratio r. edits_i ~ lognormal (skewed, positive, integer-valued -- realistic
session edit counts span roughly one order of magnitude with a long right
tail, matching this project's own real corpus: a single real session
scored during this engagement had real_tokens spanning several orders of
magnitude from typical). cost_i = r * edits_i * noise_i, noise_i ~
lognormal with E[noise_i]=1 (mean-corrected location) so the ratio
estimator is consistent (sum(cost)/sum(edits) -> r as n -> inf by the LLN)
and cost is correlated with edits at the session level (a session with more
edits tends to cost more) without being a fixed multiple of it.

**Methods measured**: percentile bootstrap (the naive approach) and BCa
(bias-corrected and accelerated -- Efron 1987) side by side at every grid
cell. ZZ1.4 is explicit that Phase 4's "percentile vs BCa, no improvement"
result was for a DIFFERENT estimator (difference-in-means) and does not
transfer -- this script re-measures BCa's actual value for THIS estimator
rather than assuming either the adk-tracegauge finding or its opposite.

**Coverage**: for each (confidence, n) cell, >=2,000 independent trials.
Each trial draws n fresh sessions from the known-r generator, computes both
interval methods, and records whether the true r fell inside each interval.
The trial-level "covered" indicator is a Bernoulli variable; the reported
coverage rate carries a Wilson score CI (same method, same rationale, as
adk-tracegauge's own measure_regression_confidence_grid.py -- see that
script's docstring for why Wilson, not the naive normal approximation, in
this near-nominal regime).

Run: ``uv run python scripts/measure_edit_ratio_bootstrap_coverage.py``
"""

import json
import time
from pathlib import Path

import numpy as np
from scipy import stats

CONFIDENCE_GRID = [0.95, 0.98, 0.99]
N_GRID = [10, 30, 50, 100, 250]
N_TRIALS = 2_000  # ZZ1.3's stated floor
N_BOOT_SURVEY = 10_000  # generous starting point for the coverage grid itself (see module docstring: NOT adopted as final without ZZ1.6's separate sufficiency check)
TRUE_RATIO = 2.50  # arbitrary but fixed -- $2.50/edit, a plausible real figure
EDIT_LOG_MEAN = 1.5  # median edits/session = exp(1.5) ~= 4.5
EDIT_LOG_SIGMA = 1.0  # realistic skew: ~90th pct edits/session ~= exp(1.5+1.28) ~= 20
NOISE_SIGMA = 0.7  # session-level cost noise around the true per-edit rate

SEED_BASE = 2_200_000
"""Distinct from every seed base already in use across both repos (see
adk-tracegauge's measure_regression_confidence_grid.py for the registry:
90_000 / 600_000 / 700_000 / 800_000 / 900_000 / 1_000_000 / 1_100_000)."""

WILSON_Z = 1.959963984540054  # 95% two-sided normal quantile, exact


def wilson_score_interval(successes: int, n: int, z: float = WILSON_Z) -> tuple[float, float]:
    """Wilson score CI for a binomial proportion. See adk-tracegauge's
    measure_regression_confidence_grid.py for the full rationale (same
    method, ported here rather than imported -- these are independent
    packages by design, see ZZ2)."""
    if n == 0:
        return (0.0, 1.0)
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = phat + z2 / (2.0 * n)
    margin = z * np.sqrt((phat * (1.0 - phat) / n) + (z2 / (4.0 * n * n)))
    lower = (center - margin) / denom
    upper = (center + margin) / denom
    return (max(0.0, float(lower)), min(1.0, float(upper)))


def generate_sessions(rng: np.random.Generator, n: int) -> tuple[np.ndarray, np.ndarray]:
    """(cost, edits) arrays, length n, with known TRUE_RATIO = sum(cost)/sum(edits) in expectation."""
    log_edits = rng.normal(loc=EDIT_LOG_MEAN, scale=EDIT_LOG_SIGMA, size=n)
    edits = np.maximum(1, np.round(np.exp(log_edits))).astype(np.float64)
    log_noise = rng.normal(loc=-(NOISE_SIGMA**2) / 2.0, scale=NOISE_SIGMA, size=n)
    noise = np.exp(log_noise)
    cost = TRUE_RATIO * edits * noise
    return cost, edits


def bootstrap_replicates(rng: np.random.Generator, cost: np.ndarray, edits: np.ndarray, n_boot: int) -> np.ndarray:
    """Resample (cost_i, edits_i) PAIRS with replacement -- preserves the
    session-level cost/edits correlation. Returns the n_boot ratio-of-sums
    replicates, vectorized (one index matrix, one pass)."""
    n = len(cost)
    idx = rng.integers(0, n, size=(n_boot, n))
    boot_cost = cost[idx].sum(axis=1)
    boot_edits = edits[idx].sum(axis=1)
    return boot_cost / boot_edits


def percentile_interval(replicates: np.ndarray, confidence: float) -> tuple[float, float]:
    alpha = 1.0 - confidence
    lo = np.percentile(replicates, 100 * alpha / 2)
    hi = np.percentile(replicates, 100 * (1 - alpha / 2))
    return float(lo), float(hi)


def bca_interval(
    replicates: np.ndarray, cost: np.ndarray, edits: np.ndarray, point_estimate: float, confidence: float
) -> tuple[float, float]:
    """Standard BCa (Efron 1987): bias-correction z0 from the fraction of
    bootstrap replicates below the point estimate, acceleration a from the
    jackknife (leave-one-session-out) skewness of the ratio estimator."""
    n = len(cost)
    alpha = 1.0 - confidence

    # z0: bias correction
    prop_below = float(np.mean(replicates < point_estimate))
    prop_below = min(max(prop_below, 1e-6), 1 - 1e-6)  # guard against 0/1 -> +/-inf
    z0 = stats.norm.ppf(prop_below)

    # a: acceleration, via jackknife
    total_cost, total_edits = cost.sum(), edits.sum()
    jk = (total_cost - cost) / (total_edits - edits)  # leave-one-out ratios, vectorized
    jk_mean = jk.mean()
    num = np.sum((jk_mean - jk) ** 3)
    den = 6.0 * (np.sum((jk_mean - jk) ** 2) ** 1.5)
    a = num / den if den != 0 else 0.0

    z_lo = stats.norm.ppf(alpha / 2)
    z_hi = stats.norm.ppf(1 - alpha / 2)

    def _adjusted_percentile(z: float) -> float:
        num_ = z0 + z
        denom_ = 1 - a * num_
        adj_z = z0 + num_ / denom_ if denom_ != 0 else z0 + num_
        return float(stats.norm.cdf(adj_z)) * 100

    p_lo = min(max(_adjusted_percentile(z_lo), 0.0), 100.0)
    p_hi = min(max(_adjusted_percentile(z_hi), 0.0), 100.0)
    lo, hi = np.percentile(replicates, [p_lo, p_hi])
    return float(lo), float(hi)


def run_coverage_grid(
    confidence_grid: list[float] = CONFIDENCE_GRID,
    n_grid: list[int] = N_GRID,
    n_trials: int = N_TRIALS,
    n_boot: int = N_BOOT_SURVEY,
) -> dict:
    """Returns {"percentile": {(conf,n): (covered, n_trials)}, "bca": {...}}.
    Trial-sharing across confidence (same underlying data, same bootstrap
    replicate set, per trial): each trial's replicate array is computed
    ONCE per n and reused across all 3 confidence levels for BOTH methods --
    same deliberate design as adk-tracegauge's own grid script, and for the
    same reason (a genuine matched comparison across the confidence column,
    not 3 independently-noisy sub-measurements; does not change what any
    individual cell estimates)."""
    percentile_covered = {(c, n): 0 for c in confidence_grid for n in n_grid}
    bca_covered = {(c, n): 0 for c in confidence_grid for n in n_grid}

    for n in n_grid:
        for trial in range(n_trials):
            seed = SEED_BASE + hash((n, trial)) % 1_000_000
            data_rng = np.random.default_rng(seed)
            cost, edits = generate_sessions(data_rng, n)
            point_estimate = cost.sum() / edits.sum()

            boot_rng = np.random.default_rng(seed + 500_000)  # distinct stream from data gen
            replicates = bootstrap_replicates(boot_rng, cost, edits, n_boot)

            for confidence in confidence_grid:
                lo, hi = percentile_interval(replicates, confidence)
                if lo <= TRUE_RATIO <= hi:
                    percentile_covered[(confidence, n)] += 1

                lo, hi = bca_interval(replicates, cost, edits, point_estimate, confidence)
                if lo <= TRUE_RATIO <= hi:
                    bca_covered[(confidence, n)] += 1

    return {
        "percentile": {k: (v, n_trials) for k, v in percentile_covered.items()},
        "bca": {k: (v, n_trials) for k, v in bca_covered.items()},
    }


def measure_n_boot_sufficiency(
    confidence: float, n: int, n_boot_options: list[int], n_repeats: int = 200
) -> dict[int, dict[str, float]]:
    """ZZ1.6: for a FIXED dataset (single seed), re-run the bootstrap
    n_repeats times at each n_boot, measuring the std dev of the resulting
    interval's lower/upper bound across repeats. n_boot is "sufficient"
    once that replicate-to-replicate std dev stops shrinking materially."""
    seed = SEED_BASE + hash((n, "n_boot_check")) % 1_000_000
    data_rng = np.random.default_rng(seed)
    cost, edits = generate_sessions(data_rng, n)

    results: dict[int, dict[str, float]] = {}
    for n_boot in n_boot_options:
        los, his = [], []
        for r in range(n_repeats):
            boot_rng = np.random.default_rng(seed + 900_000 + r)
            replicates = bootstrap_replicates(boot_rng, cost, edits, n_boot)
            lo, hi = percentile_interval(replicates, confidence)
            los.append(lo)
            his.append(hi)
        results[n_boot] = {
            "lower_std": float(np.std(los)),
            "upper_std": float(np.std(his)),
            "lower_mean": float(np.mean(los)),
            "upper_mean": float(np.mean(his)),
        }
    return results


def _print_grid(grid: dict, label: str, confidence_grid: list[float], n_grid: list[int]) -> None:
    print(f"\n=== {label}: coverage rate [Wilson 95% CI] (nominal = confidence) ===")
    header = "n\\confidence".ljust(14) + "".join(f"{c:>24}" for c in confidence_grid)
    print(header)
    for n in n_grid:
        cells = []
        for c in confidence_grid:
            covered, trials = grid[(c, n)]
            phat = covered / trials
            lo, hi = wilson_score_interval(covered, trials)
            cells.append(f"{phat:.4f} [{lo:.4f},{hi:.4f}]")
        row = str(n).ljust(14) + "".join(f"{cell:>24}" for cell in cells)
        print(row)


def main() -> int:
    total_cells = len(CONFIDENCE_GRID) * len(N_GRID)
    print(
        f"Coverage grid: {total_cells} cells x {N_TRIALS} trials, n_boot={N_BOOT_SURVEY} "
        f"({total_cells * N_TRIALS} total trials, both methods computed per trial)..."
    )
    t0 = time.time()
    grid = run_coverage_grid()
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s")

    _print_grid(grid["percentile"], "PERCENTILE bootstrap", CONFIDENCE_GRID, N_GRID)
    _print_grid(grid["bca"], "BCa bootstrap", CONFIDENCE_GRID, N_GRID)

    print("\n=== n_boot sufficiency (ZZ1.6): std dev of interval bounds across 200 repeats ===")
    for confidence, n in [(0.98, 30), (0.98, 250)]:
        print(f"\nconfidence={confidence} n={n}:")
        results = measure_n_boot_sufficiency(confidence, n, [500, 1_000, 2_000, 5_000, 10_000, 20_000])
        for n_boot, stats_ in results.items():
            print(
                f"  n_boot={n_boot:>6}: lower std={stats_['lower_std']:.5f} "
                f"upper std={stats_['upper_std']:.5f} "
                f"(lower mean={stats_['lower_mean']:.4f}, upper mean={stats_['upper_mean']:.4f})"
            )

    out_path = Path(__file__).resolve().parent.parent / "reports" / "edit_ratio_bootstrap_coverage.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        "true_ratio": TRUE_RATIO,
        "n_trials": N_TRIALS,
        "n_boot_survey": N_BOOT_SURVEY,
        "percentile": {
            f"{c}|{n}": {
                "covered": v[0], "n_trials": v[1], "coverage_rate": v[0] / v[1],
                "wilson_95ci": list(wilson_score_interval(v[0], v[1])),
            }
            for (c, n), v in grid["percentile"].items()
        },
        "bca": {
            f"{c}|{n}": {
                "covered": v[0], "n_trials": v[1], "coverage_rate": v[0] / v[1],
                "wilson_95ci": list(wilson_score_interval(v[0], v[1])),
            }
            for (c, n), v in grid["bca"].items()
        },
        "wall_clock_seconds": elapsed,
    }
    out_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    print(f"\nWrote raw grid to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
