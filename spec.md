# Project Spec: token-efficiency-scorer — Deterministic Waste Detection Layer (Iteration B4)

## Goal

Move waste-detection from the LLM judge (which B3 proved is model-dependent — Gemma reversed 94% of Qwen's waste verdicts, kappa=0.31) to a DETERMINISTIC layer that measures observable waste events from the trace. The B3 finding was decisive: "was this trajectory wasteful?" asked as an LLM judgment is a contested opinion; asked as "did the agent repeat a failing action without changing it?" it is a reproducible measurement. This iteration builds the full deterministic waste layer, grounded in established trajectory-evaluation primitives, so waste claims rest on counted events with trace-level evidence — not on a model's verdict.

This directly fixes B3: deterministic detectors are model-independent by construction. The contested-opinion problem is engineered out, not papered over.

## Design principle (the credibility rule — read first)

A deterministic detector is only credible if it fires ONLY on behavior that is waste under ANY reasonable definition. The B3 lesson applies recursively: if a rule encodes a contestable definition of waste (e.g. "any repeated file read = waste"), the opinion has just moved from the LLM into the regex, where it's harder to see and audit. So every detector must be CONSERVATIVE and UNCONTESTABLE:
- Flag repeated IDENTICAL failed commands with NO intervening change — not "repeated commands."
- Flag re-reads of an UNCHANGED file with no edit between — not "re-reads" (re-reading after an edit is correct behavior).
- Prefer UNDER-detecting defensible waste over OVER-detecting arguable waste.
Each fired event must be backed by the specific turns that prove it, so the output is auditable against the raw trace. "Deterministic" must mean "model-independent and uncontestable," not "my opinion, in code."

## Current state

See CURRENT_STATE.md. B1-B3 complete:
- Judge = Qwen3-30B-A3B, v3 prompt, validated as a trajectory-QUALITY instrument (rho=0.79 vs reference LLM). POSITIVE verdicts cross-model corroborated (84% gate overlap w/ Gemma). NEGATIVE/waste verdicts NOT corroborated (B3, report 09) — hence this iteration.
- B2 token baselines (CC-native, cache-corrected, strict-gate, scope-gated) built and validated. Two-axis scoped product.
- Digest schema captures per-turn: role, tool_names, content_snippet, token_count_input/output, cache_read. The trace digest already has the structured data deterministic detectors need.
- 143-session CC pool scored by both Qwen and Gemma, on GitHub.
- No human gold (deferred to post-raise, explicit decision). No accuracy claims. LLM-only + deterministic only.
- GPU path established (but this iteration is largely LOCAL/$0 — deterministic detection needs no model inference).

## Scope

### In scope (this iteration — the full (b) layer)
- Build deterministic waste detectors over the trace digest, each conservative and trace-auditable:
  1. REPEATED-FAILED-RETRY: same command/tool-call repeated with the same error signature and no intervening change. (The B3-contested signal — the priority detector.)
  2. REDUNDANT-READ: re-read of an unchanged file/resource with no edit or state change between reads.
  3. DEAD-END / LOOP: cycles of actions that return to a prior state without progress (bounded, conservative cycle detection).
  4. NO-OP / EMPTY-TURN: turns that produce no tool call and no state-advancing content (the "empty turns" Qwen flagged; define precisely so it doesn't catch legitimate planning turns — escalate the definition).
  5. OPTIMAL-PATH-RATIO (if computable): actual tool-calls vs a defensible minimum for the observed task — likely HARD/UNAVAILABLE without a reference, so treat as exploratory and may defer.
- Run all detectors over the 143-session pool; report per-detector fire rates + trace-level evidence samples.
- Cross-check against the LLM judge verdicts: where the deterministic layer fires waste, what did Qwen say? what did Gemma say? This is the key analysis — does deterministic waste correlate with Qwen's WORSE verdicts (i.e. was Qwen detecting real countable waste) or with neither (i.e. both models were judging something unmeasurable)?
- Re-architect the score: waste comes from the DETERMINISTIC layer (counted, evidenced); the JUDGE is scoped to the corroborated positive/quality signal; token-economy stays from B2 baselines.
- research/10-deterministic-waste.md documenting detectors, definitions, fire rates, the judge cross-check, and honest limits.

### Out of scope
- Human labels (post-raise).
- A third LLM judge.
- Using the LLM to detect waste (the whole point is to remove it from the judge).
- Changing the B2 token baselines or the v3 judge prompt.
- Packaging/distribution (next phase; the (a) launch subset is selected at the END of this iteration from what fires cleanly).
- Re-scoring the pool with any model (deterministic layer needs no inference).

### The (a) -> launch handoff (end of this iteration, not a separate build)
After the full (b) layer runs on real data, SELECT the launch-1 subset: keep the detectors that fire cleanly, defensibly, and with auditable evidence; defer or drop any that prove noisy or contestable. The data decides which detectors are launch-ready. Document the selection rationale.

## Tech stack
- Python, repo conventions. Pure trace analysis — string/structure parsing over the digest. No LLM inference, no GPU, no paid API.
- Existing trace_digest / layer1 structures as input.
- pytest for detector unit tests (deterministic = unit-testable with crafted fixtures, unlike the judge).

## Architecture (new files only)
```
scripts/
├── waste_detectors.py        # NEW - the deterministic detectors, each independently testable
├── run_waste_analysis.py     # NEW - run detectors over the pool, fire rates + evidence
└── waste_judge_crosscheck.py # NEW - deterministic waste vs Qwen/Gemma verdicts

tests/
└── test_waste_detectors.py   # NEW - fixture-based unit tests per detector (crafted traces)

data/
├── pool_waste_signals.jsonl  # NEW - per-session detector outputs + evidence turns
└── waste_crosscheck.json     # NEW - deterministic-vs-judge correlation

research/
└── 10-deterministic-waste.md # NEW
```

## Key design decisions (resolve early, escalate)
1. DETECTOR DEFINITIONS: each detector's firing condition is the load-bearing choice. Draft each as a precise, conservative rule + show me 3-5 REAL trace examples it fires on (and 2-3 near-misses it correctly does NOT fire on) BEFORE running pool-wide. The near-miss check is how we confirm the rule isn't encoding a contestable opinion. HOLD per detector.
2. EMPTY-TURN definition: highest contestability risk (a "planning turn" vs a "no-op" is exactly the Qwen/Gemma dispute). Define it so it fires ONLY on turns with no tool call AND no new decision/content — and validate against real planning turns that should NOT fire. If it can't be made uncontestable, mark it exploratory or drop it.
3. FAILED-RETRY error-signature matching: how to determine two failed attempts are "the same failure with no change" — exact command match? same error class? Define precisely; show real examples.
4. SCORE RE-ARCHITECTURE: how waste signals enter the composite. Options (surface, don't pre-decide): waste as a separate REPORTED axis (count + evidence, not folded into one number) vs a waste penalty term. Given B3, my lean is waste REPORTED as evidenced events alongside the quality + token axes — three transparent signals, not one opaque score. Decide with the consultant.
5. JUDGE CROSS-CHECK interpretation: if deterministic waste correlates with Qwen's WORSE verdicts -> Qwen was detecting real countable waste (vindicates the judge's negative signal as a proxy, but the deterministic version is what we ship). If it correlates with NEITHER model -> the LLM waste verdicts were judging something unmeasurable, reinforcing the move to deterministic. Report which.

## Verification commands
```yaml
- name: detector-unit-tests
  cmd: python -m pytest tests/test_waste_detectors.py -v
  required: true
- name: waste-evidence-integrity
  cmd: python -c "import json; [exit('no evidence') for l in open('data/pool_waste_signals.jsonl') if (r:=json.loads(l)).get('waste_events') and not all('turns' in e for e in r['waste_events'])]; print('all evidenced')"
  required: true
- name: no-inference-used
  cmd: grep -L 'ollama\|api.anthropic\|generate' scripts/waste_detectors.py
  required: true
```

## Escalation rules
- Per detector: draft definition + real fire examples + near-miss non-fires, HOLD before pool-wide run.
- If a detector can't be made conservative/uncontestable (fires on defensible behavior): mark exploratory or drop — escalate rather than ship a contestable "deterministic" rule.
- If the judge cross-check shows deterministic waste correlates with NEITHER model: surface it — it reframes what the judge's negative verdicts ever meant.
- BEFORE re-architecting the composite score: confirm the structure with the consultant.
- BEFORE modifying reports 01-09 (immutable) or B2 baselines.

## Hard rules
- Every waste event MUST carry the specific turns that prove it (trace-auditable). No unevidenced waste flags.
- Detectors are CONSERVATIVE: under-detect defensible waste over over-detect arguable waste.
- No LLM inference in the waste layer — it must be model-independent by construction.
- The judge stays scoped to the B3-corroborated POSITIVE/quality signal; do not use it for waste.
- No human labels. No accuracy claims. Reports 01-09 immutable. B2 baselines unchanged unless the consultant approves.
- .env in-process only; cost log append-only; secrets masked at ingestion (built).

## Budget
- Soft: 1-3 CC sessions. Mostly local/$0 (no inference).
- Anthropic API: unchanged (~$2.59); this iteration adds ~$0.
- GCP: none expected (no GPU needed). If any detector somehow needs scale compute, escalate first.

## Success criteria (verify ALL before done)
- Each detector: precise conservative definition + real fire examples + near-miss non-fires, consultant-approved.
- Unit tests per detector (crafted fixtures) pass.
- Detectors run over the 143-session pool; per-detector fire rates + evidence samples reported.
- Every waste event carries proof turns (verification command passes).
- Judge cross-check: deterministic-waste vs Qwen-WORSE vs Gemma reported, interpreted per decision 5.
- Score re-architecture: waste as evidenced reported signal (or approved alternative); composite documented.
- research/10-deterministic-waste.md complete: detectors, definitions, fire rates, cross-check, limits (e.g. deterministic detection measures THAT waste events occurred, not a contested "how bad"; optimal-path-ratio likely UNAVAILABLE without a reference).
- (a)-subset selected for launch: which detectors are launch-ready and why.
- No reports 01-09 modified. Git clean. No inference in the waste layer.

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md + reports 06/07/08/09 + spec.md. Internalize the credibility rule + B3 finding.
2. Draft detector 1 (REPEATED-FAILED-RETRY — the B3-contested priority). Precise rule + real fire examples + near-miss non-fires from the pool. HOLD for consultant approval.
3. On approval, unit-test it (crafted fixtures), then the remaining detectors (redundant-read, dead-end/loop, empty-turn) one at a time, same approve-then-test cycle. empty-turn gets extra scrutiny (decision 2). optimal-path-ratio: assess feasibility, likely defer.
4. Run all approved detectors over the pool; report fire rates + evidence. HOLD for consultant read.
5. Judge cross-check (deterministic waste vs Qwen/Gemma verdicts). Report + interpret. HOLD.
6. Re-architect the score (decision 4, consultant-approved structure). Document.
7. Write research/10-deterministic-waste.md. Select the (a) launch subset from what fired cleanly. HOLD for consultant read before calling B4 done.
