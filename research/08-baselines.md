# Report 08 — Quality-Gated CC-Native Token Baselines

**Author:** Gaurav Gandhi
**Date:** 2026-06-03
**Status:** FINAL — B2 iteration complete; baselines locked; see section 9 for explicit limitations

---

## 0. What This Report Covers

This report documents the B2 iteration: building a defensible token-efficiency baseline for Claude Code sessions from a quality-gated reference corpus, so the efficiency ratio `customer_tokens / baseline_tokens` reflects real work rather than corpus noise.

The core challenge B2 addressed: a baseline only means "what an efficient run should cost" if the sessions in it are known-good. We have no human labels, but we do have a validated trajectory-quality judge. The approach: pool real CC sessions, judge-score them, keep only MUCH_BETTER verdicts, classify by task type, and compute per-type token baselines from the certified-good sessions.

**What this report does not cover:** human accuracy calibration (deferred), per-customer adaptive baselines (launch-2), or Aider adapter (deferred).

---

## 1. Iteration Status

B2 is complete. All five success criteria met:
- Public CC corpora pulled and adapted; 181 sessions in pool
- Pool judge-scored on GPU; 143 sessions with verdicts
- Quality gate applied; 75 certified-good local sessions in baseline
- Task taxonomy derived + classifier validated
- Per-type baselines with scope gate computed; sparse/unseen types → UNAVAILABLE

---

## 2. Pool: Ingestion and Scoring

### 2.1 Sources

| Source | Sessions in pool | Judge-scored | MUCH_BETTER |
|---|---|---|---|
| Local (gaurav-gandhi-2411) | ~145 | 109 | 75 |
| armand0e/kimi-k2.6-claude-code-traces | 36 | 34 | 2 |
| cfahlgren1/agent-sessions-list | 0 (format incompatible) | — | — |
| **Total** | **181** | **143** | **77** |

### 2.2 Judge configuration (locked from B1)

- Model: `qwen3:30b-a3b` via Ollama (local, $0/session)
- Prompt: v3 — trajectory purposefulness only (C1-C4 criteria, no token-efficiency framing in rubric)
- Parameters: temp=0, seed=42, num_predict=6144, JSON schema
- Hardware: GCP g2-standard-8 SPOT, asia-east1-a (~$0.37/validation run; ~$3.16 total B2 GCP spend)
- Verdict scale: MUCH_WORSE (0.0), WORSE (0.25), SIMILAR (0.5), BETTER (0.75), MUCH_BETTER (1.0)

### 2.3 Pool verdict distribution

| Verdict | n | % |
|---|---|---|
| MUCH_BETTER | 77 | 53.8% |
| BETTER | 45 | 31.5% |
| SIMILAR | 3 | 2.1% |
| WORSE | 15 | 10.5% |
| MUCH_WORSE | 3 | 2.1% |

---

## 3. Quality Gate Decision

**Strict gate: MUCH_BETTER only.** Rationale: the pool is 85% MUCH_BETTER + BETTER; the loose gate barely filters and would build a baseline from "good enough" rather than "exemplary" sessions. The strict gate enforces a real quality floor — the baseline represents what an expert-executed session of that type costs, not a typical session.

MUCH_BETTER + BETTER was evaluated as an alternative; it raised informative coverage from 26.5% to 44.1% but diluted the quality floor. This was rejected as "manufacturing coverage by relaxing the claim." See section 8.2.

---

## 4. Armand0e Exclusion from Baseline

The 2 armand0e MUCH_BETTER sessions (`3e36e08b`, `79803515`) are excluded from the baseline. Reason: Kimi-k2.6 does not use Claude prompt caching. Without caching, `input_tokens` re-sends the full context every turn, accumulating linearly with session length. The corrected token measure (section 5) removes Claude cache-read re-accumulation, but has no equivalent correction for non-cached sessions. The two populations measure fundamentally different quantities and cannot share a baseline.

Local sessions: cache_hit_rate = 0.80–0.99 (median 0.967). Armand0e sessions: cache_hit_rate ≈ 0.0–0.12.

**Baseline population: 75 local Claude Code sessions, MUCH_BETTER only.**

---

## 5. Token Measure: Cache-Corrected real_tokens

### 5.1 The cache accumulation artifact

The `total_tokens` field in the Claude Code adapter initially computed:
```
total_tokens = sum_input + sum_cache_creation + sum_cache_read + sum_output
```

At 90-97% cache hit rates, `sum_cache_read` is 87–94% of total_tokens. In a session with N turns, each turn re-counts the full prior context in `cache_read_input_tokens`. This accumulates linearly with turn count and is an accounting illusion, not informational content.

