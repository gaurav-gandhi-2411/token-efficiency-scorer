# Report 09 — Cross-Model Judge Corroboration: Positive Verdicts Corroborated, Waste Detection Model-Dependent

**Author:** Gaurav Gandhi
**Date:** 2026-06-03
**Status:** FINAL

---

## 0. What This Report Establishes — and What It Does Not

This report runs a second independent judge model (Gemma 3 27B) over the same 143-session pool Qwen scored in B2. The results are asymmetric in a way that matters.

**What is established:**

- Positive verdicts (MUCH_BETTER) are cross-model corroborated. Gemma agrees with Qwen on MUCH_BETTER sessions at an 84.0% strict match rate and 96.0% top-2 rate. The MUCH_BETTER reference population used for B2 baselines is model-robust.

- Waste and negative verdicts (WORSE/MUCH_WORSE) are NOT corroborated. Gemma 3 27B, a competent independent model from a different architecture family and training source, reverses 94.4% (17/18) of Qwen's waste verdicts. The single session both models rate negatively is the only overlap.

- Waste detection is model-dependent. The product's ability to identify inefficient sessions — its core "catch inefficiency" function — is not validated across models.

- "Qwen is harsher" is not "Qwen is right." The disagreement is a rubric-level difference in how the two models interpret repeated failures. It does not establish which model is calibrated to human judgment.

- This does NOT close the human-accuracy gap from report 06. rho=0.79 against the 5-session human-rated set plus cross-model agreement on positives means positive verdicts are corroborated, not accuracy-validated. The two things are distinct.

- Reports 01-08 remain immutable. No retroactive changes to prior findings.

---

## 1. Methodology: Second Judge Setup

**Model choice rationale.** Gemma 3 27B (Google DeepMind) was selected specifically because it is architecturally and organizationally independent from Qwen 2.5 72B (Alibaba). Qwen is a Mixture-of-Experts-adjacent dense model trained on Alibaba's data mixture; Gemma 3 27B is a dense transformer trained on Google's data pipeline. Different architecture families and different training organizations mean different blind spots. A second model from the same vendor or the same architecture family would provide weaker independence evidence.

**Input parity.** Identical v3 prompt and digest schema. The `/no_think` directive was preserved; it is inert on Gemma (Gemma has no chain-of-thought suppression mechanism — the token passes through as literal text without effect). Only two parameters differ between the runs: model name, and num_predict (Qwen=6144, Gemma=2048). The num_predict difference affects generation budget only — it does not change what the model sees. Temperature=0, seed=42, num_ctx=32768 are identical. Both runs use Ollama constrained decoding with the same JSON schema, ensuring output structure parity.

**Pool scope.** 143 sessions, the same ≤551-turn pool used for the Qwen B2 baseline run.

**Hardware.** GCP VM: tes-b3-gemma-run-tmp, g2-standard-8 SPOT instance, asia-east1-a. Runtime approximately 2 hours 55 minutes (including validation run plus the full 143-session scoring run). Estimated cost: ~$1.63 from GCP credits. Cumulative Anthropic API spend: ~$2.59 (unchanged — the judge is local Ollama; no Anthropic API calls were made for this run).

---

## 2. Scoring Results

141 of 143 sessions scored successfully. Two parse failures occurred:

- fb97cea8 (519 turns): "Unterminated string" — num_predict=2048 budget exhausted mid-JSON on a dense context
- a37480e7 (524 turns): same failure mode

Both failures are context-density-sensitive, not turn-count-threshold failures. The 551-turn validation session scored successfully, which rules out a simple turn-count cutoff. The failure mechanism is that Gemma's output on dense digests requires more tokens to complete the JSON response than the 2048-token generation budget allows. Sessions with equivalent turn counts but less dense digests complete successfully.

All matched-pair analysis uses the 141 sessions where both Qwen and Gemma produced parseable outputs.

**Verdict distribution (141 matched sessions):**

| Verdict | Qwen | Gemma | Δ |
|---|---|---|---|
| MUCH_BETTER | 75 | 94 | +19 |
| BETTER | 45 | 41 | −4 |
| SIMILAR | 3 | 3 | 0 |
| WORSE | 15 | 3 | −12 |
| MUCH_WORSE | 3 | 0 | −3 |

