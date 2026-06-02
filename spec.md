# Project Spec: token-efficiency-scorer — Real Efficiency Number via Quality-Gated Baselines (Iteration B2)

## Goal

Make the efficiency score REAL on customer (Claude Code) data by building a token-economy baseline that has a quality floor. The launch-1 target is a defensible efficiency number, not just a trajectory-quality verdict.

The core idea (decided with the consultant): a baseline only means "what an efficient run should cost" if the sessions in it are known-good. We have no human labels, but we DO have a validated judge. So: pool real CC sessions, score them with the judge, KEEP ONLY judge-certified-good ones, classify by task-type, and compute per-type token baselines from the good sessions. A customer session's efficiency = its tokens vs the certified-good baseline for its task-type.

Why this works where SWE-bench baselines didn't: the baseline and the customer session both use the SAME CC-native token construct (actual API billing units), so the ratio is internally consistent. SWE-bench p25 baselines (word_count × 1.3) are a different construct and remain unusable for CC data — do not reintroduce them.

## Current state

See CURRENT_STATE.md. Key points:
- Judge (Qwen3-30B-A3B, v3 prompt) is validated as a coherent trajectory-quality instrument (rho=0.79 vs reference LLM, NOT human-validated). Model + digest schema are LOCKED.
- Claude Code adapter built and validated end-to-end on real sessions (report 07). Tool calls are structured in CC logs (clean extraction).
- CC sessions have NO resolved flag (no test harness) and NO H2 annotation — confirmed gaps. The composite's outcome and H2 terms are unavailable on CC data; the score leans on judge + token-economy.
- SWE-bench token construct ≠ CC token construct (report 07) — baselines must be CC-native.
- Judge requires GPU; the GCP L4 SPOT path is established (~$0.15 per short run; a few $ for a large pool).
- No human gold set (deferred to launch-2). No accuracy / "human-calibrated" claims permitted.
- Cumulative Anthropic API spend ~$2.59 of $5 cap. GCP credits are a separate pool.

## Scope

### In scope (this iteration)
- Pull public CC trace corpora (e.g. armand0e/kimi-k2.6-claude-code-traces, cfahlgren1/agent-sessions-list) + the user's 140 local sessions into one pool. Confirm the existing adapter reads the public format.
- Judge-score the pooled sessions on GPU (this ALSO serves generalization/hardening — sane verdicts beyond our 140).
- Filter to judge-certified-good sessions (define the quality gate; recommend MUCH_BETTER + BETTER).
- Build a task-type classifier (local Qwen, $0) with a taxonomy DERIVED from the pool, not imposed from SWE-bench. Validate classifier consistency.
- Compute per-task-type CC-native token baselines from the certified-good sessions only.
- Wire the efficiency number: customer_session_tokens vs certified-good baseline for its task-type, into the composite formula.
- Validate the efficiency number on a HELD-OUT set of sessions (not used in baseline computation).
- research/08-baselines.md documenting the whole pipeline, decisions, and limitations.

### Out of scope (do not build)
- Human gold set (launch-2).
- Reintroducing SWE-bench p25 baselines for CC data.
- Per-customer adaptive baselines (launch-2 — this iteration builds the reference-corpus baseline, option 2; per-customer is option 1, later).
- Additional ingestion adapters beyond Claude Code (Aider etc. deferred).
- Dashboard / packaging / distribution build.
- Changing the judge model or digest schema.

## Tech stack
- Python (match repo conventions).
- huggingface_hub / datasets for pulling public corpora (escalate before adding if absent).
- Ollama Qwen3-30B-A3B (judge, GPU) + a local Qwen (3-8B or 30B) for the task classifier.
- scipy/numpy/pandas for baseline statistics + validation.
- GCP L4 SPOT for GPU judge runs (established path).
- No paid API calls without escalation (judge + classifier are local/open).

## Architecture (new or modified files only)
```
scripts/
├── pull_corpora.py            # NEW - fetch public CC datasets + merge with local 140
├── task_classifier.py         # NEW - task-type classification (local Qwen)
├── build_baselines.py         # NEW - per-type CC-native baselines from judge-good sessions
└── efficiency_score.py        # NEW or MODIFIED - wires baseline into composite

data/
├── corpus_pool/               # NEW - pooled adapted sessions (public + local)
├── pool_judge_scores.jsonl    # NEW - judge verdicts over the pool
├── task_taxonomy_cc.json      # NEW - derived task-type taxonomy
├── cc_baselines.json          # NEW - per-type token baselines (the reference)
└── cost-log.jsonl             # APPEND ONLY

research/
└── 08-baselines.md            # NEW
```

