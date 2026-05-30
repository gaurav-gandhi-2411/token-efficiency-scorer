# Report 06 — B1-Revised Calibration: Dual-Target Design

**Author:** Gaurav Gandhi
**Date:** 2026-05-30
**Status:** ACTIVE — B1-revised iteration; human gold deferred; calibration scaffold in place

---

## 1. Status and Scope

This report documents the calibration design for the token-efficiency scorer, iteration B1-revised.
Human gold collection has been deferred from this phase. What this report covers:

- **Target A:** Deterministic objective efficiency proxy computed from Layer 1 features alone
  (no LLM calls). Serves as the primary calibration anchor this phase.
- **Target B:** LLM provisional rating via Anthropic claude-sonnet-4-6 Batch API, using the same
  digest text and rubric wording that a human rater would see.
- **Calibration:** Three Spearman ρ values across the 191 annotated sessions. Kill criterion is
  headline ρ ≥ 0.55 (judge vs objective proxy).

Human gold is explicitly deferred, not accidentally omitted. See Section 2 for the decision record.

---

## 2. Decision: Deferred Human Gold

The original B1 plan called for a 40-session human gold set using `scripts/rating_interface.py`.
The decision was made to defer human collection because trajectory efficiency is hard to rate
reliably by hand: the Phase A.1 IAA results (κ 0.15–0.43 across H1/H3/H4) demonstrate that even
concept-aware annotators produce low agreement on efficiency-adjacent signals when tasked to
rate per-turn behavior. Extending this to full-session efficiency ratings without clear rubric
anchoring risks producing gold that is less reliable than the objective proxy.

The accepted limitation is explicit: this phase has no human ground truth. The objective proxy
(Token A) is a principled substitute anchored on task outcomes and corpus-relative token spend.
This is not an oversight — it is the documented plan for the B1-revised iteration. Human gold
collection is retained as the production calibration target (see Section 8).

---

## 3. Why LLM Rating Alone Cannot Be Ground Truth

The Phase A.1 IAA study (report 04) ran GPT-OSS-120b and claude-sonnet-4-6 against the same
efficiency-adjacent rubrics on 25 overlapping sessions. Cohen's κ across H1/H3/H4 was 0.15, 0.43,
and 0.19 — all below the 0.60 threshold for acceptable agreement. On H4, Sonnet systematically
over-fired on 72 of 310 turns (23%) relative to GPT-OSS, despite identical rubric text. This is
not random noise: it is structured model-family rubric drift that does not average out across more
samples. A single LLM rating (even at scale) cannot serve as ground truth because there is no
external anchor to distinguish genuine efficiency signal from the model's idiosyncratic
interpretation of "efficient." The dual-target design addresses this by requiring the LLM judge
to correlate with an objective, task-outcome-based signal before its scores are trusted.

---

## 4. Dual-Target Design

### Target A — Objective Efficiency Proxy (formula)

Computed by `scripts/objective_proxy.py` from Layer 1 features only. No API calls.

```
objective_efficiency_proxy = 0.25 * resolved_score
                           + 0.50 * (1 - percentile_rank(p25_token_ratio))
                           + 0.25 * (1 - percentile_rank(turn_ratio))

where:
  resolved_score = 1.0 if test_outcome else 0.0
  p25_token_ratio = total_tokens / domain_p25_baseline  (clamped [0.1, 100.0])
  turn_ratio      = turn_count / domain_median_turns    (clamped [0.1, 10.0])
  percentile_rank = average-method scipy.stats.rankdata / N, within 191 annotated sessions
```

All ranks are computed within the 191 annotated sessions. Sessions with lower token ratio
and lower turn ratio (more efficient) receive higher proxy scores. Resolved sessions receive
a 0.25 bonus regardless of efficiency. The proxy is an imperfect ground truth: a session that
resolved by thrashing will still receive the resolution bonus, which is why the LLM judge is
needed to discriminate trajectory quality.

### Target B — LLM Provisional Rating

Computed by `scripts/llm_rater.py` using claude-sonnet-4-6 via Anthropic Batch API. The model
sees the same digest text and rubric wording that a human rater would see in
`scripts/rating_interface.py` (including the exact `_RATING_RUBRIC` text). The session content
is rendered via `digest_to_text(digest, show_stats=False)` — identical to the Layer 2 judge view.
Output field `source: "llm_provisional"` distinguishes these ratings from human gold.

### Why Both Are Needed