The distribution shift is directional: Gemma rates more sessions MUCH_BETTER and rates almost none WORSE or MUCH_WORSE. Gemma assigns zero MUCH_WORSE verdicts across 141 sessions.

---

## 3. Agreement Metrics

**Exact match:** 58.2% of 141 sessions receive the same verdict from both models.

**Adjacent match (within 1 ordinal step):** 85.1% — the majority of disagreements are one-step differences, not gross misalignments.

**Weighted Cohen's kappa:** 0.3083. By convention this falls in the "fair" agreement band (0.20–0.40). For context, Phase A.1 (report 04) produced LLM-vs-LLM kappa in the 0.15–0.43 range across provider pairs, which contributed to the decision to pivot from raw LLM scoring to the structured rubric approach. The current kappa of 0.31 is within that same band. Two models using the same structured rubric produce no better inter-rater agreement than two models using unstructured prompts did in Phase A.1. This is not a sign that the rubric has failed — it shows that the rubric constrains output structure but cannot eliminate architectural differences in how failure patterns are interpreted.

**Spearman rho on judge_score:** 0.4254 (p≈0). A moderate positive correlation. The models agree on the broad direction of quality ranking but diverge significantly on the magnitude of negative signals.

---

## 4. The Asymmetric Finding: Positive Gate vs Negative Gate

This is the central result of the report. The agreement between Qwen and Gemma is not uniform across the verdict range — it is strongly asymmetric.

**Gate overlap numbers:**

| Gate | Qwen baseline | Gemma agreement | Rate |
|---|---|---|---|
| Positive: Qwen MUCH_BETTER → Gemma strict MUCH_BETTER | 75 sessions | 63/75 | 84.0% |
| Positive: Qwen MUCH_BETTER → Gemma top-2 (MUCH_BETTER or BETTER) | 75 sessions | 72/75 | 96.0% |
| Negative: Qwen WORSE/MUCH_WORSE → Gemma also WORSE/MUCH_WORSE | 18 sessions | 1/18 | 5.6% |

The asymmetry is stark. For positive verdicts, both models agree at 84–96% depending on how strictly agreement is defined. For negative verdicts, agreement collapses to 5.6%.

**What the positive gate means for B2 baselines.** The MUCH_BETTER sessions used to establish the B2 token efficiency baselines are model-robust. When Qwen rates a session MUCH_BETTER, an independent model with a different architecture and training source agrees 84.0% of the time at strict match and 96.0% of the time within one ordinal step. The B2 reference population is corroborated.

**What the negative gate means for waste detection.** When Qwen rates a session WORSE or MUCH_WORSE, Gemma disagrees 94.4% (17/18) of the time — and does not merely assign SIMILAR, but assigns BETTER or MUCH_BETTER. Gemma does not rate these sessions as neutral; it rates them positively. The waste detection signal is not corroborated.

**Directional analysis.** Across 59 disagreement sessions (41.8% of the matched pool), the mean directional difference is +0.881 on the ordinal scale (positive = Gemma more lenient). Gemma is more lenient in 76.3% of disagreements; Qwen is more lenient in 23.7%. The leniency asymmetry is consistent throughout the disagreement set, not driven by a small number of outliers.

**Qwen-negative slice.** The 18 sessions Qwen rates WORSE or MUCH_WORSE show a mean directional difference of +2.471 steps — Gemma rates them nearly 2.5 ordinal positions higher on average. This is not noise. Both models are processing the same digest with the same rubric and reaching systematically different conclusions about what constitutes a wasteful session.

---

## 5. Diagnostic Cuts

### 5.1 Waste category pattern (Cut 1)

To understand the mechanism behind the 17/18 reversals, the 16 sessions where Qwen cites specific waste categories AND rates WORSE/MUCH_WORSE AND Gemma rates BETTER/MUCH_BETTER were examined by their primary waste signal.

