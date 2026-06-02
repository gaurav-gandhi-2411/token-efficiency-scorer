# Report 06 — Judge Calibration: Instrument Validation Without Human Ground Truth

**Author:** Gaurav Gandhi
**Date:** 2026-06-02
**Status:** FINAL — qwen3:30b-a3b validation complete; human gold calibration deferred (see sections 0 and 8)

---

## 0. What This Report Covers — and What It Does Not

This report establishes that the qwen3:30b-a3b judge is a **coherent, behaviorally-grounded
trajectory-quality instrument** that agrees with a strong reference LLM at rho = 0.79
(cluster-excluded) and penalizes duplicate-work signals as designed (rho = -0.40 vs H2).

This report does **not** establish that the judge is accurate. Accuracy requires human ground
truth — ratings from a person who assessed each session's efficiency. That ground truth does not
exist yet. Every correlation in this report is either LLM-vs-LLM (two models agreeing on a
rubric) or judge-vs-deterministic-proxy (two different but overlapping constructs). The rho = 0.75
target from report 05 was defined against the human gold set. We have not met that target in its
original meaning. We have met a weaker, meaningful precondition: the instrument is internally
consistent, behaviorally grounded, and non-random.

That distinction is load-bearing. The founding lesson of Phase A.1 — LLM labels are not ground
truth — applies here as much as it did to the heuristics. Two models can share blind spots and
agree while both being wrong (the kappa = 0.31 parable). Reporting rho = 0.79 as "TARGET MET"
would wear the right number on the wrong claim.

**Honest summary:** the judge is ready to be tested against human ground truth. It has not been
tested yet.

---

## 1. Status and Scope

This report documents judge calibration results for token-efficiency-scorer, iteration B1.
Human gold collection has been deferred from this phase. What this report covers:

- **Target A:** Deterministic objective efficiency proxy computed from Layer 1 features alone
  (no LLM calls). Serves as the independent non-LLM calibration signal.
- **Target B:** LLM provisional rating via claude-sonnet-4-6, using the same digest text and
  rubric wording that a human rater would see. Secondary consistency signal.
- **Results:** Spearman rho across four reporting cuts with bootstrapped 95% CIs. All cuts
  reported; cluster-excluded cut (b) is the honest headline.

Human gold is explicitly deferred, not accidentally omitted. See section 2 for the decision record.

---

## 2. Decision: Deferred Human Gold

The original B1 plan called for a 40-session human gold set using `scripts/rating_interface.py`.
The decision was made to defer human collection because trajectory efficiency is hard to rate
reliably by hand: the Phase A.1 IAA results (kappa 0.15-0.43 across H1/H3/H4) demonstrate that
even concept-aware annotators produce low agreement on efficiency-adjacent signals when tasked to
rate per-turn behavior. Extending this to full-session efficiency ratings without clear rubric
anchoring risks producing gold that is less reliable than the objective proxy.

The accepted limitation is explicit: this phase has no human ground truth. The objective proxy
(Target A) is a principled substitute anchored on task outcomes and corpus-relative token spend.
Human gold collection is retained as the production calibration target (see section 8).

---

## 3. Why LLM Rating Alone Cannot Be Ground Truth

The Phase A.1 IAA study (report 04) ran GPT-OSS-120b and claude-sonnet-4-6 against the same
efficiency-adjacent rubrics on 25 overlapping sessions. Cohen's kappa across H1/H3/H4 was 0.15,
0.43, and 0.19 — all below the 0.60 threshold for acceptable agreement. On H4, Sonnet
systematically over-fired on 72 of 310 turns (23%) relative to GPT-OSS, despite identical rubric
text. This is not random noise: it is structured model-family rubric drift that does not average
out across more samples.

A single LLM rating (even at scale) cannot serve as ground truth because there is no external
anchor to distinguish genuine efficiency signal from the model's idiosyncratic interpretation of
"efficient." The dual-target design addresses this by requiring the LLM judge to correlate with
an objective, task-outcome-based signal alongside the LLM consistency check.

---

## 4. Dual-Target Design

### Target A — Objective Efficiency Proxy (formula)

Computed by `scripts/objective_proxy.py` from Layer 1 features only. No API calls.

```
objective_efficiency_proxy = 0.25 * resolved_score
                           + 0.50 * (1 - percentile_rank(p25_token_ratio))
                           + 0.25 * (1 - percentile_rank(turn_ratio))
```