Target A encodes objective outcomes (resolution, token efficiency, turn economy) but cannot
capture trajectory quality — a session that resolves with many redundant steps is scored as
efficient by the proxy. Target B captures trajectory patterns but, as documented in Section 3,
cannot be trusted without an external anchor. The two targets are complementary: high ρ(judge,
proxy) means the judge is tracking objective reality; the LLM provisional provides a secondary
cross-check that reveals whether a subjective rating agrees with objective facts.

---

## 5. Calibration Protocol

Three Spearman ρ values are computed by `scripts/calibration.py`:

| # | Pair | Role |
|---|------|------|
| 1 | judge_score vs objective_efficiency_proxy | **HEADLINE** — primary anchor this phase |
| 2 | judge_score vs llm_provisional/5 | Secondary — model-vs-model consistency |
| 3 | llm_provisional/5 vs objective_efficiency_proxy | Sanity — does LLM track objective reality? |

Each ρ is reported with a 95% bootstrap CI (n=2000 resamples, seed=42). The kill criterion is
**headline ρ ≥ 0.55** (judge vs proxy). If the Qwen3-8B judge does not achieve this threshold
after three prompt iterations, the prototype judge is replaced (next candidate: Llama 3.1 8B or
Mistral 7B Instruct). All ρ values are also broken out by scaffold (swe_agent vs openhands)
because the two scaffolds have structurally different output-token availability (see Section 6).

---

## 6. Scaffold Confound

Phase A.1 (report 04, §3) confirmed that openhands scaffolds record per-turn output tokens while
swe_agent sessions have zero output tokens (`tokens_output = 0` in taxonomy). This affects the
p25_token_ratio (swe_agent sessions use only input tokens in their total) and the digest view
shown to raters (`output_tokens_available: False` is printed). A judge trained on openhands
patterns may not generalize to swe_agent sessions and vice versa.

For this reason:
- All three calibration correlations are split by scaffold group (swe_agent vs openhands_*).
- The `efficiency_score` output in `score.py` preserves the scaffold field for downstream
  per-scaffold stratification.
- The kill criterion is applied at the overall level, but per-scaffold ρ below 0.45 triggers
  a separate escalation.

---

## 7. [PLACEHOLDER] Calibration Results

*Populated after Layer 2 judge run completes.*

| Correlation | Overall ρ | 95% CI | swe_agent ρ | openhands ρ |
|-------------|-----------|--------|-------------|-------------|
| Judge vs proxy (HEADLINE) | TBD | [TBD, TBD] | TBD | TBD |
| Judge vs LLM provisional | TBD | [TBD, TBD] | TBD | TBD |
| LLM provisional vs proxy | TBD | [TBD, TBD] | TBD | TBD |

Kill criterion (headline ρ ≥ 0.55): **TBD**

---

## 8. What Changes When Human Gold Arrives

When `data/gold/human_ratings.jsonl` is populated (40 sessions, rated via
`scripts/rating_interface.py`), the calibration pipeline requires no code changes:

```bash
python scripts/calibration.py --human-ratings data/gold/human_ratings.jsonl
```

`calibration.py` already accepts `--human-ratings` and will compute additional correlations:
- judge_score vs human_gold
- objective_proxy vs human_gold
- llm_provisional vs human_gold

The **new headline** becomes **judge vs human_gold** (replacing judge vs proxy). The production
target is **ρ ≥ 0.75 vs human gold** over the 40-session sample. The proxy-based kill criterion
(ρ ≥ 0.55) is a preliminary gate only; it is not sufficient for production promotion.

---

## 9. Files Produced This Phase

| File | Description |
|------|-------------|
| `scripts/objective_proxy.py` | Target A: deterministic proxy from Layer 1 features; writes `data/objective_proxy.jsonl` and `config/p25_refs.yaml` |
| `scripts/llm_rater.py` | Target B: claude-sonnet-4-6 provisional rater via Anthropic Batch API; writes `data/llm_provisional_ratings.jsonl` |
| `scripts/layer2_judge.py` | Qwen3-8B/Ollama reference-based pointwise judge; writes `data/judge_scores.jsonl` |
| `scripts/score.py` | Compose final `efficiency_score` from Layer 1 + judge scores; writes `data/efficiency_scores.jsonl` |
| `scripts/calibration.py` | Three Spearman ρ calibration values with 95% bootstrap CI; writes `data/calibration/calibration_{datestamp}.json` |
| `research/06-calibration.md` | This document: calibration design, decision record, and placeholder results table |