- 12/16 (75%) have `failed_retry` as a component
- 2/16 are empty-turn sessions (12 turns each) cited as `redundant_read`
- 2/16 are `redundant_read` or `trajectory_drift` dominant with no `failed_retry`

The leniency is NOT uniform across waste categories — it is concentrated on the `failed_retry` signal. Gemma's rubric reads repeated command failures as "exploration" or "minor redundancy." Qwen reads them as failure loops without approach adjustment, a distinct waste pattern. Both interpretations are defensible from the rubric text. Neither is provably correct without knowing what a human rater would conclude.

The 2 empty-turn sessions (12 turns, 6/12 non-advancing turns) represent a separate disagreement: Gemma credits short conversational or planning turns that Qwen classifies as empty. This is a different calibration gap from the `failed_retry` pattern.

### 5.2 Size correlation (Cut 2)

One potential confound is session size. If Gemma's leniency were driven by long sessions masking early waste in dense contexts, the directional difference would increase with session length.

Among the 16 waste disagreement sessions:
- Sessions <150 turns (n=10): mean directional difference = +2.40
- Sessions ≥150 turns (n=6): mean directional difference = +2.50

Size is NOT the driver. The leniency is uniform across session lengths. The mechanism operates at the rubric level — how each model interprets repeated failures — not at the context-compression level. Long sessions do not hide waste from Qwen that short sessions reveal; the disagreement exists at all sizes at essentially the same magnitude.

### 5.3 Parse failures (Cut 3)

Two of 143 sessions (1.4%) produced "Unterminated string" parse failures. Both are near 520 turns with dense digests. The 551-turn validation session completed successfully.

