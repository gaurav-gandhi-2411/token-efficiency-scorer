# CURRENT_STATE.md — token-efficiency-scorer

Snapshot as of 2026-06-03. Read this BEFORE planning. This supersedes the prior snapshot
dated 2026-06-02.

---

## Iteration status: B2 CLOSED

The B2 iteration is complete. Quality-gated CC-native token baselines are built, validated,
and committed. The two-axis efficiency product (token economy + trajectory quality) is now
scoped with explicit domain boundaries.

Do NOT extend this iteration. Read NEXT_PHASE.md for candidate next directions.

---

## What B2 delivered

**Token measure (locked):**
`real_tokens = sum_ai_turns(token_count_input - cache_read + token_count_output)`
Excludes cache_read re-accumulation (which inflated total_tokens by 87–94% on CC sessions).
CC-caching-native only — non-caching agents (Kimi etc.) cannot use this baseline.

**Quality gate:** MUCH_BETTER only (strict). 75 local Claude CC sessions as the reference
corpus. Armand0e/Kimi excluded — no-caching token accounting is incommensurable.

**Task taxonomy (5 types, keyword classifier):**
ml-eval, debug-fix, infra-deploy, research-recon, feature-build (fallback).
Handles 10 `<local-command-caveat>` sessions. One documented session override.
Classifier: deterministic keyword matching, no LLM, consistency PASS.

**Per-type baselines (cc_baselines.json):**

| Type | n | p25 | median | p75 |
|---|---|---|---|---|
| infra-deploy | 20 | 386K | 698K | 1,003K |
| debug-fix | 19 | 353K | 524K | 654K |
| ml-eval | 12 | 458K | 646K | 1,034K |
| research-recon | 12 | 362K | 718K | 1,339K |
| feature-build | 12 | 424K | 711K | 803K |

**Scope gate (p10 turns per type):** ml-eval=127, debug-fix=59, infra-deploy=63,
research-recon=44, feature-build=166. Sessions below the floor → UNAVAILABLE (token axis);
trajectory verdict only. Rationale: p10 anchors to reference mass, not a single outlier.

**Circularity:** Spearman r=−0.0801, p=0.3418 (n=143). Token baseline and judge score
are independent axes.

**Two-axis output (efficiency_score.py):**
- Token economy: scope status + band verdict (above_p75 / within_band / below_p25 / unavailable)
- Trajectory quality: judge verdict + score + reasoning (populated when judge entry provided)
- No composite score — each axis labeled with its own domain of validity.

---

## Validated findings

**Two-axis orthogonality confirmed.**
- `6852df92` (MUCH_WORSE, within_band): trajectory scope violation, normal token cost. Judge
  catches what the token axis misses.
- `78bd2719` (WORSE, above_p75): 470 turns of repeated WSL2 failures. Both axes agree.
- `b9c6cbd4` (BETTER, above_p75): larger task done well. Token excess = task scope, not waste.
  Judge verdict needed to distinguish.

**Cache inflation was real.** Removing cache_read re-accumulation reduced per-session counts
by 87–94%. The ml-eval bimodality (CV=0.88 inflated → CV=0.51 corrected) dissolved completely.

**Circularity is not a concern.** Baseline tokens and judge scores are orthogonal (r=−0.08, n.s.).

**Armand0e gate rate (6% vs 69%) is earned, not bias.** Investigation confirmed identical
behavioral grounding (same waste categories, turn-specific citations, same bar applied to both
populations). The gap reflects population-level quality difference from expert prompting.

---

## Known limitations (all carry forward — do not paper over)

**Single-developer, expert-prompted corpus.** 97% of the baseline is one developer's
sessions, predominantly structured orchestrator workflows with explicit ROLE/STEP/CONSTRAINT
prompting. The baseline encodes "efficient under expert prompting." Cross-customer and
cross-prompting-style generalization is unvalidated.

**Token axis scope boundary.** The token verdict fires for 50% of the held-out sessions;
17/34 are UNAVAILABLE (below p10 scope gate). Coverage improves with more reference data
across scope ranges, not with wider quality bands. MUCH_BETTER+BETTER widening was
evaluated and rejected — it raises coverage from 26.5% to 44.1% but dilutes the quality
floor. See report 08.

**feature-build: zero held-out validation.** All 3 held-out feature-build sessions were
below the scope gate (12–35 turns vs gate of 166). The baseline exists but is unvalidated
on held-out data.

**No human gold.** Judge validated at rho=0.79 vs Sonnet reference LLM (instrument
coherence only, not accuracy). No "calibrated to human experts" claims permitted.

**CC-caching-native tokens.** Non-caching agents need their own baseline (launch-2 /
per-customer accrual).

**Score weights still provisional.** The B1 composite formula weights (0.50/0.35/0.15) are
untuned. The composite score is NOT run on CC sessions in B2 — token economy + trajectory
verdict are the two-axis product for launch-1.

---

## Repo structure (key paths, B2 additions)

```
token-efficiency-scorer/
├── research/
│   ├── 01-07-*.md              IMMUTABLE (B1 reports)
│   └── 08-baselines.md         B2 final report — IMMUTABLE
├── scripts/
│   ├── task_classifier.py      5-type keyword classifier + selftest
│   ├── build_baselines.py      Baseline computation + circularity check
│   ├── efficiency_score.py     Two-axis session scorer
│   ├── adapters/
│   │   └── claudecode_adapter.py  CC JSONL → digest schema
│   ├── pull_corpora.py         Pool ingestion (local + HF public)
│   └── layer2_judge.py         Qwen3 judge (locked, GPU-required)
├── data/
│   ├── cc_baselines.json       Per-type baselines + scope gates (locked)
│   ├── pool_judge_scores.jsonl 143 sessions scored (qwen3:30b-a3b)
│   ├── corpus_pool/
│   │   └── pool_adapted.jsonl  181 adapted CC sessions
│   └── cost-log.jsonl          Append-only, ~$2.59 Anthropic cumulative
├── CURRENT_STATE.md            This file
└── NEXT_PHASE.md               Candidate next directions (not committed)
```

---

## What NOT to touch

- **research/01-08-*.md** — All immutable. New findings get report 09+.
- **data/corpus_pool/** and **data/pool_judge_scores.jsonl** — Do not re-score or modify.
- **data/cc_baselines.json** — Locked for launch-1. Rebuild only for launch-2 with new data.
- **data/cost-log.jsonl** — Append-only. $5 cumulative Anthropic cap; currently ~$2.59.

---

## GCP infrastructure status

All B2 GPU VMs deleted after scoring:
- B2 pool scoring VM (asia-east1-a, g2-standard-8 SPOT): DELETED
- B2 step3 rescore VM: DELETED
- CC validation VM (report 07): DELETED (previously recorded)
- No persistent disks, snapshots, static IPs, or storage buckets remain

Estimated B2 GCP spend: ~$3.53 USD (pool scoring ~$2.80, rescore ~$0.36,
CC validation ~$0.37). GCP credits pool, not Anthropic cap.

---

## Judge configuration (locked — do not change)

- Model: qwen3:30b-a3b via Ollama ($0/session, GPU required)
- Prompt: v3 — trajectory purposefulness only, /no_think prefix
- Parameters: temp=0, seed=42, num_predict=6144, JSON schema
- GPU path: GCP g2-standard-8 SPOT, asia-east1-a
- DO NOT substitute Claude or any paid API as judge without escalation.