Sessions with lower token ratio and fewer turns (more efficient) receive higher proxy scores.
The proxy is an imperfect ground truth: a session that resolved by thrashing receives the
resolution bonus, which is why the LLM judge is needed to discriminate trajectory quality.

### Target B — LLM Provisional Rating

Computed by `scripts/llm_rater.py` using claude-sonnet-4-6 via Anthropic Batch API. The model
sees the same digest text and rubric wording that a human rater would see. Output field
`source: "llm_provisional"` distinguishes these from human gold.

### Why Both Are Needed

Target A encodes objective outcomes but cannot capture trajectory quality. Target B captures
trajectory patterns but cannot be trusted without an external anchor. Strong LLM-vs-LLM
agreement combined with near-zero proxy correlation would mean the judge tracks "what
rubric-reading LLMs track" without demonstrated correspondence to measurable efficiency.
Both signals are required.

---

## 5. Calibration Protocol

Four cuts are reported for each comparison, with 95% bootstrap CI (n=2000, seed=42):

| Cut | Definition |
|---|---|
| (a) Full scored set | All 67 sessions with valid judge verdicts |
| (b) Empty-loop excluded | 14 near-identical swegym failure sessions removed — **honest headline** |
| (c) H2=0 subset | Sessions with zero duplicate messages — non-circular check |
| (d) Per scaffold | openhands_nebius / openhands_swegym / swe_agent independently |

Cut (b) is the headline. A validation that rests on easy floor-detection (see section 6.1) is not
the validation we need.

---

## 6. Corpus Composition Caveats

### 6.1 Empty-Loop Cluster (21% of corpus, 54% of MUCH_WORSE)

**14 of 67 sessions (20.9%) are near-identical swegym empty-loop failures.**
Criteria: scaffold = openhands_swegym, turns <= 15, verdict = MUCH_WORSE.

| Metric | Count | Share |
|---|---|---|
| Of total corpus | 14 / 67 | 20.9% |
| Of WORSE + MUCH_WORSE | 14 / 40 | 35.0% |
| Of MUCH_WORSE specifically | 14 / 26 | 53.8% |

These 14 sessions share a specific failure mode: the agent emits empty or single-token turns
(T2, T4, T6, T8) in a loop without integrating environment results. The judge correctly rates
them MUCH_WORSE with concrete behavioral citations — they are not mislabeled. But because they
cluster at the floor and are behaviorally near-identical, any instrument that detects "empty
turns in a loop" gets credit for 14 sessions without having to discriminate anything subtle.
Including them in a headline correlation overstates how hard the judge is working.

### 6.2 Sample Scope

The corpus is 100% SWE-bench-style, 100% Python, 100% offline scaffolds. Two sessions were not
scored (e1b043ff429ed5a2, 9dd32933ac04fd31): both are in layer1_outputs.jsonl; Ollama returned
None (likely structured-output timeout). 67 sessions are sufficient for this report.

### 6.3 Scaffold Confound

openhands scaffolds record per-turn output tokens; swe_agent sessions have zero output tokens
(tokens_output = 0). This affects p25_token_ratio and the digest view shown to the judge.
Per-scaffold rho values are reported to detect any scaffold-specific breakdown.

### 6.4 Resolved Collinearity

Point-biserial r = 0.50, Spearman rho = 0.58 between judge_score and resolved status. Moderate.
Off-diagonal cells confirm independence: 7 unresolved sessions score MUCH_BETTER; 5 resolved
sessions score WORSE or MUCH_WORSE. Not blocking; documented for weight-tuning context.

---

## 7. Calibration Results

### 7.1 Experimental Setup

- **Judge:** qwen3:30b-a3b, Q4 quant, Ollama, v3 prompt
- **Config:** temperature=0, seed=42, num_predict=4096, num_ctx=32768
- **Prompt v3:** trajectory purposefulness only, C1-C4 criteria in fixed order, `/no_think` prefix
- **Inference:** GCP g2-standard-8 SPOT (NVIDIA L4, asia-east1-a), 21,146/23,034 MiB VRAM
  confirmed GPU-resident. ~46 min wall-clock for 67 sessions, ~41 s/session warm.

### 7.2 Verdict Distribution (67 sessions, parse failures = 0)

| Verdict | Count | % |
|---|---|---|
| MUCH_BETTER | 17 | 25.4% |
| BETTER | 7 | 10.4% |
| SIMILAR | 3 | 4.5% |
| WORSE | 14 | 20.9% |
| MUCH_WORSE | 26 | 38.8% |
| mean score | — | 0.407 |

