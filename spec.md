# Project Spec: token-efficiency-scorer — Hybrid Scorer Implementation (Iteration B1)

## Goal

Build the hybrid LLM-judge scorer designed in accepted report 05, and prove it works by calibrating the judge against a human gold set. This iteration succeeds if we have an end-to-end scoring pipeline (Layer 1 + Layer 2) and a calibration number (Spearman rho of judge vs human ratings) that clears the kill-criterion floor. This is the make-or-break iteration: if the judge can't calibrate, the architecture is wrong and we stop and rethink.

## Current state

See CURRENT_STATE.md in full. Key points:
- Validation is complete; heuristic-primary is retired. H2 survives as a feature; H1/H3/H4 are dead.
- Architecture is the three-layer hybrid from report 05. Judge = local Qwen3-8B via Ollama (already installed and smoke-tested).
- The human gold set is the calibration ground truth and the single most important asset. LLM labels are NOT ground truth.
- Reports 01-05 are immutable. Report 03 holds domain resolve-rates and p25 token baselines.
- Cumulative cost ~$2.59 of $5. The judge is local ($0), so this iteration's API spend is near zero unless a frontier diagnostic is escalated.

## Scope

### In scope (this iteration)

- Layer 1 deterministic feature extractor: compute the 7 features and a deterministic structured trace digest for each session.
- Digest fidelity check: validate that the digest preserves efficiency-relevant signal, on a small sample, BEFORE the human rates.
- Rating interface: a local script that presents one session at a time (session-level stats prominent, turn-by-turn digest skimmable) and records a 1-5 efficiency rating plus an optional note to a gold JSONL.
- Human gold set: the consultant rates 40 sessions (stratified by domain and resolved/unresolved). This is a human escalation step.
- Layer 2 judge: Ollama Qwen3-8B client, reference-based pointwise, criterion-order permutation fix, structured output schema.
- Calibration: Spearman rho between judge scores and human gold ratings; report with confidence interval.
- Kill-criterion evaluation: rho >= 0.55 floor to proceed; rho >= 0.75 target. Escalate the verdict.
- If calibration passes: implement the full composite score formula end-to-end and run it on the gold set.
- Report 06 documenting calibration results and the working pipeline.
- First real unit tests for the deterministic Layer 1 functions.

### Out of scope (do not build)

- Corpus expansion (real Claude Code, Aider non-Python, multi-language) - deferred.
- Judge distillation / fine-tuning a small production judge - later productization.
- Weight optimization beyond a single tuning pass against the gold set.
- Any API / package / product-packaging work (later iteration).
- Modifying reports 01-05.
- Re-annotating or regenerating the corpus.
- Actioning cleanup-backlog items unless a build step strictly depends on one.
- Switching the judge to Claude or any paid API.

## Tech stack

