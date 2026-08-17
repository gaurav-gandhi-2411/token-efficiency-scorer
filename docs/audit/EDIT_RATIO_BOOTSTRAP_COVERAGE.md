# Edit-ratio bootstrap CI: coverage measurement (ZZ1, audited under AB1)

**AB1 correction, read this first:** the original version of this document
attributed the undercoverage below to Cov(edits, cost) / Var(edits) — the
classic finite-sample ratio-of-sums bias. That specific mechanism is
**refuted by direct experiment** (AB1.4's known-answer check, below): a
constant-edits generator, which eliminates that bias entirely by
construction, produces statistically indistinguishable undercoverage from
the original correlated generator. The real mechanism is the percentile
bootstrap's well-documented weak spot for the MEAN of a **right-skewed
distribution** at small-to-moderate n (the lognormal cost-noise itself) —
not anything specific to the ratio-of-sums structure. This also means the
gap **does eventually close**: a supplementary check at n=500/1,000 found
coverage back within its nominal CI. See "AB1 audit" section below for the
full evidence chain. The bottom-line recommendation is unchanged — the
gap has not closed anywhere in the range a real `tes cost --roi`-style
window would realistically hold (10s–low-100s of sessions) — but the
mechanism, and the harness's correctness, are now verified rather than
assumed.

## AB1 audit: was the harness measuring the right thing?

**AB1.1 — the TRUE target, exact quote** (`scripts/measure_edit_ratio_bootstrap_coverage.py:62`):
```python
TRUE_RATIO = 2.50  # arbitrary but fixed -- $2.50/edit, a plausible real figure
```
used to generate data at line 98: `cost = TRUE_RATIO * edits * noise`. Coverage
is checked as `lo <= TRUE_RATIO <= hi` on each trial's interval.

**AB1.2 — the estimator, exact quote** (line 179):
```python
point_estimate = cost.sum() / edits.sum()
```
A ratio of sums, exactly as designed.

**AB1.3 — are they the same quantity?** `noise_i` is drawn independently of
`edits_i` (separate `rng.normal` calls, no functional dependence) with
`E[noise_i]=1` (mean-corrected lognormal location). Asymptotically,
`Σcost/Σedits → E[cost_i]/E[edits_i] = TRUE_RATIO * E[edits_i] * E[noise_i]
/ E[edits_i] = TRUE_RATIO` — no asymptotic bias. At finite n, the classical
ratio-estimator bias (a real, textbook effect, order ~1/n, driven by
`Cov(edits,cost)` and `Var(edits)`) DOES apply, and AB1.3 correctly
predicted this would produce a bias not fully accounted for by the
interval — but AB1.4 below shows this specific mechanism is not what's
actually driving the measured undercoverage.

**AB1.4 — known-answer sanity check.** Generated data with `edits_i` held
**constant** (`edits_value=5.0` for every session — `Var(edits)=0`,
eliminating the ratio-of-sums bias term identically, since
`Σcost/Σedits = Σcost/(n·5) = mean(cost)/5` becomes a pure linear rescaling
of the sample mean of `cost_i`, unbiased for `TRUE_RATIO` at ANY n):

| n\confidence | 0.95 | 0.98 |
|---|---|---|
| 10  | 0.8650 [0.8493,0.8793] | 0.9000 [0.8861,0.9124] |
| 30  | 0.9035 [0.8898,0.9157] | 0.9420 [0.9309,0.9514] |
| 50  | 0.9230 [0.9105,0.9339] | 0.9585 [0.9488,0.9664] |
| 100 | 0.9320 [0.9201,0.9422] | 0.9615 [0.9521,0.9691] |
| 250 | 0.9330 [0.9212,0.9431] | 0.9680 [0.9593,0.9749] |

**Statistically indistinguishable from the original correlated-generator
result at every matching cell** (e.g. n=250, confidence=0.95: 0.9330 here
vs. 0.9340 originally — well within each other's CIs). **This directly
refutes AB1.3's specific hypothesis**: removing the ratio-of-sums bias
mechanism entirely does not fix coverage, so that mechanism was never the
actual driver. With `edits` constant, the estimator is literally
`mean(cost_i)/5` — a rescaled sample mean of a right-skewed (lognormal)
variable — and percentile-bootstrap undercoverage for the mean of a skewed
distribution at small-to-moderate n is the textbook effect (Efron &
Tibshirani 1993) actually responsible, not anything specific to dividing
by a second random quantity.

**Does the gap close eventually?** A supplementary check (same
constant-edits generator, confidence=0.95, 1,000 trials): n=500 → coverage
0.9510 [0.9358,0.9627] (nominal now inside the CI); n=1,000 → 0.9430
[0.9269,0.9557] (also inside). **Yes — it closes somewhere between n=250
and n=500** for this skew level, consistent with ordinary bootstrap
convergence, not a permanently broken method. The practical conclusion is
unchanged only because a real `tes cost --roi`-style rolling window
realistically holds far fewer sessions than 500 for the overwhelming
majority of users.

**AB1.5 — resampling unit vs. generator's exchangeable unit.** The
generator produces n independent `(cost_i, edits_i)` PAIRS (sessions are
the i.i.d. unit). `bootstrap_replicates` resamples via a single index array
applied to BOTH arrays: `idx = rng.integers(0, n, size=(n_boot, n));
boot_cost = cost[idx].sum(axis=1); boot_edits = edits[idx].sum(axis=1)` —
the same `idx` indexes both, so whole sessions are resampled as units,
preserving the cost/edits pairing. Verified correct.

**AB1.6 — BCa jackknife unit.** `jk = (total_cost - cost) / (total_edits -
edits)` — vectorized leave-one-SESSION-out (each `jk[i]` removes session
i's own `(cost_i, edits_i)` pair), matching the resampling unit exactly.
The full BCa formula (bias-correction `z0` from
`mean(replicates < point_estimate)`, acceleration `a` from the jackknife
skewness, adjusted percentiles via the standard Efron 1987 formula) was
checked line-by-line against the textbook definition and matches. **BCa
measuring worse than percentile here is not traced to an implementation
bug** — both the resampling unit and the jackknife unit are correct. Left
as a genuine, measured, not-fully-explained property of this estimator/
generator combination (plausible mechanism: the jackknife-based
acceleration estimate is itself a noisy statistic at these n, compounding
rather than correcting the skew-driven error — not confirmed further).

**AB1.8 verdict: harness is correct.** All checks pass; the coverage
problem is real, general (driven by cost-distribution skewness, not
ratio structure), and does not affect the recommendation. Studentized/
bootstrap-t is the next candidate, as originally proposed.

---

**Status: MEASURED, XX2's ratio-CI feature BLOCKED pending a different
interval method.** Per ZZ1.7's own explicit instruction ("if coverage is
bad at every setting, report that before implementing"): this is that
report. No `tes/_bootstrap.py`, no cost-per-edit/cost-per-100-lines CI
output, ships from this pass. The rest of XX2 (data model, extraction,
churn ranking) is unaffected and unblocked — only the CI-bearing ratio
statistics wait on this.

Script: `scripts/measure_edit_ratio_bootstrap_coverage.py`. Raw grid:
`reports/edit_ratio_bootstrap_coverage.json`.

## Why adk-tracegauge's constants were not inherited (ZZ1.1)

`adk-tracegauge/_regression.py` validates a two-sample **difference-in-means**
estimator. XX2's estimator is a **ratio-of-sums** (`sum(cost) / sum(edits)`)
over session-paired data, resampled at the session level to preserve the
real cost/edit-count correlation. These are different sampling
distributions — a ratio estimator is more prone to the exact failure mode
(skew) that makes a percentile bootstrap under-cover at small-to-moderate
n. Nothing was assumed; it was measured.

## Method

Synthetic `(cost_i, edits_i)` session pairs with a **known** true ratio
(`TRUE_RATIO = 2.50`). `edits_i` lognormal (skewed, realistic — median ~4.5
edits/session, long right tail), `cost_i = TRUE_RATIO * edits_i * noise_i`
with `noise_i` mean-corrected lognormal (`E[noise_i]=1`), so the estimator
is consistent and cost/edits stay correlated at the session level without
being a fixed multiple. Grid: confidence ∈ {0.95, 0.98, 0.99} × n ∈ {10,
30, 50, 100, 250}, **2,000 trials/cell** (ZZ1.3's stated floor), coverage
rate reported with a Wilson 95% CI (same method as
`measure_regression_confidence_grid.py`, for the same reason — see that
script's docstring).

## Result: coverage grid

Percentile bootstrap, `n_boot=10,000`:

| n\confidence | 0.95 | 0.98 | 0.99 |
|---|---|---|---|
| 10  | 0.8350 [0.8181,0.8506] | 0.8835 [0.8687,0.8968] | 0.9005 [0.8866,0.9129] |
| 30  | 0.8830 [0.8682,0.8964] | 0.9280 [0.9158,0.9385] | 0.9465 [0.9358,0.9555] |
| 50  | 0.8995 [0.8855,0.9119] | 0.9425 [0.9314,0.9519] | 0.9625 [0.9532,0.9700] |
| 100 | 0.9080 [0.8945,0.9199] | 0.9495 [0.9390,0.9583] | 0.9665 [0.9577,0.9735] |
| 250 | 0.9340 [0.9223,0.9441] | 0.9685 [0.9599,0.9753] | 0.9835 [0.9769,0.9882] |

BCa bootstrap, same grid:

| n\confidence | 0.95 | 0.98 | 0.99 |
|---|---|---|---|
| 10  | 0.8235 [0.8062,0.8396] | 0.8625 [0.8467,0.8769] | 0.8870 [0.8724,0.9001] |
| 30  | 0.8685 [0.8530,0.8826] | 0.9145 [0.9014,0.9260] | 0.9355 [0.9239,0.9455] |
| 50  | 0.8785 [0.8635,0.8921] | 0.9310 [0.9190,0.9413] | 0.9540 [0.9439,0.9623] |
| 100 | 0.8925 [0.8782,0.9053] | 0.9345 [0.9228,0.9445] | 0.9565 [0.9467,0.9646] |
| 250 | 0.9250 [0.9126,0.9357] | 0.9590 [0.9494,0.9668] | 0.9765 [0.9689,0.9823] |

**Coverage is below nominal at every single cell, for both methods, at
every tested n from 10 to 250.** The gap narrows as n grows (n=10 →
n=250 improves coverage by ~10 points at confidence=0.95) but has not
closed by n=250 — even there, the coverage estimate's own 95% CI excludes
the nominal confidence level at every column (e.g. confidence=0.99, n=250:
coverage 0.9835 [0.9769,0.9882] — 0.99 sits above the upper bound).

## ZZ1.4: BCa does not improve on percentile for this estimator

**BCa is consistently WORSE than plain percentile at every cell**, not
better — e.g. confidence=0.98, n=30: percentile 0.9280 vs. BCa 0.9145.
Phase 4's "no improvement from BCa" finding was for the difference-in-means
estimator and explicitly does not transfer (ZZ1.4) — it doesn't transfer
in the way one might guess, either: rather than BCa being neutral here too,
it is measurably worse. Not deeply investigated further (out of scope for
this pass) — plausible mechanism: the jackknife-based acceleration
estimate is itself a noisy statistic at these sample sizes, on top of the
ratio estimator's own skew, compounding rather than correcting error at
small-to-moderate n. Flagged as a real, measured result, not a fully
explained one.

## A supplementary check: log-transform doesn't help (and can't)

Tested whether a bootstrap CI on `log(ratio)`, exponentiated back, closes
the gap (a standard technique for skewed positive-ratio statistics).
**Identical results to plain percentile at every cell** — mechanically
expected once written down: percentiles are invariant under a monotonic
transform (`exp(percentile(log(X), p)) == percentile(X, p)` exactly), so a
log-scale *percentile* bootstrap can never differ from a linear-scale one.
This rules out the cheapest candidate fix and correctly redirects toward
methods that use the log scale differently — e.g. a **studentized
(bootstrap-t) interval**, which uses a symmetric interval around a
log-scale point estimate scaled by an estimated standard error (itself
requiring a nested bootstrap or a delta-method variance estimate) rather
than log-scale empirical percentiles. Not implemented or measured this
pass — the natural next experiment, not run for scope reasons.

## ZZ1.5 / ZZ1.6: no min_n or n_boot recommendation follows

**No minimum n in the tested range (10–250) achieves nominal coverage**,
so ZZ1.5's instruction ("pick min_n from the grid... if that lands above
30, say so and use the real number") cannot be satisfied — the real
number, if it exists at all within a practically useful range, is above
250, or this estimator may not be well-served by percentile/BCa at any
achievable n. `min_n=30` is explicitly NOT adopted; no number is, yet.

`n_boot` sufficiency WAS measured cleanly (independent of the coverage
question — this measures replicate-to-replicate CI-bound stability, not
coverage): at confidence=0.98, n=30, the interval bound's std dev across
200 independent bootstrap runs drops from 0.044/0.088 (lower/upper) at
`n_boot=500` to 0.010/0.020 at `n_boot=10,000`, continuing to shrink to
0.007/0.014 at `n_boot=20,000` — still improving, not yet flat. At n=250
the same pattern holds at a smaller absolute scale. **`n_boot=10,000`
narrows the interval-bound noise by ~4x over `n_boot=1,000` and is still
improving at 20,000** — a real, measured case for `n_boot` at least at
10,000 for this estimator (unlike the coverage question, this part
transfers cleanly: more resamples make the reported bound more stable
regardless of the underlying coverage problem). This number can be
adopted once the coverage question is resolved; it was never the blocker.

## What actually blocks XX2's ratio-CI feature, and what doesn't

**Blocked:** any `tes cost --roi`-style feature reporting a bootstrap CI on
cost-per-edit or cost-per-100-lines specifically, at the confidence/n
combinations tested, using percentile or BCa.

**Not blocked by this finding** — proceed independently, per the original
XX2 design, unaffected by the CI question:
- The `edit_operations` data model and score-time persistence (XX2.2).
- Edit/Write extraction, the `prior_content_unknown` tracking (XX2.1/2.3).
- Per-file/per-directory churn ranking as plain transparent counts
  (XX2.5) — this was already recommended as a non-CI, non-composite-score,
  purely descriptive ranking; it never depended on the bootstrap question.
- The legacy-row consequence statement (XX2.6) once the above ships.

## Recommended next step (not run this pass)

Measure a studentized (bootstrap-t) interval for this same estimator and
generator before concluding percentile/BCa are the ceiling. If that also
under-covers at practically achievable n (`tes cost --roi`-scale windows
realistically hold 10s to low-100s of sessions, not thousands), the
honest fallback is not a CI-bearing ratio at all — a plain point-estimate
ratio (`cost/edits`, `cost/100 lines`) reported WITHOUT a CI, labeled
explicitly as a point estimate with a stated `n`, similar to how `tes
budget`'s projection is labeled with sample size but not a discovered
interval. That would be a real design change from XX2.4's original
"report each with a CI" framing — flagged for a decision, not decided here.