On ml-eval baseline sessions, the artifact inflated `total_tokens` by ~94% on average (range 87–98% reduction after correction). The inflated measure exhibited false bimodality in the ml-eval distribution (CV=0.88) that dissolved completely after correction (CV=0.51).

### 5.2 Corrected measure

```
real_tokens = sum over AI turns of (token_count_input - cache_read) + token_count_output
           = sum_input + sum_cache_creation + sum_output
```

This is "tokens actually processed once": uncached new context, context newly written to cache, and generated output. Cache re-reads excluded.

**Economic interpretation:** `cache_creation` and `input_tokens` are billed at standard rate; `output_tokens` is billed at output rate; `cache_read` is billed at the reduced cache-read rate (~10%). The `real_tokens` measure closely tracks actual computational cost, not accounting volume.

### 5.3 Corrected baseline token ranges

Post-correction, all medians are in the 500-720K range — consistent with real coding-session complexity:

| Type | n | p25 | median | p75 | CV |
|---|---|---|---|---|---|
| ml-eval | 12 | 458,439 | 646,026 | 1,034,218 | 0.51 |
| debug-fix | 19 | 353,407 | 524,989 | 654,348 | 0.56 |
| infra-deploy | 20 | 386,220 | 698,512 | 1,003,593 | 0.83 |
| research-recon | 12 | 362,790 | 718,627 | 1,339,625 | 0.63 |
| feature-build | 12 | 424,780 | 711,859 | 803,514 | 0.43 |

---

## 6. Task Taxonomy and Classifier

### 6.1 Five task types

Derived from real task descriptions in the pool, not from SWE-bench categories:

| Type | Definition |
|---|---|
| ml-eval | ML experiment, training run, eval harness, model analysis |
| debug-fix | Bug investigation, error diagnosis, fix, test repair |
| infra-deploy | Infrastructure, deployment, CI/CD, cloud provisioning, env setup |
| research-recon | Research, audit, analysis, codebase survey, report writing |
| feature-build | New feature/component/capability built or extended (default/fallback) |

### 6.2 Classifier design

Rule-based keyword matching on `task_description` (or first non-boilerplate turn for `<local-command-caveat>` sessions). Priority order: ml-eval > debug-fix > infra-deploy > research-recon > feature-build.

**Implementation notes:**
- `<local-command-caveat>` sessions (10 found): turn-1 is boilerplate; classifier reads the first non-boilerplate user turn for task signal.
- Keywords deliberately conservative: removed overly generic terms (`"score"`, `"model"`, `"inference"`, `"checkpoint"`) from ml-eval after audit showed they caused ~29 false positives in a 77-session strict-gate set.
- One documented session-level override: `d57f0f0e-56aa-4d3a-9637-98719c8dfe47` → `research-recon` (STATUS CHECK session whose task description fired `"eval"` from "v2 eval artifact").
- Classifier is deterministic (no LLM); selftest consistency PASS.

### 6.3 Strict-gate distribution (75 local baseline sessions)

| Type | n |
|---|---|
| infra-deploy | 20 |
| debug-fix | 19 |
| ml-eval | 12 |
| research-recon | 12 |
| feature-build | 12 |

---

## 7. Scope Gate

### 7.1 Motivation

Turn count and real_tokens are strongly correlated within each type (Spearman r = 0.55–0.92 across types). The baseline sessions skew toward complex orchestrator workflows (median 187–308 turns depending on type). Sessions shorter than any baseline session have no comparable reference — comparing their token cost to the p25 floor would be structurally invalid.

A session landing "below_p25" should mean "lean relative to comparable reference sessions," not "structurally incomparable." These are different conditions and require different outputs.

### 7.2 Gate definition

**p10 of baseline turn counts per type** (10th percentile of the reference corpus, not the raw minimum). The raw minimum is set by a single outlier session; p10 anchors the floor to the reliable reference mass.

| Type | p10_turns (scope gate floor) |
|---|---|
| ml-eval | 127 |
| debug-fix | 59 |
| infra-deploy | 63 |
| research-recon | 44 |
| feature-build | 166 |

Sessions with `turn_count < p10_turns` for their type receive `band_verdict = "unavailable"` with interpretation: "Session scope too small for a token-economy reference; trajectory verdict only."

### 7.3 Gate effect on held-out (section 8.3)

Under p10 gate, 17/34 local held-out sessions are in-scope (50%) vs 23/34 under the raw-minimum gate (68%). The p10 gate correctly reclassifies the infra-deploy 37–41 turn WORSE sessions as UNAVAILABLE — they were scraping in at the min-gate floor and should not receive token verdicts.

