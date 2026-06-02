# Project Spec: token-efficiency-scorer — Cross-Model Judge Corroboration (Iteration B3)

## Goal

Strengthen judge credibility WITHOUT humans by testing whether the trajectory-quality verdicts are robust across independent open models. Run a second, structurally-different, different-family open-weight model as a parallel judge on the existing 143-session pool, measure concordance with Qwen3-30B-A3B, investigate divergences, and report the result HONESTLY as corroboration — not validation.

The claim this phase can earn: "verdicts are corroborated across independent open models (agreement = X)." The claim it CANNOT earn: anything about human accuracy. Two LLMs agreeing is not ground truth — that was the founding lesson of this project (kappa=0.15-0.43 across LLMs is exactly why we pivoted). High cross-model agreement strengthens the position but does NOT close the accuracy gap, because two models can share blind spots and agree while both wrong. Report 09 must frame it precisely as corroboration.

## Current state

See CURRENT_STATE.md. B1 + B2 complete:
- Judge = Qwen3-30B-A3B, v3 prompt (task + domain + digest_text only, no Layer-1 scalars), api/chat, temp 0, seed 42, num_predict 6144, JSON schema. LOCKED.
- 143-session CC pool judge-scored, on GitHub (data/pool_judge_scores.jsonl), all ≤551 turns, 0 parse failures.
- Quality-gated baselines built from the strict-gate (MUCH_BETTER) sessions; two-axis scoped product (token-economy + trajectory quality).
- Judge credibility to date: rho=0.79 vs a reference LLM (Sonnet provisional), NOT human-accuracy-validated. No human gold set. No accuracy claims permitted.
- GPU path established (GCP L4 SPOT, ~$1-3/run, full guardrails).
- Cumulative Anthropic API ~$2.59 of $5; GCP credits separate.

## Scope

### In scope (this iteration)
- Select a SECOND judge model: open-weight, self-hostable, and from a genuinely DIFFERENT model family/lineage than Qwen (different training lineage = independent blind spots; same-lineage agreement is meaningless).
- Validate the second model emits schema-valid JSON on the v3 prompt before the full run (it will have a different inference profile than Qwen — e.g. no <think> mode).
- Score the existing 143-session pool with the second model, under STRICT input parity (identical v3 prompt + digest + schema; only the model differs).
- Determinism check: confirm Qwen at temp 0 is actually reproducible (re-score a few sessions, verify identical verdicts).
- Compute agreement: exact verdict match, adjacent (within-1) match, weighted Cohen's kappa, Spearman rho on numeric scores, per-verdict-level agreement.
- Gate-agreement analysis: of the strict-gate (MUCH_BETTER) sessions Qwen certified for the baseline, how many does the second model also rate MUCH_BETTER (or top-2)? This tests whether the BASELINE is Qwen-specific or model-robust.
- Divergence investigation: where the two models disagree, read BOTH reasonings — is it one model catching real behavior the other missed, a genuinely ambiguous session, or one model wrong?
- research/09-cross-model.md, framed as corroboration, with the human-accuracy gap restated as still-open.

### Out of scope (do not build)
- Human gold set / any human-in-the-loop (explicit user decision — LLM-only this phase).
- Replacing Qwen as the production judge (Qwen stays primary; the second model is a corroboration instrument, not a replacement).
- Re-running baselines (unless gate-agreement analysis reveals the baseline is materially model-specific — then we surface it, don't silently rebuild).
- Changing the v3 prompt or digest schema.
- Packaging / distribution (next phase).
- Scoring the >600-turn overflow sessions (same ceiling as B2).

## Tech stack
- Ollama on GPU for the second open model (same path as Qwen).
- Same v3 prompt + JSON schema as the locked judge.
- scipy/numpy/sklearn for kappa, Spearman, agreement stats.
- GCP L4 SPOT (or larger if the second model needs more VRAM — surface the requirement before provisioning).
- No paid API. Reports immutable.

## Architecture (new files only)
```
scripts/
├── second_judge_run.py        # NEW - score pool with model #2, parity-enforced
└── judge_agreement.py         # NEW - all concordance metrics + divergence dump

data/
├── pool_judge_scores_m2.jsonl # NEW - second model's verdicts
├── judge_agreement.json       # NEW - computed agreement metrics
└── cost-log.jsonl             # APPEND ONLY

research/
└── 09-cross-model.md          # NEW
```

## Key design decisions (resolve early, escalate)
1. SECOND MODEL CHOICE: must be (a) open-weight + self-hostable, (b) genuinely different family/lineage than Qwen3 (Alibaba) — e.g. Gemma (Google), Llama (Meta), Mistral (Mistral), not another Qwen derivative or a Qwen-distilled model, (c) fit available GPU (a 70B needs quantization or a bigger instance than L4 — surface the VRAM/cost tradeoff). Dense-vs-MoE difference is a BONUS for independence. CC proposes 2-3 candidates with family + hardware-fit + independence rationale; HOLD for confirmation.
2. INPUT PARITY: identical v3 prompt + digest + JSON schema. The /no_think token is a Qwen-ism — inert to other models, leave it for exact parity. Inference params (num_predict) MAY differ ONLY to the extent needed for the model to emit valid JSON (non-Qwen models lack the runaway think-chain, likely need LESS budget) — but document any difference and confirm it doesn't change what the model SEES. The ONLY substantive variable is the model.
3. AGREEMENT METRIC: weighted Cohen's kappa is the headline (accounts for chance agreement on a 5-level ordinal scale; we used kappa earlier so it's consistent). Report exact-match %, adjacent %, Spearman, and per-level too. State the kappa interpretation honestly (e.g. >0.6 substantial, >0.8 near-perfect).
4. DIVERGENCE READ: for the disagreements, the reasonings determine whether agreement reflects shared real signal vs the divergences reflect a real boundary. Cannot detect SHARED blind spots from agreement alone — state that limitation explicitly.
5. GATE ROBUSTNESS: the baseline depends on which sessions are MUCH_BETTER. Report the overlap between Qwen's MUCH_BETTER set and model-2's. High overlap = baseline is model-robust (credibility win). Low overlap = baseline is Qwen-specific (a real finding for report 08/09).