Corpus skews negative (59.7% WORSE or MUCH_WORSE), consistent with SWE-bench scaffolds.

### 7.3 vs Objective Proxy (deterministic formula, Target A)

| Cut | N | rho | 95% CI |
|---|---|---|---|
| (a) Full 67 | 67 | -0.050 | [-0.282, +0.195] |
| **(b) Empty-loop excluded** | **53** | **+0.241** | **[+0.005, +0.499]** |
| (c) H2=0 subset | 17 | -0.084 | [-0.415, +0.368] |
| (d) openhands_nebius | 17 | +0.566 | [+0.411, +0.726] |
| (d) openhands_swegym | 25 | -0.088 | [-0.451, +0.322] |
| (d) swe_agent | 25 | +0.601 | [+0.369, +0.792] |

**Full-corpus rho is -0.050 and not significant.** The honest cluster-excluded headline is
+0.241, with a CI that just clears zero. This is weak, not strong.

Two explanations are both partly true. The *design explanation*: the proxy measures outcomes and
token spend; the judge measures trajectory purposefulness. An efficient trajectory can produce a
poor token ratio (long but coherent); a token-efficient session can be a quick failure (lean and
bad). Some disagreement is expected by design. The *concern*: strong LLM-vs-LLM agreement
combined with near-zero non-LLM signal means the judge reliably tracks what rubric-reading LLMs
track, which has not been independently shown to equal real efficiency.

The within-scaffold numbers are the honest encouraging signal: nebius +0.566 and swe_agent +0.601
show genuine proxy-judge alignment within a homogeneous trajectory style. swegym -0.088 does not.
Full-corpus rho is contaminated by the construct mismatch and the swegym cluster and does not
cleanly answer whether the judge is right.

### 7.4 vs LLM Provisional Rating (Sonnet, 1-5 scale, Target B)

| Cut | N | rho | 95% CI |
|---|---|---|---|
| (a) Full 67 | 67 | +0.776 | [+0.634, +0.882] |
| **(b) Empty-loop excluded** | **53** | **+0.792** | **[+0.662, +0.884]** |
| (c) H2=0 subset | 17 | +0.378 | [-0.214, +0.881] |
| (d) openhands_nebius | 17 | +0.730 | [+0.625, +0.897] |
| (d) openhands_swegym | 25 | +0.630 | [+0.358, +0.867] |
| (d) swe_agent | 25 | +0.759 | [+0.608, +0.881] |

The judge agrees with the Sonnet reference rater at rho = 0.792 (cluster-excluded), CI
[0.662, 0.884]. The cluster contributes negligibly (+0.016 inflation): removing 14 trivially-easy
sessions barely moves the number, which means discrimination on the hard middle of the corpus is
real. Per-scaffold rho is positive and meaningful on all three scaffold types (0.630 / 0.730 /
0.759), confirming the judge works within each operating mode, not just exploiting the bimodal
between-group split.

**What this proves and what it does not.** This is a strong instrument-coherence result. It
proves the judge is not random and not scaffold-biased in its rubric application. It does not
prove the judge is accurate. Both qwen3:30b and Sonnet share training distribution, rubric-following
norms, and potentially the same blind spots. The Phase A.1 kappa = 0.31 lesson applied to a
per-turn rubric; the present result is a session-level task with a more structured rubric and
a stronger model. The epistemological structure is the same: LLM-vs-LLM agreement is necessary
but not sufficient for accuracy.

The rho = 0.75 target from report 05 required agreement with human ground truth. Reporting 0.792
as meeting that criterion would be a category error. The criterion has not been tested. The
instrument is ready to be tested against it.

### 7.5 H2=0 Subset (non-circular check)

rho = +0.378 vs LLM provisional, CI [-0.214, +0.881], N=17. **INCONCLUSIVE.**

The point estimate is positive-directional and consistent with the full-corpus result. The CI
spans from near-zero to +0.88; we cannot make a claim. At N=17, the bootstrapped interval
covers almost the full possible range. This cut was designed to test whether the judge
discriminates when its strongest correlated feature (H2) is zero — i.e., whether it does more
than detect obvious duplicate-message sessions. The point estimate says yes; the width says
we cannot prove it from this data. A larger H2=0 subsample in the human gold set would close
this question.

### 7.6 Sanity Check — judge_score vs H2

rho = -0.400 (n=67). **PASS.** The judge penalizes high-duplicate-message sessions as designed.
Sign is correct; magnitude is moderate, consistent with H2 being one of four criteria rather
than the dominant signal.

### 7.7 Summary of Claims