## Key design decisions (resolve early, escalate)
1. QUALITY GATE: which verdicts count as "certified-good" for the baseline? Recommend {MUCH_BETTER, BETTER}. Report the count that survives the gate per task-type.
2. BASELINE STATISTIC: what number IS the baseline per type? Options: median tokens of good sessions ("typical good run"), or a percentile. Recommend MEDIAN of judge-good sessions (p25 of already-good sessions may be too strict). Escalate the choice with the resulting distributions.
3. TASK TAXONOMY: derive from the pool (embedding-cluster task_descriptions, or a small fixed set validated against the pool) — do NOT impose SWE-bench's 8 domains. Target a small, interpretable set.
4. SPARSE TYPES: minimum N of good sessions required for a stable baseline (recommend >=10). For types below threshold or unseen customer types: efficiency marked UNAVAILABLE for that session (honest), NOT computed against a fabricated fallback.
5. CIRCULARITY CHECK: the baseline is token-count of judge-good sessions; the judge assesses trajectory quality. These are different axes (a good session can be high-token for a big task). Verify empirically that baseline tokens are not just a proxy for judge_score — report the correlation. If high, flag it.

## Verification commands
```yaml
- name: adapter-reads-public
  cmd: python scripts/pull_corpora.py --dry-run --limit 5
  required: true
- name: classifier-consistency
  cmd: python scripts/task_classifier.py --selftest
  required: false
- name: baseline-integrity
  cmd: python -c "import json; b=json.load(open('data/cc_baselines.json')); assert all(v['n']>=10 for v in b['types'].values()); print('baselines valid')"
  required: false
- name: cost-check
  cmd: python -c "import json; t=sum(json.loads(l).get('cost_estimate_usd',0) for l in open('data/cost-log.jsonl')); print(f'${t:.2f}'); assert t<5"
  required: true
```

## Escalation rules (orchestrator must ask before doing)
- BEFORE the large GPU judge run over the pool: report pool size, estimated wall-clock + GCP cost. If estimate exceeds a few hours / a few dollars, confirm before provisioning.
- BEFORE finalizing the quality gate, baseline statistic, and task taxonomy (decisions 1-3) — surface options with real distributions, HOLD.
- If the circularity check (decision 5) shows baseline tokens correlate strongly with judge_score — flag before building the efficiency number on it.
- If a large share of the pool fails to adapt (public format drift) — report, don't silently drop.
- BEFORE any paid API call. BEFORE installing deps not in stack. BEFORE modifying reports 01-07.
- If validation shows the efficiency number behaves nonsensically on held-out sessions — STOP and escalate; do not ship a number that doesn't validate.

## Hard rules
- Judge model + digest schema LOCKED.
- Baselines are CC-native only; never mix in SWE-bench token numbers.
- Quality gate is judge-certified; never include quality-unknown sessions in a baseline.
- Efficiency UNAVAILABLE is an acceptable output; a fabricated baseline is not.
- No human-accuracy / "calibrated to experts" claims anywhere.
- GPU VMs: SPOT, trapped auto-shutdown, hard max-runtime stop, persistent-disk JSONL, distinctive temp name, budget alert, torn down after each run with actual spend reported. Never touch aetherart-eval-001 / review-iq-prod live work.
- Reports 01-07 immutable. .env in-process only. Cost log append-only. No secrets committed.

## Budget
- Soft: 2-4 CC sessions (large GPU run + classifier build + validation span real time).
- Anthropic API: $5 cumulative cap, escalate at $4 (judge/classifier are local, so near-zero added).
- GCP: separate credits; report actual spend per run; budget alert at $10.
- Orchestrator runs /cost at midpoint.

## Success criteria (verify ALL before done)
- Public CC corpora pulled and adapted; report how many sessions adapted vs failed.
- Pool judge-scored on GPU; verdict distribution reported (also serves generalization evidence).
- Quality gate applied; certified-good count per task-type reported.
- Task taxonomy derived + validated; classifier consistency reported.
- Per-type CC-native baselines computed from good sessions only; minimum-N guard enforced; sparse/unseen types -> UNAVAILABLE.
- Circularity check reported (baseline-tokens vs judge_score correlation).
- Efficiency number wired into the composite and validated on a HELD-OUT set; behaves sensibly (lean-good sessions score efficient, wasteful sessions score inefficient).
- research/08-baselines.md documents pipeline, all 5 design decisions, validation, and limitations (no human gold; reference-corpus not per-customer; only CC adapter).
- GCP torn down, zero lingering resources, actual spend reported.
- Git clean, conventional commits. No reports 01-07 modified.

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md, reports 05/06/07. Inspect repo conventions.
2. pull_corpora.py: fetch public CC datasets, merge with local 140, run through the CC adapter. Report adapted-vs-failed counts + pool size. HOLD if a large share fails to adapt.
3. Scope the GPU judge run: pool size -> wall-clock + GCP cost estimate. Escalate the estimate before provisioning.
4. Provision L4 SPOT, judge-score the whole pool, retrieve, tear down. Report verdict distribution + actual spend.
5. Apply quality gate; report certified-good counts. Escalate the gate choice (decision 1).
6. Build task taxonomy from the pool + classifier; validate consistency. Escalate taxonomy (decision 3).
7. Compute per-type baselines from good sessions (decision 2 statistic); enforce min-N; mark sparse/unseen UNAVAILABLE (decision 4). Run circularity check (decision 5). Escalate decisions 2+5.
8. Wire efficiency_score.py; validate on held-out sessions. HOLD for consultant read.
9. Write research/08-baselines.md. Commit. Final verification. Confirm teardown.
