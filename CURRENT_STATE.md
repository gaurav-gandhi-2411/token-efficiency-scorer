# CURRENT_STATE.md — token-efficiency-scorer

Snapshot as of 2026-06-02. Read this BEFORE planning. This supersedes all prior
CURRENT_STATE.md snapshots (the last one dated 2026-05-30).

---

## Iteration status: B1 CLOSED

The B1 prototype is complete. The three-layer hybrid scorer is built and the judge has been
validated as a coherent instrument. Human gold calibration is the remaining gate before any
accuracy claims can be made; it is explicitly deferred to the next product phase.

Do NOT extend this iteration. If you are planning new work, read NEXT_PHASE.md first.

---

## What was built and proved

**Layer 1 — Deterministic features (complete, production-ready):**
7 scalar features (test_outcome, total_tokens, turn_count, h2_duplicate_count, cache_hit_rate,
p25_token_ratio, domain_id) plus a deterministic structured trace digest. Computes for all
191 corpus sessions. Scripts: `scripts/layer1_features.py`, `src/token_efficiency/trace_digest.py`.

**Layer 2 — LLM judge (prototype-ready, not production-hardened):**
Reference-based pointwise judge using qwen3:30b-a3b via Ollama. Prompt v3: trajectory
purposefulness only (C1-C4 criteria, fixed order, `/no_think` prefix, no token-efficiency
framing in the rubric). 67 of 69 calibration sessions scored on GCP L4 GPU.

**Layer 3 — Calibration (instrument validation complete, accuracy validation deferred):**
Spearman rho computed across four cuts with bootstrapped 95% CIs. Honest result: the judge
agrees with a Sonnet reference rater at rho = 0.79 (cluster-excluded). That is an
instrument-coherence result, not an accuracy result. No human ground truth was collected this
iteration. See research/06-calibration.md for the full calibration report and explicit claim
boundaries.

**Score formula (weights PROVISIONAL, not tuned):**
```
efficiency        = composite_quality / (p25_token_ratio × difficulty_norm)
composite_quality = 0.50 × outcome_score + 0.35 × judge_score + 0.15 × h2_score
difficulty_norm   = 1 / domain_resolve_rate
```

---

## Validated findings (load-bearing for next phase decisions)

**H2 survives as a deterministic feature.** Phase A.1 kappa = 0.825. Judge_score vs H2
rho = -0.40: the judge independently penalizes high-duplicate sessions, consistent with H2
direction. Non-circular: the judge was not given H2 values.

**Scaffold split is real, not style bias.** openhands_nebius (mean 116 turns, 59% resolve)
scores MUCH_BETTER at 0.926 mean. openhands_swegym (mean 23 turns, 20% resolve) scores near
floor at 0.150. The behavioral stats confirm structural difference, not judge bias. Reasonings
cite specific turn numbers and failure modes in both directions.

**p25 inversion is a corpus artifact, not judge miscalibration.** Lean (<1.0 p25_ratio)
sessions score worse than wasteful ones on average because the lean group is dominated by
swegym quick-fail sessions (empty loops, gave up fast). Within lean, the judge correctly
distinguishes lean-efficient from lean-failed. No miscalibration.

**Resolved collinearity is moderate, not blocking.** Point-biserial r = 0.50. Off-diagonal
cells confirm independence (7 unresolved MUCH_BETTER; 5 resolved WORSE/MUCH_WORSE). Acceptable
for this iteration; documented for weight-tuning context.

---

## Known limitations (all carried forward, do not paper over)

**No human ground truth.** The rho = 0.75 target from report 05 was defined against a 40-session
human gold set that was never collected. Every calibration result in report 06 is LLM-vs-LLM or
judge-vs-deterministic-proxy. The judge may not match what a human expert would assign. No
"calibrated to human experts" or "human-validated" claims are permitted until the gold set exists.

**Swegym empty-loop cluster dominates the negative tail.** 14 of 67 scored sessions (20.9%)
are near-identical 7-11 turn openhands_swegym sessions that fail via empty/single-token loops.
These represent 54% of all MUCH_WORSE verdicts. The cluster makes floor-detection easy;
calibration numbers that include it overstate judge discrimination difficulty. Any future
calibration should report cluster-excluded rho as the headline.

**Corpus is 100% Python, SWE-bench-shaped, offline scaffolds.** No real Claude Code traces,
no Aider, no multi-language, no interactive sessions. Generalization is unknown. Do not expand
the corpus in this phase without explicit authorization.

**2-session scoring gap.** e1b043ff429ed5a2 and 9dd32933ac04fd31 are in layer1_outputs.jsonl
but were not scored (Ollama returned None, likely structured-output timeout). 67/69 is
sufficient for B1; noted here for completeness. Both are short sessions (25 and 35 turns).

**Score weights are provisional.** The composite formula weights (0.50/0.35/0.15) are
untuned placeholders from report 05. Weight tuning requires the human gold set first.