## Verification commands
```yaml
- name: parity-check
  cmd: python scripts/second_judge_run.py --verify-parity
  required: true
- name: agreement-integrity
  cmd: python -c "import json; a=json.load(open('data/judge_agreement.json')); assert 'weighted_kappa' in a and 'n' in a; print('ok', a['n'])"
  required: true
- name: cost-check
  cmd: python -c "import json; t=sum(json.loads(l).get('cost_estimate_usd',0) for l in open('data/cost-log.jsonl')); print(f'${t:.2f}'); assert t<5"
  required: true
```

## Escalation rules (orchestrator must ask before doing)
- BEFORE provisioning GPU: report the second model choice, its VRAM need, the instance type required (if >L4), and the estimated cost. Confirm.
- BEFORE the full run: validate the second model emits schema-valid JSON on 3-5 sessions; report its failure mode (if any) and how params were set to fit JSON output. If it can't reliably emit valid JSON on the v3 prompt, STOP and report — we pick a different model rather than hack the prompt (which breaks parity).
- If gate-overlap is LOW (the two models disagree substantially on which sessions are MUCH_BETTER): surface it before writing report 09 — it affects what report 08's baselines can claim.
- BEFORE any paid API call / dep not in stack / modifying reports 01-08.

## Hard rules
- Qwen + v3 prompt + digest schema stay LOCKED and primary. Model-2 is a corroboration instrument only.
- Second model MUST be different-family/lineage from Qwen, or the agreement is meaningless.
- Input parity is sacred: same prompt, same digest, same schema. Document any inference-param difference and justify it doesn't change model input.
- Report 09 frames the result as CORROBORATION, never VALIDATION. The human-accuracy gap stays open and restated. High agreement does NOT permit accuracy claims.
- GPU: SPOT, trapped auto-shutdown, hard max-runtime, persistent-disk JSONL, distinctive temp name, budget alert, teardown + actual spend. Provision immediately before run. Never touch aetherart-eval-001 / review-iq-prod.
- Reports 01-08 immutable. .env in-process only. Cost log append-only. Secrets masked at ingestion (layer already built).

## Budget
- Soft: 1-3 CC sessions.
- Anthropic API: $5 cap, escalate at $4 (judge is local, near-zero added).
- GCP: separate; report actual spend; budget alert $10. A larger-than-L4 instance (if a 70B is chosen) costs more — surface before provisioning.

## Success criteria (verify ALL before done)
- Second model selected with documented independence rationale + hardware fit.
- Parity verified: identical prompt/digest/schema; any param difference documented + justified.
- Second model emits schema-valid JSON; failure rate reported.
- Pool scored with model-2 (same ≤600-turn scope as Qwen pool); count reported.
- Determinism check on Qwen (temp 0 reproducibility) reported.
- Agreement metrics computed: weighted kappa (headline), exact %, adjacent %, Spearman, per-level. All reported with honest interpretation.
- Gate-overlap analysis: Qwen-MUCH_BETTER vs model-2 overlap reported; implication for baseline credibility stated.
- Divergence investigation: disagreements read out with both reasonings; characterized.
- research/09-cross-model.md written: corroboration framing, all metrics, gate-overlap finding, divergence analysis, and an explicit "this does NOT close the human-accuracy gap; no accuracy claims" section.
- GPU torn down, zero lingering resources, actual spend reported.
- Reports 01-08 unmodified. Git clean.

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md + reports 06/07/08 + spec.md.
2. Propose 2-3 second-model candidates (family, independence, VRAM, GPU/cost). HOLD for confirmation.
3. Validate chosen model emits schema-valid JSON on 3-5 sessions (local or short GPU). Report failure mode + param settings. HOLD if it can't.
4. Determinism check on Qwen (re-score ~5 sessions temp 0, confirm identical).
5. Provision GPU (sized for model-2), score the pool under parity, retrieve -> verify -> commit -> push -> re-read -> teardown. Report count + spend.
6. Compute agreement metrics (judge_agreement.py): weighted kappa, exact/adjacent, Spearman, per-level.
7. Gate-overlap analysis (Qwen-MUCH_BETTER vs model-2).
8. Divergence investigation (read both reasonings on disagreements).
9. Write research/09-cross-model.md (corroboration framing). HOLD for consultant read before any baseline-revision discussion.