---

## 8. Validation

### 8.1 Circularity check

Spearman correlation between `real_tokens` and `judge_score` across all 143 scored sessions:

**r = −0.0801, p = 0.3418, n = 143**

Not significant. The slight negative direction (more tokens → marginally lower judge score) is consistent with expectation but far below any actionable threshold. Baseline tokens and judge score are measuring orthogonal axes.

### 8.2 Held-out validation design

34 local held-out sessions (verdict ≠ MUCH_BETTER, not armand0e).

**Critical note on armand0e contamination:** A naive held-out using all 66 non-baseline scored sessions would include 32 armand0e sessions (48%), whose no-caching real_tokens accumulate differently. Under that contaminated analysis, 47% of held-out sessions appeared above_p75 and feature-build showed a 79% above_p75 rate. Both are false positives from the token accounting incompatibility. All held-out analysis uses local sessions only.

### 8.3 Held-out results under p10 scope gate

**Coverage:**

| Status | n | % |
|---|---|---|
| UNAVAILABLE (out-of-scope) | 17 | 50.0% |
| In-scope (token verdict fires) | 17 | 50.0% |

**Of 17 in-scope:**

| Band verdict | n | % of in-scope |
|---|---|---|
| above_p75 | 2 | 11.8% |
| within_band | 7 | 41.2% |
| below_p25 | 8 | 47.1% |
| **Informative (above/within)** | **9** | **52.9%** |

**Overall:** token axis gives a clear strength signal for 9/34 = 26.5% of held-out sessions; 17/34 = 50% are UNAVAILABLE (scope too small); 8/34 = 23.5% are in-scope lean.

No WORSE session scrapes in at the p10 floor. In-scope WORSE sessions (78bd2719 at 470 turns, a1e1e20e at 357 turns, 6852df92 at 312 turns) are all well above the gate.

### 8.4 Two-axis orthogonality

The efficiency number has two independent axes — token economy and trajectory quality. Validation confirmed both patterns:

**Convergent (both axes agree — wasteful):**
- `78bd2719` (debug-fix, WORSE, above_p75): 1,854K tokens vs p75 654K. Judge: "Repeatedly attempted file reads (T4-T8, T12-T13, T19) and Windows/WSL2 command failures without adjusting approach, creating context bloat and trajectory drift." Real token waste from repeated failures — confirmed by both axes.

**Divergent — trajectory waste without token waste:**
- `6852df92` (debug-fix, MUCH_WORSE, within_band): 503K tokens (normal), but judge: "Agent deviated from read-only status sync task to implement and merge T5, which was not part of the instructions." Scope violation costs no extra tokens; only the judge catches it.
- `a1e1e20e` (research-recon, WORSE, within_band): 1,280K tokens (normal), but judge: redundant reads + drift into merge conflicts after task completion.

**Divergent — good trajectory, high token count (bigger task):**
- `b9c6cbd4` (debug-fix, BETTER, above_p75): 890K tokens vs p75 654K, but judge: "Systematic progress with minor redundant reads." Larger task done well — above band reflects scope, not waste.

**Below-p25 in-scope distribution:**
8 in-scope below_p25 sessions: **7 BETTER, 1 WORSE**. Sessions of comparable scope that spent fewer tokens were almost entirely rated as good trajectories. "Below_p25 for in-scope" = lean cost, not scope mismatch. The combined verdict (lean + BETTER = efficient; lean + WORSE = structural issue) is what the two-axis output surfaces.

---

## 9. Output: efficiency_score.py

Per-session output fields:
- `task_type`: classified type
- `real_tokens`: cache-corrected token count
- `scope_status`: `"in_scope"` | `"out_of_scope"` | `"no_baseline"`
- `band_verdict`: `"above_p75"` | `"within_band"` | `"below_p25"` | `"unavailable"`
- `p25`, `median`, `p75`: reference band (None if unavailable)
- `interpretation`: human-readable explanation of the token verdict
- Judge axis (`judge_verdict`, `judge_score`, `judge_reasoning`): populated when judge entry provided

The two axes are labeled separately. No composite "efficiency score" is computed — the product is the combination of token band + trajectory verdict, each with its own domain of validity.

---

## 10. Limitations — Core (Not Footnotes)

These limitations are load-bearing for how the efficiency number should be interpreted. They are stated here and must be carried forward into any product framing.

### 10.1 Single-developer, expert-prompted reference corpus

The baseline is built from 75 MUCH_BETTER sessions from a single developer (gaurav-gandhi-2411, ~97% of the strict-gate pool). These sessions are predominantly structured orchestrator workflows with explicit `ROLE / STEP / CONSTRAINT` prompting that eliminates exploratory waste at the prompt level.

