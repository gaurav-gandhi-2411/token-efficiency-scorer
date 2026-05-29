# Report 05 — Architecture Pivot: Hybrid LLM-Judge Scorer

**Author:** Gaurav Gandhi  
**Date:** 2026-05-30  
**Status:** FINAL — supersedes Phase A.2 rebuild scope defined in spec.md  
**Scope-change authority:** User escalation 2026-05-30; Path B pivot authorized after Phase A.1
IAA results invalidated heuristic-primary architecture.

---

## 0. Research-and-Design Plan (orientation record)

This section records the plan reviewed with the user before writing, per the PROCESS requirement.

**Research completed:** Four focused SOTA sweeps (pairwise vs pointwise reliability, RLVR hybrid
architecture, judge distillation cost cascade, calibration targets in practice). Eleven papers
verified with arXiv IDs. Key design reversals made on the evidence before writing:

- Pairwise → reference-based pointwise: reversed per arXiv:2504.14716 (35% vs 9% flip rate)
  and arXiv:2602.02219 (permutation fix removes pointwise's structural position bias). User
  approved reversal.
- Layer 3 redesigned around a 40-session human gold set after the user corrected the plan:
  the 191 LLM-labeled sessions have Phase A.1-confirmed unreliable labels and cannot serve as
  human ground truth.
- Local Qwen3-8B/Ollama as the prototype judge: user directed evaluation of local open models
  before any OpenAI dependency.
- Score weights marked PROVISIONAL throughout.

---

## 1. Executive Summary

Phase A.1 validated H2 (duplicate_message, κ = 0.825) and invalidated H1, H3, H4 (κ = 0.15,
0.43, 0.19 respectively), with confirmed model-family rubric drift on H4 (Sonnet over-fired on
72/310 turns versus the GPT-OSS bulk labeler). The three failed heuristics measure concepts —
context-load redundancy, trajectory backtracking, tool-result utilization — that are inherently
contextual and resist per-turn rubric formalization regardless of rubric quality. We are pivoting
to a three-layer hybrid architecture under user-authorized scope change.

**Layer 1** computes seven deterministic, free features from the trace (including H2 as a
surviving signal and the per-task p25 token counterfactual). **Layer 2** runs a reference-based
pointwise LLM judge — from a non-Claude model family — that rates each session's efficiency
against the described p25 reference standard for its domain. **Layer 3** calibrates the judge
against a 40-session human gold set (the only source of genuine ground truth), with the 191
GPT-OSS-labeled sessions repurposed as a secondary consistency signal, not human ground truth.

The prototype judge is Qwen3-8B via Ollama ($0 inference, different family from Claude, proves
enterprise self-hosting). Production path is a domain-fine-tuned 24B judge (FLAMe-style,
~$50–200 to train). Score weights are provisional pending human gold-set tuning. Testless
sessions (common in enterprise) renormalize the judge to 46% of composite weight; these scores
carry an explicit `reliability: LOW` flag. The kill criterion is Spearman ρ < 0.55 versus the
human gold set after three prompt iterations.

---

## 2. Why Heuristic-Primary Failed

### 2.1 Phase A.1 IAA Results

| Heuristic | Cohen's κ (GPT-OSS vs Sonnet-4-6) | Threshold | Verdict |
|---|---|---|---|
| H1 redundant_read | **0.15** | ≥ 0.60 | FAIL |
| H2 duplicate_message | **0.825** | ≥ 0.60 | PASS |
| H3 backtrack | **0.43** | ≥ 0.60 | FAIL |
| H4 tool_result_used | **0.19** | ≥ 0.60 | FAIL |

Additional finding: Sonnet over-fired H4 on 72 of 310 turns (23%) versus the GPT-OSS bulk
labeler — a systematic model-family effect, not random noise. Both models were given the same
rubric; the disagreement is in how a capable model interprets "incorporation of a tool result."
This confirms that the rubric is under-specified for the concept, not that either model is wrong.

### 2.2 Why Rubric Rewrites Cannot Fix H1, H3, H4

The root cause is conceptual, not textual.

**H1 (redundant_read):** A file re-read is "redundant" only if the agent retrieved no new
information AND had no legitimate reason to reconfirm state. Both conditions require knowing
the agent's intent across multiple turns. A deterministic rule (same path, no intervening write)
fires on justified re-checks after test failures. Any rubric clause that adds the intent condition
reduces to "the annotator decides per instance" — which is what κ = 0.15 already measures. Phase
A.1 also confirmed the H3 regex fires 0 of 218 turns on real CC-Bench sessions (report 03, §6),
showing the vocabulary is scaffold-specific; no pattern addition generalizes.

**H3 (backtrack):** Recognizing a strategy reversal requires understanding what the agent believed
before and after a turn boundary — holistic contextual inference that no phrase-matching rule
captures. The distinction between "real backtrack" and "normal forward planning phrased with hedge
language" requires full-session context.

**H4 (tool_result_used):** Whether downstream reasoning "incorporates" a tool result is a credit
assignment question across turns. Two equally competent annotators can reach different verdicts
on the same turn depending on how strictly they interpret "incorporated." The Sonnet over-firing
is the quantitative proof.

**H2 passes because it is structural, not semantic.** Near-duplication (>90% text similarity
between turns) is an objective property of the output stream. No intent inference is required.

### 2.3 What This Means

H1, H3, and H4 encode genuine waste phenomena. The failure is that they cannot be operationalized
as local, per-turn, rubric-checkable rules. The appropriate representation for these concepts is a
holistic LLM judge with full session context and an explicit reference standard — which is Layer 2.

---

## 3. SOTA Grounding

### 3.1 Pairwise vs Reference-Based Pointwise Judging

The MT-Bench / Chatbot Arena lineage (Zheng et al., arXiv:2306.05685, NeurIPS 2023) established
LLM-as-judge with pairwise preference as the dominant paradigm and documented position bias
(>10% accuracy shift from swapping response order) and verbosity bias. Subsequent work refines
the picture substantially.

**Stability under perturbation (arXiv:2504.14716, Tripathi et al., 2025):** Pairwise preference
judgments flip in approximately 35% of cases under prompt perturbation (rewording, reordering),
versus approximately 9% for absolute pointwise scores. Pairwise better tracks human preference
ordering when no reference exists; pointwise is more stable when an anchored reference standard
is available.

*Implication for our design:* We have a fixed reference standard (p25 efficient trace per
domain). Reference-based pointwise — rating the actual session against the described reference
standard — gives a 26pp lower flip rate and requires one judge call rather than two. This is
strictly better for our use case. The original design specified pairwise; this report reverses
that decision on the evidence above, with user approval.

**Rubric-criteria position bias (arXiv:2602.02219, Xu et al., 2026):** Pointwise rubric scoring
exhibits structural position bias toward criteria appearing first in the rubric list. Balanced
permutation (randomizing criterion order) corrects this: average +0.027 Pearson, +0.032 Spearman
across models; +0.089 Spearman for Qwen3-32B on HANNA benchmark. Pointwise is not automatically
safer than pairwise — it requires an explicit position-bias fix.

**Criterion injection vs absence (arXiv:2506.13639, Yamauchi et al., 2025):** Including explicit
evaluation criteria raises Krippendorff α from ~0.60 (criteria absent) to 0.908 (GPT-4o, full
criteria + reference). The gain from criterion injection (+0.30 α) dwarfs the gain from criterion
order randomization (+0.032 Spearman). For open-source models, Llama-3.1-70B achieves α = 0.806
with full criteria — still above the production threshold. Without explicit criteria on open-ended
code tasks, inter-judge κ drops to 0.16 (arXiv:2604.27727, Amin et al., 2026). **Explicit
criteria are not optional.**

CoT vs direct scoring produces minimal reliability difference (α 0.908 vs 0.912); mean-score
averaging over samples modestly improves Spearman r (0.666 vs 0.635). CoT is retained for
interpretability, not reliability.

### 3.2 Hybrid Verifiable + LLM Judge Pattern

**HERO (arXiv:2510.07242, Tao et al. / FAIR Meta, 2025):** Combines a binary verifier (0/1
test pass) with a dense LLM reward model via stratified normalization — RM scores are bounded
within verifier-defined correctness groups, and variance-aware weighting balances the two signals.
Qwen3-4B with HERO on verifiable tasks: 62.0 vs verifier-only 58.3 vs RM-only 56.4. On
hard-to-verify tasks: HERO 66.3 vs verifier-only 57.1 (+9.2pp). Neither signal captures the
full picture alone; the hybrid consistently outperforms either.

We adapt HERO as a scoring-time (not training-time) architecture: test execution (verifiable,
binary outcome_score) and LLM judge (dense, holistic judge_score) are combined in the composition
formula with explicit weights and a no-test fallback (§6).

**RLVRR (arXiv:2601.18533, Jiang et al., 2026, ICLR 2026):** Extends RLVR to open-ended
generation by decomposing rewards into verifiable content signals and LLM-judged quality. Beats
SFT on 10× more data across 10+ benchmarks. Validates the hybrid reward pattern for tasks where
full verifiability is absent.

**Honest gap:** "Anthropic Hybrid Norm" does not appear in any Anthropic-authored paper, blog
post, or system card as of May 2026. Third-party characterizations describe Anthropic running RLVR
for coding quality, but no architecture details are published. HERO (FAIR Meta) is the closest
peer-reviewed analog and is used as the design reference throughout this report.

### 3.3 Three-Tier Judge Cost Cascade and Distillation

**Small judge distillation:**

*Prometheus 2* (arXiv:2405.01535, Kim et al., EMNLP 2024): fine-tuned judge on direct +
pairwise judge data. Mixtral-8x7B base achieves 85.52% pairwise accuracy on HHH Alignment vs
GPT-4's 90.95% — a 5.4pp gap at open-weight, self-hostable cost. Pearson correlation on FLASK:
Prometheus 2-8x7B = 0.659 vs GPT-4 = 0.736.

*FLAMe-RM-24B* (arXiv:2407.10817, Vu et al. / Google DeepMind, 2024): 24B parameter judge
fine-tuned on 53 quality assessment tasks achieves 87.8% RewardBench accuracy, exceeding
GPT-4-0125 (85.9%) and GPT-4o (84.7%). FLAMe-Opt-RM-24B matches near-equivalent accuracy (87.0%)
using ~25× fewer training examples via tail-patch fine-tuning. Key finding: domain-specific
fine-tuning on a moderate-size model reliably beats frontier API judges.

**Cascade routing (arXiv:2510.20369, Xu et al., 2025):** Routing 9.2% of uncertain comparisons
to a frontier judge raises RewardBench from 87.3% to 89.2%; routing 42.5% reaches 91.6%. On
coding tasks specifically: +14.5pp accuracy gain from uncertainty-guided routing. Application:
in production, route only calls where Layer 2 `confidence < 0.3` to a higher-tier judge.

**Production three tiers:**

- **Tier 1 (Human):** 40 sessions, consultant-rated on efficiency. Run once; ground truth. Never
  at inference.
- **Tier 2 (Frontier, e.g. GPT-4o-class):** Augments calibration labels from 40 to ~200 for
  fine-tuning. Run once per calibration cycle. Estimated cost: ~$20 (191 sessions × 5K tokens ×
  $0.15/MTok).
- **Tier 3 (Distilled small judge, self-hosted):** Production inference. Cost: $0 (local Ollama)
  to ~$0.005/session (GPU endpoint). Calibrated to Tier 1 human labels.

### 3.4 Calibration Targets

**Human-human baseline (arXiv:2510.09738, Han et al. / NVIDIA, 2025):** Three human raters
produce Fleiss' κ = 0.79, Krippendorff's α = 0.79 on quality ratings — the empirical ceiling
for LLM judge calibration. 50% of tested LLMs clear the Pearson r ≥ 0.80 production screening
bar; Qwen3-30B-A3B achieves κ = 0.780 (nearly matching human κ = 0.801).

**Achievable targets by model tier (arXiv:2506.13639):**
- GPT-4o class: α = 0.908 (full criteria + reference)
- Llama-3.1-70B class: α = 0.806 (passes production bar)
- Qwen3-8B (our prototype): α ≈ 0.70–0.80, extrapolated from open-model benchmarks. **Unverified
  for our domain.** The kill criterion catches failure at this tier.

**Position bias protocol (arXiv:2406.07791, Shi et al., AACL-IJCNLP 2025):** Position Consistency
(PC) ranges 0.23–0.82 across 15 judges; GPT-4-class judges achieve PC = 0.82. For reference-based
pointwise, the position risk is criterion-order bias, not A-vs-B presentation order. The
permutation fix from arXiv:2602.02219 is the correct mitigation.

**Production target:** Spearman ρ ≥ 0.75 between judge scores and human gold ratings on the
40-session gold set. This is below the human-human ceiling of 0.79 and achievable by
Llama-70B-class models on general benchmarks.

---

## 4. Three-Layer Architecture

### 4.1 Layer 1 — Deterministic Features (zero cost)

All seven signals are extracted from the trace JSON with no model calls. They constitute the
`waste_vector` passed to both the judge (as context in the Layer 2 prompt) and the score
formula.

| Signal | Source in trace | Formula | Unit |
|---|---|---|---|
| `test_outcome` | `result.resolved` or test harness field | {1.0: pass, 0.5: no_test_present, 0.0: fail} | float |
| `total_input_tokens` | Sum `usage.input_tokens` across all assistant turns | Σ input_tokens | int |
| `total_output_tokens` | Sum `usage.output_tokens` across all assistant turns | Σ output_tokens | int |
| `turn_count` | Count of assistant turns | raw count | int |
| `h2_duplicate_count` | H2 heuristic (κ = 0.825, survives Phase A.1) | turns where cosine-sim(turn_t, any prior turn) > 0.90 | int |
| `cache_hit_rate` | `usage.cache_read_input_tokens` / `total_input_tokens` | [0, 1]; 0 if no cache field | float |
| `p25_token_ratio` | (total_input + total_output) / `p25_ref_tokens[domain]` | clamped [0.3, 5.0] | float |

**p25_ref_tokens[domain]** is the 25th percentile total-token count among resolved sessions in
that domain, computed from the existing 200-session corpus (report 03, §5.3). A session with
`p25_token_ratio = 1.0` matches the efficient baseline; `p25_token_ratio = 2.0` spent twice as
many tokens.

**Thin-domain fallback:** For domains with n < 10 resolved sessions (db_orm n=7, web_api n=3,
testing_ci n=2), use corpus-wide p25 as the reference and tag with `reference_quality: LOW`.

**OOD fallback:** When the domain classifier returns "unknown" (0% coverage on CC-Bench, per
report 03 §6), use corpus-wide p25 and tag `ood: true`. The difficulty_norm fallback also applies
(§6.1).

### 4.2 Layer 2 — Reference-Based Pointwise Judge

**Design reversal note:** The user's original specification called for pairwise format. This
report reverses to reference-based pointwise on the evidence from arXiv:2504.14716 (35% vs 9%
flip rate) and arXiv:2602.02219 (permutation fix removes the resulting position bias). Because
we have a fixed reference standard (p25 trace), reference-based pointwise is strictly better:
lower flip rate, one call instead of two, no position-swap overhead. User approved this reversal.

**One judge call per session.** Input prompt:

```
TASK: {task_description, first 200 chars}

DOMAIN: {domain}
REFERENCE STANDARD: A p25-efficient {domain} session resolves a task of this type using
approximately {p25_ref_tokens} total tokens and {p25_ref_turns} median turns. Domain baseline
resolve rate: {domain_resolve_rate:.0%}. Reference-level sessions are characterized by: direct
file edits without repeated re-reads, no failed retries of identical commands, no repeated
assistant outputs, and tool results that influence the next action.

SESSION UNDER EVALUATION:
  Total tokens: {total_input + total_output} ({p25_token_ratio:.2f}x the p25 reference)
  Turn count: {turn_count}
  Cache hit rate: {cache_hit_rate:.0%}
  Duplicate turns (H2): {h2_duplicate_count}

Turn-by-turn summary:
{turn_summaries}   ← one line per turn: "T3: read_file(foo.py) → 340 tokens, result: ok"

EVALUATION CRITERIA (apply all five in this fixed order):
C1. Token economy: how close to the p25 efficient baseline is total token spend?
C2. Turn economy: are turns advancing task state vs exploratory or redundant?
C3. Trajectory coherence: does the agent avoid unanchored backtracking and exact retries?
C4. Tool utilization: are tool results integrated into the next action or reasoning?
C5. Context discipline: does the agent avoid unnecessary re-reads and verbose tool outputs?

Rate the session's efficiency RELATIVE TO THE REFERENCE STANDARD above.
Respond with ONLY valid JSON:
{
  "verdict": <MUCH_BETTER | BETTER | SIMILAR | WORSE | MUCH_WORSE>,
  "waste_categories": <subset of ["redundant_read", "failed_retry", "context_bloat",
                                   "trajectory_drift", "duplicate_output"]>,
  "confidence": <0.0-1.0; use < 0.5 for ambiguous sessions or OOD domains>,
  "reasoning": <1-2 sentences citing specific turn numbers>
}
```

**Permutation fix:** Criterion order (C1→C5) is fixed and canonical across all calls. Consistency
is preferred over per-call randomization for reproducibility. If calibration analysis detects a
first-criterion bias (C1-aligned signals disproportionately predict verdicts), switch to balanced
permutation in the next calibration cycle.

**Verdict float mapping:**

| Verdict | Float |
|---|---|
| MUCH_BETTER | 1.00 |
| BETTER | 0.75 |
| SIMILAR | 0.50 |
| WORSE | 0.25 |
| MUCH_WORSE | 0.00 |

**Low-confidence handling:** Calls where `confidence < 0.3` are tagged `reliability: LOW` and
flagged for optional routing to a higher-tier judge in production (§5). For the prototype, they
are included in scores with the flag set.

### 4.3 Layer 3 — Calibration Harness

**The human calibration gap:** The 191 sessions in `data/validation-corpus/annotations/gpt_oss/`
have Phase A.1-confirmed unreliable labels (H1 κ = 0.15, H4 κ = 0.19). They cannot serve as
human ground truth for judge calibration. Layer 3 is designed around a genuine human gold set.

#### Human Gold Set

**Construction:**
- **n = 40 sessions**, stratified: 5 domains × 2 resolution outcomes × 4 sessions per cell.
  Domains: lib_general, type_checker, data_ml, cloud_devops, graph_geo (the five largest by
  corpus count). Resolution: 2 resolved + 2 unresolved per domain.
- Sessions drawn from existing `data/validation-corpus/traces_normalized/` — no new API calls.

**Rating scale (shown to annotator):**

```
1 — Extremely wasteful: massive redundancy, failure loops, context snowball;
    far more turns/tokens than a competent approach would need.
2 — Notably wasteful: obvious redundant operations or unnecessary backtracking.
3 — Average: typical waste for this task type; nothing conspicuously efficient or wasteful.
4 — Efficient: direct path, minimal redundancy, most turns productive.
5 — Exemplary: optimal or near-optimal token use; no notable waste signals.
```

**Rating interface (to build, design only here):**
A terminal script that displays one session summary at a time (the same turn-by-turn summary
format used in the Layer 2 prompt — 20–50 lines, not the raw full trace) and records:
```
{session_id, domain, resolved, rating: 1-5, note: str, timestamp}
→ appended to data/human-gold/ratings.jsonl
```
Estimated annotation burden: 3–4 minutes per session × 40 sessions ≈ 2–3 hours.

#### Calibration Measurement

After collecting all 40 ratings:
1. Run Layer 2 judge on all 40 sessions; collect `verdict` and `confidence` per session.
2. Compute `judge_score = verdict_float(verdict)` per session (§4.2 mapping).
3. Normalize human rating: `human_score = rating / 5` → [0.20, 1.00].
4. Compute Spearman ρ and Kendall τ between `judge_score` and `human_score` (n = 40).

**Target:** Spearman ρ ≥ 0.75.

**Version pinning:**
```
calibration_run_id = f"{judge_model_id}:{sha256(prompt_template)[:8]}:{date_yyyymmdd}"
```
All calibration results stored in `data/calibration/{calibration_run_id}.json`. Every score
output embeds the `calibration_run_id` that was active when the score was produced.

**Drift monitoring:** Re-run calibration on the gold set quarterly or after any judge model
update. Alert if ρ drops > 0.05 from the baseline run.

#### Secondary Set (191 LLM-labeled sessions)

The GPT-OSS-labeled corpus is a secondary consistency signal, **not** human ground truth. Metric:
Spearman ρ between judge verdict floats and `llm_total_waste_pct` field across 191 sessions.
Expected to be lower than human gold ρ (LLM labels are noisy). Acceptable floor: ρ ≥ 0.40.
Value: larger n for detecting systematic judge failures (e.g., judge assigns SIMILAR to all
sessions regardless of waste signal, a failure mode invisible at n = 40).

---

## 5. Judge Model + Hosting Decision

### 5.1 Family Independence Constraint

The coding agents we measure run on Claude Code (Anthropic family). A Claude-family judge would
risk self-enhancement bias — favoring outputs stylistically similar to its training distribution.
All options below exclude Anthropic models.

### 5.2 Option Analysis

| Option | Cost / session | Latency | Family | Self-hostable | Dependency |
|---|---|---|---|---|---|
| **Qwen3-8B via Ollama (local)** | **$0** | 20–30s CPU / 3–5s GPU | Alibaba/Qwen | YES | None |
| Qwen3-30B via Groq | ~$0.003 | 1–2s | Alibaba/Qwen | NO | Groq entitlement (currently blocked on free tier) |
| GPT-4o-mini via OpenAI | ~$0.0003 | 0.5s | OpenAI | NO | Adds new OpenAI provider |
| Distilled 24B, fine-tuned (production) | ~$0.001–0.005 | 2–5s w/ GPU | Non-Anthropic base | YES | One-time ~$50–200 training run |

**Local hardware cost for Qwen3-8B (Q4 quantized):**
- 16–32GB RAM (consumer laptop, CPU only): 20–30s per session; $0/session; viable for dev and
  calibration runs.
- Consumer GPU (RTX 3090 / 16GB VRAM): 3–5s per session; $0/session; viable for batch
  production on a dev machine.
- Cloud A100 batch: ~$2–3/GPU-hr; at 100 sessions/hr ≈ $0.02–0.03/session. Still ~10× cheaper
  than GPT-4o-mini at enterprise scale.

### 5.3 Recommendations

**Prototype: Qwen3-8B via Ollama (local)**

Rationale:
1. $0 marginal inference cost — calibration on 40 gold sessions costs nothing.
2. Alibaba/Qwen family — family independence constraint satisfied structurally.
3. Enterprise self-hosting story — organizations buying a coding-agent scorer want on-premises
   deployment without API key management. Local Ollama serves that story directly.
4. No new provider dependency — OpenAI integration is not required.
5. Expected accuracy: α ≈ 0.70–0.80 (extrapolated from Llama-70B-class benchmarks; unverified
   for our domain — the kill criterion catches failure at this tier; see §9.2).

Fallback path if Qwen3-8B does not clear ρ ≥ 0.55 after three prompt iterations: escalate to
Qwen3-30B via Groq (once entitlement clears), then to a fine-tuned distilled judge, before
touching OpenAI.

**Production: Distilled 24B judge fine-tuned on our calibration corpus**

Path:
1. Collect 40 human gold sessions via the Layer 3 rating interface.
2. Augment to ~200 labeled examples: run a GPT-4o-class judge on the 191 LLM-labeled corpus;
   collect verdict + reasoning per session. Estimated cost: ~$20 (191 × 5K tokens × $0.15/MTok).
3. Fine-tune Prometheus 2-8x7B or FLAMe base on the ~200 domain-specific examples.
4. Estimated fine-tuning cost: $50–200 on a cloud A100 (3 epochs, ~200 examples, ~5K tokens
   each).
5. Serve via Ollama or vLLM on self-hosted hardware or a Cloud Run endpoint.

Expected outcome (based on FLAMe, arXiv:2407.10817): domain-specific fine-tuning of a 24B
model on moderate data can match or exceed GPT-4o on the target benchmark at 1/10th the
per-call cost. Confidence is MEDIUM — this is extrapolated from a general-domain result (see §10).

**Groq entitlement dependency:** The prototype does NOT depend on Groq. Groq becomes relevant
only for production latency at scale (1–2s vs 3–5s local GPU). If entitlement remains blocked
at production time, self-hosted vLLM on a GPU node is the alternative.

---

## 6. Score Composition Formula

### 6.1 Primary Formula

```
efficiency_score(session) = composite_quality(session)
                           / (p25_token_ratio(session) × difficulty_norm(domain))

composite_quality = w_outcome × outcome_score
                  + w_judge   × judge_score
                  + w_h2      × h2_score

Default weights (PROVISIONAL — tune against human gold set after collection):
  w_outcome = 0.50
  w_judge   = 0.35
  w_h2      = 0.15

Component definitions:

  outcome_score   = {1.0: test_pass, 0.5: no_test_present, 0.0: test_fail}

  judge_score     = verdict_float(verdict)
                    {MUCH_BETTER: 1.00, BETTER: 0.75, SIMILAR: 0.50,
                     WORSE: 0.25, MUCH_WORSE: 0.00}

  h2_score        = 1.0 - (h2_duplicate_count / max(turn_count, 1))
                    clamped to [0, 1]

  p25_token_ratio = (total_input_tokens + total_output_tokens)
                   / p25_ref_tokens[domain]
                    clamped to [0.3, 5.0]

  difficulty_norm = 1.0 / domain_resolve_rate[domain]
                    Empirical priors from report 03 §5.3:
                      type_checker:  7.1×  (resolve rate 14%)
                      data_ml:       2.4×  (resolve rate 42%)
                      lib_general:   1.7×  (resolve rate 59%)
                      graph_geo:     1.1×  (resolve rate 95%)
                    OOD / unknown fallback: 2.0×  (50% assumed resolve rate)
```

**Score range and interpretation:** efficiency_score is unbounded above 1.0. A session matching
the p25 token reference with perfect quality scores ≈ 1.0 (before difficulty normalization). A
session that is twice as efficient as the p25 reference and achieves full quality scores ≈ 2.0.
Scores below 0.3 indicate poor quality AND significant token waste. Difficulty normalization
means a perfect score on a hard domain (type_checker, difficulty 7.1×) is not penalized against
a perfect score on an easy domain (graph_geo, 1.1×).

### 6.2 No-Test Fallback

**Critical risk:** Free-form Claude Code sessions — the majority of real enterprise usage —
have no test harness. When `outcome_score = 0.5` (neutral), the judge component carries 46%
of the composite weight after renormalization. This is the scenario where the verifiable anchor
is absent and where judge miscalibration causes the most damage to score reliability.

Renormalized formula for testless sessions:
```
composite_quality_testless = (0.50 × 0.5 + 0.35 × judge_score + 0.15 × h2_score)
                             / (0.50 × 0.5 + 0.35 + 0.15)

                           = (0.25 + 0.35 × judge_score + 0.15 × h2_score) / 0.75
```

Judge weight in testless sessions: 0.35 / 0.75 = **46.7%** of total composite.

Output flags for all testless scores:
```json
{
  "has_test": false,
  "reliability": "LOW",
  "dominant_signal": "judge",
  "score_label": "efficiency_estimate"
}
```

Any reporting layer must surface the `reliability: LOW` label. Testless scores should not be
compared directly to tested scores in the same ranking or dashboard view without disclosure.

### 6.3 Weight Tuning Procedure

After collecting the 40 human gold ratings:
1. Grid-search over (w_outcome, w_judge, w_h2) with Σw = 1 constraint and step size 0.05.
2. Maximize Spearman ρ on the gold set using leave-one-out cross-validation (mitigates
   overfitting at n = 40).
3. Publish tuned weights as `config/weights_v{N}.yaml` alongside the active `calibration_run_id`.
4. The default weights (0.50 / 0.35 / 0.15) are held until tuning is complete.

**Rationale for default weights:** The verifiable test signal dominates (0.50) because it
cannot be gamed by a judge optimizing for plausible-sounding output. The judge signal (0.35)
drives qualitative assessment of the H1/H3/H4 phenomena now absorbed holistically. H2 (0.15)
is cheap and reliable but fires infrequently (~10% of turns in the corpus), so it should not
dominate the composite.

---

## 7. Sellability Artifacts

An enterprise technical evaluator needs six concrete, verifiable demonstrations in a POC:

**1. Calibration certificate**
```
Judge:             {model_id} (version pinned, immutable at score time)
Calibration run:   {judge_model_id}:{prompt_sha256[:8]}:{date}
Human gold set:    40 sessions, 5 domains, resolved/unresolved balanced
Human rater:       Software engineering consultant (role identified)
Spearman ρ:        {measured, target ≥ 0.75}
Kendall τ:         {measured}
Human-human ceiling (arXiv:2510.09738): ρ = 0.79
Reproducible from: data/human-gold/ratings.jsonl + judge call logs
```
This is the primary trust artifact. It transforms "AI judges AI" into a measurable, third-party
verifiable claim.

**2. Family independence attestation**
Judge model is from {Alibaba/Qwen family}, not Anthropic. Sessions produced by Claude Code
agents are scored by a judge with no training distribution overlap with the agent. Self-enhancement
bias is structurally prevented. This directly addresses the enterprise concern that LLM-based
evaluation tools may inadvertently favor the same vendor's models.

**3. Comparative scorecard on SWE-bench sessions**
Score the top-decile and bottom-decile sessions (by resolution outcome × turn count) from the
200-session corpus. Expected separation: top-decile median > 3.0, bottom-decile median < 1.0.
Demonstrates face validity — the scorer correctly identifies efficient and wasteful sessions that
a domain expert can independently verify.

**4. Correlation with business outcomes**
Spearman ρ between efficiency_score and session resolution outcome across the 200-session corpus.
Expected: positive correlation (resolved sessions score higher on average). This is the "does
the score track what matters?" demonstration for business stakeholders.

**5. Secondary LLM-label agreement report**
Spearman ρ on 191 GPT-OSS-labeled sessions. Explicitly labeled as secondary — not human ground
truth. Disclosed honestly: lower ρ expected than human gold due to Phase A.1-confirmed label
noise. Value: larger n for detecting systematic judge failures.

**6. Cost transparency card**
| Deployment | Cost/session | Setup |
|---|---|---|
| Prototype (Qwen3-8B Ollama) | $0 | 16-32GB RAM, ~30min Ollama setup |
| Production (distilled 24B, GPU) | ~$0.001–0.005 | One-time ~$50–200 fine-tuning |
| Comparison: GPT-4o API | ~$0.015 | No self-hosting |

At 1M sessions/year: GPT-4o ≈ $15K/yr vs distilled local ≈ $1–5K/yr + hardware.

---

## 8. Migration Plan + Next-Iteration Scope

### 8.1 What Is Reused

| Asset | Destination | Required action |
|---|---|---|
| `data/validation-corpus/annotations/gpt_oss/` (191 sessions) | Layer 3 secondary eval set | Relabel in docs as non-human ground truth; no file changes |
| H2 heuristic implementation (`scripts/10_remeasure_heuristics.py`) | Layer 1 deterministic signal | Extract into standalone function; document κ = 0.825 |
| Domain taxonomy + resolve-rate priors (report 03 §5.3) | `difficulty_norm` lookup table | Extract to `config/domain_priors.yaml` |
| p25 token percentile by domain (computed from 200-session corpus) | Layer 2 reference standard | Compute once from existing traces; output to `config/p25_refs.yaml` |
| `data/cost-log.jsonl` | Continued cost tracking | Append-only; no changes |

### 8.2 What Is Retired

| Asset | Reason |
|---|---|
| H1, H3, H4 heuristic implementations | Absorbed into judge's holistic assessment; no longer scored independently |
| H1/H3/H4 F1 measurement sections of `scripts/10_remeasure_heuristics.py` | No longer needed; archive rather than delete |
| Phase A.2 BGE-embedding semantic backtrack redesign | No longer needed; leave noted as deferred in spec.md but do not build |

### 8.3 Next Iteration Scope (input to the next spec.md)

**Must build:**
1. Judge prompt template module + output schema parser (validates `verdict`, `waste_categories`,
   `confidence`, `reasoning`; rejects malformed JSON)
2. p25 reference computation script: reads existing corpus, outputs `config/p25_refs.yaml` per
   domain; no API calls required
3. Layer 3 rating interface script (terminal UI; displays session summary; records 1-5 + note
   to `data/human-gold/ratings.jsonl`)
4. Calibration runner: executes Layer 2 judge on all 40 gold sessions; computes and stores
   Spearman ρ, Kendall τ; writes `data/calibration/{run_id}.json`
5. Score composition module `scripts/20_score_session.py` implementing §6 formula exactly
6. Configuration artifacts: `config/domain_priors.yaml`, `config/p25_refs.yaml`,
   `config/weights_v1.yaml`

**User action required (not automatable):**
- Annotate 40 gold sessions via the rating interface (estimated 2–3 hours)

**Validation gates (in order):**
- p25 reference computation produces non-null values for all 5 primary domains
- Judge calibration Spearman ρ ≥ 0.75 on gold set (production gate)
- Judge calibration Spearman ρ ≥ 0.40 on LLM-label corpus (consistency gate)
- `20_score_session.py` produces monotonically ordered scores on a synthetic 3-session
  test case (worst / average / best waste profile)

### 8.4 What We Are NOT Building Yet

- Production scoring API (FastAPI service with `/metrics` endpoint)
- Web UI or dashboard
- Fine-tuned distilled production judge (depends on collecting gold set first)
- Multi-language / non-Python corpus (Phase A.3, still deferred)
- PRM-based architecture (v2, contingent on kill criterion; see §9.2)
- Real-time or streaming scoring
- OpenAI provider integration (not needed if local Qwen3-8B path works)
- Aider, Cursor, or Copilot scaffold support (Phase A.3)

---

## 9. Risks + Kill Criteria

### 9.1 Top Failure Modes

**Risk 1 — Judge gives unfaithful CoT (HIGH severity)**
The judge outputs convincing turn-level reasoning that post-hoc rationalizes a verdict not
actually derived from the evaluation criteria. This is the "judge gaming" failure mode
(arXiv:2604.23178). It is undetectable from individual calls. Mitigation: Layer 3 calibration
catches systematic drift (if the judge is systematically wrong across the gold set, ρ drops
below the kill threshold). Explicit criteria injection (§4.2 — required, not optional per
arXiv:2604.27727) reduces free-form rationalization by anchoring verdicts to observable session
properties.

**Risk 2 — Reference traces thin for tail domains (MEDIUM severity)**
db_orm (n = 7 resolved), web_api (n = 3), testing_ci (n = 2). The p25 statistic is statistically
unstable: a single outlier session shifts the reference significantly. Mitigation: corpus-wide
p25 fallback with `reference_quality: LOW` flag. Resolution: Phase A.3 corpus expansion to
achieve n ≥ 30 per domain.

**Risk 3 — Calibration drift as Claude Code evolves (MEDIUM severity)**
A new Claude Code model version changes agent behavior patterns; p25 baselines and judge
calibration both become stale. Mitigation: all calibration artifacts are tied to a
model-version stamp in the `calibration_run_id`. Trigger re-calibration when production session
distributions shift detectably (e.g., median turn count changes by > 20%).

**Risk 4 — Testless sessions make judge the dominant signal (HIGH severity for enterprise)**
Free-form Claude Code sessions renormalize judge weight to 46%. If the judge is miscalibrated
for a domain or scaffold type not represented in the gold set, all testless scores for that
type are unreliable. Mitigation: `reliability: LOW` + `score_label: efficiency_estimate` flags
in output. Enterprise guidance: integrate even lightweight outcome signals (e.g., binary "PR
merged" or "user accepted") before trusting efficiency scores on testless sessions. This is a
fundamental limit of reference-based judging without verifiable outcome signals, not a fixable
implementation bug.

**Risk 5 — Local Qwen3-8B latency unacceptable at scale (LOW severity for prototype)**
20–30s/session on CPU is acceptable for batch processing 200 sessions; it is not viable for
real-time scoring of live sessions. Mitigation: GPU serving (3–5s/session) or migration to
the distilled 24B production path. This is a scaling concern, not a correctness concern.

**Risk 6 — Human gold set annotation is the critical path (MEDIUM severity)**
40 sessions × ~4 minutes = ~3 hours of consultant time. If annotation is delayed, the
calibration gate blocks all downstream work (distilled judge training, weight tuning, score
validation). Mitigation: pre-compute and cache all 40 session summaries before the annotation
session begins; minimize interface friction; schedule as a dedicated block.

**Risk 7 — Gold set domain generalization to real Claude Code sessions (MEDIUM severity)**
The 40 gold sessions are SWE-bench Python. Real enterprise Claude Code sessions have different
task distributions, vocabulary, and tool patterns. A judge calibrated on SWE-bench may
underperform on OOD sessions. Mitigation: `ood: true` flag on non-SWE-bench sessions; Phase A.3
corpus expansion to include real Claude Code sessions in the gold set.

### 9.2 Kill Criteria

**Primary kill criterion (judge architecture):**
Spearman ρ < 0.55 between Layer 2 judge scores and human gold ratings on the 40-session gold
set, after 3 prompt engineering iterations using the same judge model tier. If this threshold is
not cleared after escalating through Qwen3-8B → Qwen3-30B → GPT-4o-class judge:
- Narrow scope to specific waste categories only (redundant_read, duplicate_output), where
  deterministic signals exist, and drop holistic efficiency scoring.
- Escalate to PRM-based v2: a process reward model trained on our gold sessions that assigns
  per-turn waste scores. Cost: requires ~500 labeled turns (10× our current gold set) and
  a fine-tuning run. This is the contingent v2 path.

**Cross-family consistency kill criterion:**
If two different non-Claude judge models (e.g., Qwen3-8B and GPT-4o-mini) agree on fewer than
40% of 20 sample sessions (pairwise κ < 0.40), the task definition is too ambiguous for
automated judging regardless of model quality. Narrow scope before proceeding.

**LLM-label consistency floor:**
If judge vs LLM-label corpus Spearman ρ < 0.30, the judge is not tracking even the noisy
GPT-OSS signal on the 191-session set. Diagnose judge failure modes (e.g., SIMILAR verdict on
all sessions, criterion collapse) before continuing.

---

## 10. Self-Critique + Confidence Levels

| Claim | Confidence | Notes |
|---|---|---|
| H1/H3/H4 cannot be fixed by rubric rewriting | **HIGH** | Structural argument supported by A.1 kappas; confirmed by OOD zero-fire finding (report 03 §6) |
| Reference-based pointwise is better than pairwise for our use case | **MEDIUM** | Stability advantage (9% vs 35% flip, arXiv:2504.14716) is for general LLM output. Not validated specifically for coding-agent efficiency scoring. Requires empirical confirmation on our gold set. |
| Criterion injection raises α from ~0.60 to 0.908 | **HIGH** | Directly from arXiv:2506.13639 (HTML verified). The 0.908 is for GPT-4o on BIGGEN-Bench; may not replicate exactly on coding-domain sessions. |
| Qwen3-8B will reach ρ ≥ 0.55 | **LOW** | Untested assumption. At 7-8B parameters, Qwen3-8B is smaller than Llama-3.1-70B where α = 0.806 was measured. The kill criterion is designed precisely because I am not confident this tier clears the bar. |
| FLAMe 24B fine-tuned on 200 domain examples beats GPT-4o | **MEDIUM** | Directly from arXiv:2407.10817 for a general-domain judge. Domain-specific fine-tuning on ~200 examples may not replicate the full FLAMe result. The cost estimate ($50–200) is a rough extrapolation from public A100 pricing, not a measured number. |
| HERO hybrid pattern applies to scoring-time weighting | **MEDIUM** | HERO is a training-time architecture with stratified normalization. Applying it as fixed-weight scoring is an analogy, not a direct application. The empirical gains (+9.2pp) may not transfer to a fixed-weight context. |
| Anthropic "Hybrid Norm" terminology | **NOT CITABLE** | Does not appear in any Anthropic-authored primary source as of May 2026. Used HERO (Meta FAIR) throughout. Any Anthropic-specific claim would be speculative. |
| p25 token ratio is a valid counterfactual proxy | **MEDIUM** | The project's established wedge (reports 02-03) but not independently validated in external literature. Rests on face validity: a real achieved p25 run is a legitimate baseline. No external citation confirms this as a "counterfactual" in the formal sense. |
| Domain-level difficulty normalization (difficulty_norm) | **HIGH** within corpus | Empirically confirmed in report 03 (AUC = 0.50 for structural proxies; 7× resolve-rate variance by domain). Claim for OOD sessions: unknown; fallback to 2.0× is explicitly labeled as an assumption. |
| Cascade routing (9% calls → +1.9pp, coding +14.5pp) | **HIGH** | Directly from arXiv:2510.20369 on RewardBench and RM-Bench. Directly applicable to production routing pattern. |
| n = 40 human gold sessions is sufficient for calibration | **MEDIUM** | Gives ±0.15 CI on Spearman ρ at 95% confidence for ρ ≈ 0.75 (standard error formula). This is adequate for a go/no-go gate but thin for tuning weights precisely. n = 100 would be better; n = 40 is the trade-off against ~3 hours of annotation burden. |

---

## 11. Bibliography

New papers identified for this report:

1. Tripathi, T. et al. "Pairwise or Pointwise? Evaluating Feedback Protocols for Bias in
   LLM-Based Evaluation." arXiv:2504.14716 (2025). https://arxiv.org/abs/2504.14716

2. Xu, Y., Hirasawa, T., Kozuno, T., Ushiku, Y. "Am I More Pointwise or Pairwise? Revealing
   Position Bias in Rubric-Based LLM-as-a-Judge." arXiv:2602.02219 (2026).
   https://arxiv.org/abs/2602.02219

3. Yamauchi, Y., Yano, T., Oyamada, M. "An Empirical Study of LLM-as-a-Judge: How Design
   Choices Impact Evaluation Reliability." arXiv:2506.13639 (2025).
   https://arxiv.org/abs/2506.13639

4. Tao, L. et al. "Hybrid Reinforcement: When Reward Is Sparse, It's Better to Be Dense
   (HERO)." arXiv:2510.07242 (2025). FAIR Meta / UW-Madison.
   https://arxiv.org/abs/2510.07242

5. Jiang, Y. et al. "From Verifiable Dot to Reward Chain: Harnessing Verifiable
   Reference-based Rewards for Reinforcement Learning of Open-Ended Generation (RLVRR)."
   arXiv:2601.18533 (2026). ICLR 2026. https://arxiv.org/abs/2601.18533

6. Kim, S. et al. "Prometheus 2: An Open Source Language Model Specialized in Evaluating
   Other Language Models." arXiv:2405.01535 (2024). EMNLP 2024.
   https://arxiv.org/abs/2405.01535

7. Vu, T. et al. "Foundational Autoraters: Taming Large Language Models for Better Automatic
   Evaluation (FLAMe)." arXiv:2407.10817 (2024). Google DeepMind.
   https://arxiv.org/abs/2407.10817

8. Xu, Z. et al. "Ask a Strong LLM Judge When Your Reward Model Is Uncertain."
   arXiv:2510.20369 (2025). https://arxiv.org/abs/2510.20369

9. Han, S. et al. "Judge's Verdict: A Comprehensive Analysis of LLM Judge Capability
   Through Human Agreement." arXiv:2510.09738 (2025). NVIDIA.
   https://arxiv.org/abs/2510.09738

10. Shi, L. et al. "Judging the Judges: A Systematic Study of Position Bias in
    LLM-as-a-Judge." arXiv:2406.07791 (2024/2025). AACL-IJCNLP 2025.
    https://arxiv.org/abs/2406.07791

11. Amin, I. et al. "LLM-as-Judge for Code Quality in Human-AI Co-Creation."
    arXiv:2604.27727 (2026). https://arxiv.org/abs/2604.27727

Carried forward from reports 01–04:

12. Zheng, L. et al. "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena."
    arXiv:2306.05685 (2023). NeurIPS 2023. https://arxiv.org/abs/2306.05685

13. Gu, J. et al. "A Survey on LLM-as-a-Judge." arXiv:2411.15594 (2024).
    https://arxiv.org/abs/2411.15594

14. "Judging the Judges: A Systematic Evaluation of Bias Mitigation Strategies in
    LLM-as-a-Judge Pipelines." arXiv:2604.23178 (2026).
    https://arxiv.org/abs/2604.23178

15. Xiao, Y. et al. "AgentDiet: Improving the Efficiency of LLM-based Agents via Diet
    Strategy." arXiv:2509.23586 (2025). https://arxiv.org/abs/2509.23586

16. Jimenez, C. E. et al. "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?"
    arXiv:2310.06770 (2023). ICLR 2024. https://arxiv.org/abs/2310.06770

17. Yang, J. et al. "SWE-ABS: Adversarial Benchmark Strengthening." arXiv:2603.00520 (2026).
    https://arxiv.org/abs/2603.00520

18. "Don't Break the Cache: Cache Strategy for Long-Horizon Agents." arXiv:2601.06007 (2026).
    https://arxiv.org/abs/2601.06007