- Python (match existing pyproject.toml).
- python-dotenv for any key loading (judge needs no key - it's local).
- ollama Python client (or HTTP to http://localhost:11434) for Qwen3-8B. Escalate before adding the package if not present; HTTP needs no new dep.
- scipy for Spearman rho and CI; numpy, pandas (likely present).
- pytest for the new unit tests (escalate before installing if absent).
- No other new packages without escalation.

## Architecture (new or modified files only)

```
src/  (or scripts/ - match existing repo convention; inspect first)
├── layer1_features.py      # NEW - 7 deterministic features per session
├── trace_digest.py         # NEW - deterministic structured digest builder
├── rating_interface.py     # NEW - human rating CLI, writes gold JSONL
├── layer2_judge.py         # NEW - Ollama Qwen3-8B reference-based pointwise judge
├── calibration.py          # NEW - Spearman rho judge vs human gold
└── score.py                # NEW - composite score formula (Layer 1 + Layer 2)

data/
├── gold/
│   └── human_ratings.jsonl # NEW - the human gold set (sacred)
├── judge_outputs/          # NEW - judge verdicts per session
└── cost-log.jsonl          # APPEND ONLY

research/
└── 06-calibration.md       # NEW

tests/
└── test_layer1.py          # NEW - unit tests for deterministic features
```

## Data model

Human gold rating (one JSON line per rated session):
```json
{"session_id": "...", "domain": "...", "resolved": true,
 "efficiency_rating": 4, "note": "optional free text",
 "rated_at": "ISO-8601", "rater": "consultant"}
```

Judge output (one JSON object per session):
```json
{"session_id": "...", "verdict": "BETTER",
 "verdict_score": 0.75, "waste_categories": ["..."],
 "confidence": 0.0, "position_swap_consistent": true,
 "reasoning": "...", "judge_model": "qwen3:8b", "prompt_sha256": "..."}
```

Verdict scale -> score: MUCH_BETTER 1.0 / BETTER 0.75 / SIMILAR 0.50 / WORSE 0.25 / MUCH_WORSE 0.0.

## Verification commands

```yaml
- name: unit-tests
  cmd: pytest tests/ -v
  required: true
- name: layer1-coverage
  cmd: python -c "import json,glob; n=len(glob.glob('data/validation-corpus/annotations/gpt_oss/*.json')); print(f'{n} sessions available')"
  required: true
- name: ollama-up
  cmd: python -c "import urllib.request; urllib.request.urlopen('http://localhost:11434/api/tags',timeout=5); print('ollama reachable')"
  required: true
- name: gold-integrity
  cmd: python -c "import json; rows=[json.loads(l) for l in open('data/gold/human_ratings.jsonl')]; assert all(1<=r['efficiency_rating']<=5 for r in rows); print(f'{len(rows)} gold ratings valid')"
  required: false
- name: cost-check
  cmd: python -c "import json; t=sum(json.loads(l).get('cost_estimate_usd',0) for l in open('data/cost-log.jsonl')); print(f'${t:.2f}'); assert t<5"
  required: true
- name: lint
  cmd: ruff check .
  required: false
```

ruff/pytest best-effort if unconfigured - escalate rather than install silently.

## Subagent usage rules

- executor for any file write/edit.
- verifier for tests, lint, ollama checks, and running the calibration/judge scripts.
- Orchestrator does NOT write code - always delegates.
- The human rating step is NOT a subagent task - it is a user escalation; the orchestrator hands off and waits.

## Escalation rules (orchestrator must ask before doing)

- BEFORE the human rating step: confirm the rating interface is validated (digest fidelity check passed) and present the 40-session stratified sample for the user to rate. Then HOLD for the user to complete ratings - this is a multi-hour human task, not a quick reply.
- If the digest fidelity check suggests the human cannot reliably rate from the digest - escalate the digest design before proceeding.
- BEFORE any frontier-model / paid-API call (e.g., the calibration diagnostic if Qwen3-8B underperforms).
- If calibration rho < 0.55 after 3 prompt-iteration attempts - kill criterion; escalate, do NOT keep iterating silently.
- If calibration rho is in [0.55, 0.75) - proceed but escalate the number; the user decides whether to ship or tune further.
- BEFORE installing any dependency not in Tech stack.
- If cumulative cost exceeds $4.
- BEFORE modifying any research report 01-05.
- If a single executor pass would touch more than 4 files.
- If verification fails 3 times in a row on the same check.
- BEFORE expanding the corpus or actioning a cleanup-backlog item.

## Hard rules

- DO NOT modify research reports 01-05.
- DO NOT regenerate or overwrite data/validation-corpus/.
- DO NOT synthesize, impute, or LLM-fill human gold ratings. Ever.
- DO NOT use a Claude model or any paid API as the judge (local Qwen3-8B only) without escalation.
- DO NOT let judge_score encode task SUCCESS - it rates efficiency conditional on the task. Success lives only in outcome_score. (Prevents collinearity.)
- DO NOT export ANTHROPIC_API_KEY to the shell; load .env in-process.
- DO NOT rewrite past cost-log entries (append only).
- DO NOT commit .env or secrets.

## Budget

- Soft target: 2-3 Claude Code sessions (the human rating step spans real-world time).
- Hard cap: stop and escalate after 20 executor invocations.
- API cost: judge is local ($0). Only a diagnostic frontier batch would cost - escalate first. $5 cumulative cap, escalate at $4.
- Orchestrator runs /cost at midpoint and reports.

## Success criteria (orchestrator verifies ALL before declaring done)

- Layer 1 produces 7 features + a digest for all 191 sessions without crashing.
- Digest fidelity check ran and passed (or its limitations are documented and accepted).
- Rating interface works; the user rated 40 sessions; data/gold/human_ratings.jsonl has 40 valid rows.
- Layer 2 judge runs locally via Ollama and produces a verdict for every gold session, with position-swap consistency recorded.
- Calibration computed: Spearman rho (with CI) between judge verdict_score and human efficiency_rating, reported in research/06-calibration.md.
- Kill-criterion evaluated and the verdict escalated to the user.
- If rho >= 0.55: composite score formula implemented and run end-to-end on the 40 gold sessions, with per-session scores in an output file.
- Unit tests for Layer 1 pass.
- Cumulative cost < $5.
- No research report 01-05 modified; corpus not regenerated; no gold rating synthesized.
- Git history clean, conventional commits.

## Build order (recommended; orchestrator may adjust)

1. Read CURRENT_STATE.md, then spec.md. Inspect repo to match src/ vs scripts/ convention.
2. Build Layer 1: layer1_features.py + trace_digest.py. Run on all 191 sessions. Add tests/test_layer1.py. Verifier runs pytest.
3. Digest fidelity check: build a couple of digests, present to the user to confirm they're rateable. HOLD briefly for user confirmation.
4. Build rating_interface.py. Verifier smoke-tests it on 1 session.
5. ESCALATE: present the 40-session stratified sample; user rates them via the interface. HOLD for completion (multi-hour human task).
6. Build layer2_judge.py (Ollama, reference-based pointwise, permutation fix). Verifier runs it on 2-3 sessions to confirm schema + Ollama connectivity.
7. Run the judge on all 40 gold sessions. Record verdicts + position-swap consistency.
8. Build calibration.py. Compute Spearman rho judge vs human gold.
9. Evaluate kill criterion. Escalate the number and verdict to the user. HOLD.
10. If cleared: build score.py, run the composite formula on the 40 gold sessions.
11. Write research/06-calibration.md. Commit. Run full verification. Declare done.