**Consequence:** the baseline encodes "what an efficient task costs WHEN DRIVEN BY EXPERT PROMPTING." A customer with ordinary or less-structured prompting may score as above-p75 not because their agent wasted tokens, but because their prompting style differs from the baseline's. The efficiency number is calibrated to expert-orchestrated CC sessions.

**Armand0e evidence:** Kimi-k2.6 sessions cleared the strict gate at 6% (2/34) vs 69% (75/109) for local sessions. Investigation confirmed this is a real behavioral gradient, not judge bias — Kimi sessions consistently earn BETTER (65% under loose gate) rather than MUCH_BETTER because they have minor correctly-identified redundancies. The strict gate discriminates, but the cause is population-level quality difference, not agent-family bias.

**Cross-customer generalization is unvalidated.** Each customer's first baseline is measured against this single-developer reference. Per-customer adaptive baselines (launch-2) are the path to correction.

### 10.2 Token axis scope boundary

The token-economy verdict fires only for sessions within the reference's scope range (turn count ≥ p10_turns for the session's type). Sessions below this threshold return UNAVAILABLE for the token axis and deliver the trajectory verdict only.

Coverage in the held-out set: 50% in-scope, 50% UNAVAILABLE. The baseline is calibrated to substantial orchestrator workflows; shorter sessions have no comparable reference. The path to broader token-economy coverage is more reference data across task complexity ranges, not wider quality bands.

### 10.3 feature-build: ZERO held-out validation

All 3 held-out feature-build sessions had turn counts of 12, 14, and 35 — well below the feature-build scope gate of 166 turns. The feature-build band is computed from 12 MUCH_BETTER baseline sessions, but has received zero held-out validation. The band may be correct, but there is no evidence from this dataset that it correctly identifies feature-build token waste vs efficiency. Use with caution.

### 10.4 No human gold — no accuracy claims

The judge (qwen3:30b-a3b) was validated at rho = 0.79 against a Sonnet reference LLM (cluster-excluded). This is instrument coherence — two LLMs agreeing — not accuracy. No human ground truth exists. No "calibrated to human experts" or "human-validated" claims are permitted.

The circularity check (r = −0.0801, n.s.) confirms that token baselines and judge scores are independent, but both axes are LLM-derived. The combined verdict is two independent LLM signals, not a human-grounded assessment.

### 10.5 CC-caching-native tokens only

`real_tokens` is valid for Claude Code sessions with prompt caching (cache_hit_rate ≥ 0.80). For agents that do not use prompt caching (Kimi-k2.6, potentially Aider, others), `real_tokens` accumulates differently and is not comparable to this baseline. Non-Claude-caching agents need their own baseline built from their own session pool.

### 10.6 The efficiency number is diagnostic, not a score

The product outputs two labeled verdicts (token band + trajectory quality), not a single efficiency number. The composite formula from B1 (outcome_score, judge_score, h2_score, p25_token_ratio) is not used here — `resolved` and H2 remain unavailable for CC sessions, and the SWE-bench p25 baselines are incommensurable with CC token counts.

---

## 11. Infrastructure Closeout

All B2 GPU VMs deleted after scoring runs. Actual spend:
- B2 pool scoring (asia-east1-a, g2-standard-8 SPOT, ~5 hr): estimated ~$2.80
- B2 step3 rescore (16 sessions, num_predict=6144): estimated ~$0.36
- CC validation (report 07): ~$0.37
- **B2 total estimated: ~$3.53 USD**
- Cumulative Anthropic API spend: ~$2.59 of $5 cap (judge is local, no added spend this iteration)

---

## 12. What Changes When More Data Arrives (Launch-2 Path)

The two axes work now. What limits them is reference data volume:
- **Scope coverage:** each additional MUCH_BETTER session in an underrepresented scope range (shorter sessions, different task sizes) lowers the p10 scope gate and makes the token axis applicable to more sessions.
- **Per-customer baselines:** instead of a single-developer reference, each customer accumulates their own MUCH_BETTER sessions over time. The efficiency ratio then measures "you vs your own best runs" rather than "you vs one developer's expert-orchestrated sessions."
- **feature-build validation:** once held-out feature-build sessions accumulate at comparable scope (>166 turns), the band can be validated.
- **Cross-provider coverage:** non-caching agents (Kimi, Aider) need separate pool scoring with their own token-accounting convention, yielding per-provider baselines.

The baseline framework (quality gate → task taxonomy → scope gate → band) is the launch-2 foundation. The parameters change; the structure holds.