This establishes that num_predict=2048 is context-density-sensitive rather than turn-count-limited. Dense digests at ~520 turns require more generation budget to complete the JSON response than 2048 tokens allows. At 6144 tokens (Qwen's budget), no parse failures occurred in the same pool. Future Gemma runs on dense sessions should use num_predict≥4096 or dynamically set based on digest character count.

---

## 6. Waste Disagreement Cases

The 16 sessions where Qwen cites waste categories AND rates WORSE/MUCH_WORSE AND Gemma rates BETTER/MUCH_BETTER:

| Session ID | Turns | Qwen verdict | Gemma verdict | Primary waste pattern |
|---|---|---|---|---|
| 5e416ec7 | 12 | MUCH_WORSE | BETTER | empty turns (6/12 non-advancing) |
| e0e44c19 | 12 | WORSE | BETTER | empty turns (6/12 non-advancing) |
| 9922d849 | 17 | WORSE | MUCH_BETTER | trajectory_drift + redundant_read |
| b323ba3e | 35 | WORSE | MUCH_BETTER | repeated doc reads without progress (redundant_read) |
| 201e333b | 24 | WORSE | BETTER | failed_retry + redundant_read |
| 342add8e | 41 | WORSE | BETTER | failed_retry loop (T3–T6) + context_bloat |
| 4b5a4cbe | 44 | WORSE | BETTER | failed_retry on /filter without strategy change (T13–T30) |
| f2f8cee5 | 50 | WORSE | BETTER | failed_retry after Edit failure (T21), redundant reads |
| 5cd1530e | 89 | WORSE | BETTER | failed_retry + redundant_read (T57, T64, T66) |
| 3a0402a5 | 104 | MUCH_WORSE | BETTER | failed_retry on dependency error (T33, T75, T85, T94) |
| 79fcd9ad | 152 | WORSE | BETTER | repeated Bash failures (T3, T8, T10, T27, T41, T81, T86, T142) |
| abaeb8b0 | 211 | WORSE | BETTER | failed pytest (T90–T95) + redundant_read |
| 5da59be9 | 214 | WORSE | BETTER | failed Bash (T53, T57) + redundant reads (T128–T133) |
| a1e1e20e | 357 | WORSE | MUCH_BETTER | repeated file reads (T176, T178, T180) + trajectory_drift |
| 84a95c8e | 426 | WORSE | MUCH_BETTER | repeated redundant reads + failed builds without root cause fix |
| 78bd2719 | 470 | WORSE | MUCH_BETTER | WSL2 failure loop (T4–T19) + all 5 waste categories |

The pattern across the table is consistent: Qwen reads repeated failures across multiple turns as a waste loop; Gemma reads the same sequence as iterative problem-solving or minor redundancy. The longest session (78bd2719, 470 turns) triggers all five waste categories in Qwen's rubric and still receives MUCH_BETTER from Gemma.

---

## 7. What This Means for the Project

**What is corroborated.** The MUCH_BETTER reference population used to establish B2 baselines is model-robust. 84.0% of Qwen's MUCH_BETTER verdicts are confirmed by an independent model at strict match; 96.0% are confirmed within one ordinal step. When the product identifies a session as highly efficient, that identification holds across model families. The B2 baselines — token counts and efficiency metrics derived from MUCH_BETTER sessions — rest on a corroborated foundation.

**What is not corroborated.** The waste detection signal is not corroborated. WORSE and MUCH_WORSE verdicts from Qwen do not hold across models. Gemma 3 27B, using the same rubric on the same digests, rates 94.4% (17/18) of Qwen's negative sessions positively. The single session both models rate negatively is the only shared waste signal.

**The core implication.** Two independent LLMs from different architecture families, different training organizations, and different data pipelines agree on "this session is good" at 84–96% rates. The same two models agree on "this session is wasteful" at 5.6%. This asymmetry is not a scoring artifact — it reflects a genuine rubric interpretation gap concentrated on how repeated command failures should be classified: as failure loops (Qwen) or as iterative exploration (Gemma).

**The unresolvable part.** The current state of evidence cannot determine which model is calibrated to human judgment. A third model that agrees with Qwen on waste would still not constitute ground truth — it would mean two LLMs share Qwen's interpretation, not that the interpretation is correct. The question "is this session wasteful" requires a human rater who has reviewed the session content and applied their own judgment. That ground truth does not exist.

**What would close this.** A human gold set of sessions spanning the verdict range, specifically including sessions Qwen rates WORSE/MUCH_WORSE. Human raters reviewing those sessions — and indicating whether they consider the repeated failures in those sessions to constitute genuine waste or acceptable iteration — would provide the human ground truth needed to test whether Qwen's waste signal is accurately calibrated or systematically overcalling. Without that data, "Qwen catches waste that Gemma misses" and "Qwen overcalls waste that Gemma correctly forgives" are equally supported by the current evidence.

---

## 8. Cumulative Limitations

The following limitations carry forward from reports 06-08 without modification:

**From report 06:**
- Human accuracy gap: rho=0.79 on 5 human-rated sessions. 5 sessions is not a sufficient sample for accuracy claims. The correlation result establishes rough rank-order alignment; it does not establish that the judge's verdicts are correct.
- The 5 human-rated sessions are not uniformly distributed across the verdict range.
- Self-consistency (same prompt, same model, multiple runs) was not measured.

**From report 07:**
- Ingestion pipeline processes Claude Code session JSON. Sessions from other LLM tools are not covered by the digest schema.
- Digest truncation at the turn-count limit may compress sessions differently depending on turn density.

**From report 08:**
- B2 baselines are derived from a single judge model's verdicts on a single pool. Generalization to different task domains is unknown.
- The ≤551-turn scope excludes very long sessions from the baseline population.
- Baseline stability under pool expansion has not been tested.

**New limitations from this report:**

- **Waste detection is model-dependent.** WORSE/MUCH_WORSE verdicts from Qwen are not confirmed by an independent model. The product's ability to identify inefficient sessions is currently calibrated to one model's rubric interpretation and has not been confirmed against human judgment.

- **num_predict=2048 is context-density-sensitive.** Gemma 3 27B with a 2048-token generation budget produces parse failures at ~520 turns on dense digests. The failure is density-sensitive, not turn-count-limited (551-turn sessions with lower density complete successfully). Future Gemma runs require a higher generation budget for dense sessions.

- **The human-accuracy gap remains open.** Cross-model agreement on positive verdicts is corroborated evidence, not accuracy-validated evidence. rho=0.79 + positive gate agreement does not substitute for a human gold set spanning the verdict range.