**The judge IS:**
- Coherent. Verdicts are grounded in specific behavioral observations: turn numbers cited,
  failure modes named (empty turns, repeated reads, single-token loops, wrong-target fixation).
  Verified on six sessions in both directions (investigation session 2026-06-02).
- Non-circular on H2. rho = -0.40 against H2 duplicate count is the expected directional signal;
  the judge was not given H2 values, it inferred the same quality signal from the trajectory text.
- Consistent with a strong reference LLM. rho = 0.792 (cluster-excluded) vs Sonnet provisional,
  robust across all three scaffold types, negligible cluster inflation.
- Scaffold-aware without being scaffold-biased. Positive per-scaffold rho on all three scaffold
  types; the judge is not exploiting the bimodal split between nebius (near-ceiling) and swegym
  (near-floor).

**The judge is NOT YET:**
- Validated against human ground truth. The rho = 0.75 target from report 05 required a
  40-session human gold set. That set has not been collected. No correlation in this report
  establishes that the judge's ratings match what a human expert would assign.
- Validated on the efficiency construct end-to-end. The proxy correlation (+0.241 cluster-
  excluded, CI barely off zero) leaves open whether the judge tracks trajectory quality in
  a way that correlates with measurable efficiency outcomes.
- Ready to be sold as "calibrated to human experts." That claim requires the deferred
  human gold-set work.

---

## 8. What Changes When Human Gold Arrives

When `data/gold/human_ratings.jsonl` is populated (40 sessions rated via
`scripts/rating_interface.py`), the calibration pipeline requires no code changes:

```bash
python scripts/calibration.py --human-ratings data/gold/human_ratings.jsonl
```

`calibration.py` already accepts `--human-ratings` and will compute:
- judge_score vs human_gold (new headline)
- objective_proxy vs human_gold
- llm_provisional vs human_gold

The new headline becomes **judge vs human_gold**. The production target is rho >= 0.75 vs
human gold over the 40-session sample. The kill criterion (rho < 0.55 after 3 prompt iterations)
is tested against this pair, not against the LLM provisional. The four cuts defined in section 5
should be rerun with the human gold comparator; the empty-loop exclusion criterion stays the same.

Additionally: the human gold set should over-sample sessions outside the swegym empty-loop
cluster. With 14 cluster sessions representing 54% of MUCH_WORSE, a gold set that
proportionally includes them produces a calibration test that over-weights easy floor-detection.
Capping cluster representation at <= 10% of the gold set would produce a harder, more
informative test.

---

## 9. Infrastructure Notes

- **VM:** tes-judge-scoring-tmp TERMINATED, billing stopped. Boot disk preserved; do not
  delete until human gold calibration is complete (saves ~2h setup on re-score if needed).
- **Preemption fix:** `gpu_score_runner.sh` sentinel guard committed (0874b38); first run
  truncates and writes `data/.judge_scores_cleared`; restarts skip truncation and resume via
  skip-if-scored. Resolves the mid-run restart trap identified in pre-flight.
- **Startup fix:** `vm_startup.sh` exports `HOME=/root` before Ollama pull; `chown` after
  clone ensures SSH user write access (0874b38).
- **Estimated GCP spend this run:** ~$0.15 VM compute. Cumulative project API spend ~$2.59
  (judge is local, $0 inference per session).

---

## 10. Files Produced This Phase

| File | Description |
|---|---|
| `scripts/objective_proxy.py` | Target A: deterministic proxy; writes `data/objective_proxy.jsonl` |
| `scripts/llm_rater.py` | Target B: Sonnet provisional rater; writes `data/llm_provisional_ratings.jsonl` |
| `scripts/layer2_judge.py` | qwen3:30b-a3b Ollama judge; writes `data/judge_scores.jsonl` |
| `scripts/calibration.py` | Spearman rho with 95% bootstrap CI; writes `data/calibration/calibration_{datestamp}.json` |
| `scripts/calibration_multicutnow.py` | Four-cut multi-comparator calibration; produces this report's numbers |
| `scripts/investigate_findings.py` | Post-scoring investigation: scaffold split, p25 inversion, collinearity |
| `scripts/gpu_score_runner.sh` | Preemption-safe scoring runner with sentinel guard |
| `scripts/vm_startup.sh` | GCP startup script with HOME fix and chown |
| `data/judge_scores.jsonl` | 67-session GPU calibration scores (qwen3:30b-a3b, v3, L4, asia-east1-a) |
| `research/06-calibration.md` | This document |