**Real agent-log ingestion not built.** The current pipeline reads from pre-built digests in
layer1_outputs.jsonl. No adapters exist for live Claude Code, Aider, or Cursor log formats.
Scoring a real agent run requires manual digest construction today.

**Local judge only.** qwen3:30b-a3b via Ollama at $0/session. A production path (FLAMe-style
distillation to a smaller, faster judge) was outlined in report 05 but not started.

---

## Repo structure (key paths)

```
token-efficiency-scorer/
├── research/
│   ├── 01-sota-scan.md             IMMUTABLE
│   ├── 02-trajectory-waste.md      IMMUTABLE
│   ├── 03-validation-corpus.md     IMMUTABLE (domain priors, p25 baselines)
│   ├── 04-phaseA1-remeasure.md     IMMUTABLE (IAA results)
│   ├── 05-architecture-pivot.md    IMMUTABLE (accepted design)
│   ├── 06-calibration.md           IMMUTABLE (B1 calibration results)
│   └── cleanup-backlog.md          tech-debt list
├── scripts/
│   ├── layer1_features.py          Layer 1 feature extractor
│   ├── layer2_judge.py             Layer 2 Ollama judge (v3 prompt)
│   ├── calibration.py              Spearman rho + CI (human-gold-ready)
│   ├── calibration_multicutnow.py  Four-cut calibration (produced report 06 numbers)
│   ├── diagnose_distribution.py    Verdict distribution diagnosis
│   ├── investigate_findings.py     Post-scoring investigation script
│   ├── objective_proxy.py          Deterministic efficiency proxy
│   ├── score.py                    Composite score formula
│   ├── gpu_score_runner.sh         Preemption-safe GPU scoring runner
│   ├── provision_gpu_vm.sh         GCP VM provisioner
│   └── vm_startup.sh               GCP startup script (HOME fix + chown)
├── src/token_efficiency/
│   ├── trace_digest.py             Deterministic structured digest
│   └── layer1_features.py          Layer 1 feature computation
├── data/
│   ├── layer1_outputs.jsonl        191 sessions with Layer 1 features + digests
│   ├── judge_scores.jsonl          67-session GPU calibration scores (qwen3:30b-a3b v3)
│   ├── objective_proxy.jsonl       Deterministic proxy scores (191 sessions)
│   ├── llm_provisional_ratings.jsonl  Sonnet provisional ratings (188 sessions)
│   ├── calibration_sample.json     69-session calibration subset metadata
│   ├── cost-log.jsonl              API spend log (append-only, ~$2.59 cumulative)
│   └── validation-corpus/          191 annotated sessions (DO NOT REGENERATE)
├── CURRENT_STATE.md                This file
├── NEXT_PHASE.md                   Candidate next builds (not a committed plan)
├── spec.md                         B1 iteration spec (complete)
└── PLAN.md                         Execution tracker (B1 closed)
```

---

## What NOT to touch

- **research/01-06-*.md** — All immutable. New findings get report 07+.
- **data/validation-corpus/** — Do not regenerate or modify.
- **data/cost-log.jsonl** — Append-only. $5 cumulative API cap; currently ~$2.59.
- **data/gold/human_ratings.jsonl** — Does not exist yet. Do not synthesize or impute.

---

## GCP infrastructure status

All B1 scoring resources deleted as of 2026-06-02:
- VM tes-judge-scoring-tmp: DELETED
- Boot disk (100 GB pd-balanced, asia-east1-a): DELETED
- No snapshots, static IPs, or storage buckets were created

Estimated actual GCP spend for the full scoring job:
- g2-standard-8 SPOT (asia-east1-a): ~$0.56/hr (on-demand ~$1.40/hr, SPOT ~60% off)
- VM active time: ~75 min (setup + timing test + scoring run + retrieval restart)
- VM compute: ~$0.70
- 100 GB pd-balanced disk: ~$0.02 (1.25 hr at $0.102/GB-month amortized)
- **Total estimated: ~$0.72 USD**
- Note: precise figure requires GCP Billing Explorer (no CLI path to line-item data).
  The gpu-judge-safety-net budget (INR 850 / ~$10 USD cap) was not triggered.
- Cumulative Anthropic API spend: ~$2.59 of $5 cap (judge is local, $0/session)

---

## Conventions (non-obvious)

1. **Judge model is local Qwen3 via Ollama.** $0 inference. Do not substitute Claude or any
   paid API as judge without escalation and explicit rationale.
2. **Human gold ratings are sacred.** Never synthesize, impute, or LLM-fill. If not rated by a
   human, not in the gold set. Full stop.
3. **API keys in .env, loaded via python-dotenv.** Never export ANTHROPIC_API_KEY to shell.
4. **Reports are versioned by phase, never edited after acceptance.**
5. **Judge rates efficiency CONDITIONAL on the task, not task success.** Success lives only in
   outcome_score. Conflating the two corrupts the composite score.
