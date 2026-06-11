# Project Spec: tracegauge — Token Attribution + Judge-On-Demand (Iteration P8)

## Goal

Turn tracegauge from a thermometer ("you used 1.4M tokens, +640%") into a diagnostic that answers the user's actual question: **"are my tokens used PROPERLY?"** Two halves:

- **A — Token Attribution (deterministic, no GPU):** break each session's tokens into WHERE THEY WENT — productive work vs. re-read files vs. retry loops vs. context re-send/bloat. This is the measurable form of "properly." It builds on data already in the digest (per-turn input/output/cache_creation/cache_read, the waste detectors).
- **B — Judge On-Demand (the AI/ML half):** make trajectory-quality scoring actually available, via EITHER a local Ollama judge OR an opt-in API-key judge (Claude via the user's own key). Answers the judgment question A can't: "was this work justified / on-track / efficient in PATH, not just volume?"

Together they answer "properly": A says WHERE the tokens went (measured), B says WHETHER that was justified (judged). The current "+640%" headline becomes a diagnosis: "1.4M tokens — 60% productive, 25% context re-send, 10% redundant reads; judge says substantial work but context bloat cost ~$3; a checkpoint around turn 100 would have helped."

## The honesty constraint specific to this phase (read first)

**Token attribution must be MEASURED, not estimated by vibes.** The temptation is to slap plausible-looking percentages on a pie chart. That would be the exact "meaningful-but-fake number" failure this project has refused all along. So:

1. **Every attribution bucket must be defined by an OBSERVABLE, defensible rule** over the session digest — not a heuristic guess. "Re-read tokens" = tokens in turns the REDUNDANT-READ detector fired on (already proven). "Retry-loop tokens" = tokens in REPEATED-FAILED-RETRY turns (already proven). "Context re-send" = a DEFINED, measurable quantity (see below). "Productive" is the RESIDUAL after the measurable-waste buckets — and must be LABELED as residual, not claimed as "definitely productive."
2. **Buckets must sum to the session's real token total** (reconciliation — like the cost sum check). No tokens unaccounted, no double-counting.
3. **Attribution carries its domain of validity:** "productive" is "not-attributable-to-measured-waste," NOT a positive proof of value. The tool must not claim "60% of your tokens were productive" — it claims "60% were not attributable to detectable waste; the judge assesses whether that work was on-track."
4. **The judge (B) is where JUDGMENT lives — and it carries the B3 caveats:** positive signal corroborated, negative model-dependent, no human calibration. An API judge (vs local) does NOT change those caveats — it changes availability, not validity.

## The token-attribution buckets (A — define rigorously, escalate the definitions)

For a session, attribute its real tokens into observable buckets that SUM to the total:
1. **Redundant-read tokens** — token cost of the turns the REDUNDANT-READ detector flagged (the re-read content). Measured, proof-turns already exist.
2. **Retry-loop tokens** — token cost of the REPEATED-FAILED-RETRY redundant attempts (2nd-onward, same as the P6 waste-cost definition). Measured.
3. **Context re-send / growth** — THE BIG NEW ONE, and the hardest to define honestly. CC re-sends conversation context every turn; cache_read tokens are the re-sent context (billed at 0.10x). High cache_read relative to fresh input = lots of context being carried. DEFINE a defensible measure: e.g. "context-carry tokens = cumulative cache_read" or "context-GROWTH = the rate at which per-turn input grows over the session." Decide the exact, defensible definition (see design decisions). This must be a REAL measure of re-sent/growing context, labeled precisely — NOT a vibe.
4. **Output tokens** — the agent's actual generation (measured directly).
5. **Productive/residual input** — fresh input tokens NOT attributable to the above. LABELED as residual ("not attributable to detected waste"), never "proven productive."

Reconciliation: buckets must sum to real_tokens (or to total billed tokens — pick ONE basis and be consistent; cost-basis vs efficiency-basis must not be conflated, same as P5). A test asserts the sum reconciles.

## The judge on-demand (B — both paths)

Make trajectory-quality ACTUALLY AVAILABLE, two ways, user picks:
1. **Local Ollama** (exists today) — `--judge` with the local Qwen model. Surface it as a first-class, documented option (currently buried/UNAVAILABLE with no easy on-ramp).
2. **API key (NEW)** — opt-in: the user provides their own API key (env var, e.g. `ANTHROPIC_API_KEY`, or config), and the judge calls the API model to score trajectory. This makes the judge available to users WITHOUT a GPU — the majority.

Constraints on the API judge:
- **OPT-IN, EXPLICIT.** Default stays no-judge (UNAVAILABLE). The user must explicitly enable the API judge AND it must be clear that this SENDS SESSION CONTENT TO THE API (a moat consideration — it's the user's own key/data/call, but data leaves the machine for the judge). Clear consent, like the contribution preview: "Enabling the API judge sends session trajectory data to <provider> using your key. Continue?"
- **The user's OWN key.** tracegauge never ships a key, never proxies through a tracegauge server (there is no server). The call goes from the user's machine directly to the API provider with the user's key. Document this.
- **Same B3 caveats apply** regardless of local-vs-API: positive corroborated, negative model-dependent, no human calibration. The judge VERDICT carries the same domain-of-validity; the API path changes availability, not validity.
- **`tes serve --judge`** (local) and a documented API-judge enablement. The judge-footgun guard stays: background judging is still opt-in (running it on every session continuously, local or API, has cost/throughput implications — surface them).
- **The API judge must use the SAME judge prompt / scoring rubric** as the validated local Qwen judge (the B1/B3 v3 prompt), so verdicts are comparable. If the API model needs prompt adaptation, that's a re-validation question — flag it; do NOT silently use a different rubric.

## Current state
tracegauge 0.3.1 on PyPI. P1-P7 done. Two install bugs (templates, watcher db_path) found via real-user testing + fixed. The product WORKS end-to-end now (dashboard renders, watcher scores). Self-baseline (P4), cost (P5), waste backfill (P6), contribution export (P7). Judge exists (local Qwen, tiered) but is UNAVAILABLE without a GPU and has no easy on-ramp. Detectors frozen. Reports 01-11 immutable.

## Scope

### In scope
1. **Attribution module** (`tes/attribution.py`): compute the token buckets per session from the digest, reconciling to the total. Each bucket from a defensible observable rule. Residual labeled honestly.
2. **Judge on-demand**: surface `tes serve --judge` (local) properly; ADD an opt-in API-key judge path (user's own key, explicit consent, same rubric, same caveats). Both populate the trajectory axis.
3. **Dashboard surfacing (the diagnostic view)**: per-session attribution breakdown (where the tokens went) + the judge verdict when available. This is the "are they used properly" answer made visible.
4. **Reconciliation test**: buckets sum to the session total; no double-count, no unaccounted tokens.
5. **Honest labeling**: attribution carries DOV ("residual = not-attributable-to-detected-waste, not proven-productive"); judge carries B3 caveats; API-judge consent makes data-leaves-for-judge explicit.
6. **Apply to the real store**: attribution on the existing sessions; report what the breakdown looks like on the user's heavy sessions (e.g. the 1.4M-token infra session — where DID those tokens go?).

### Out of scope
- Inverting the dashboard priorities / the bigger UI redesign (that's the NEXT phase, P9 — noted, parked, the user asked for better UI).
- Corpus transmission/server (still P7-deferred).
- Changing detectors, real_tokens, cost model, self-baseline math.
- Re-validating the judge rubric for a new API model beyond flagging if adaptation is needed.
- Trends (still parked).
- Modifying reports 01-11.

### Hard rules
- ATTRIBUTION IS MEASURED: every bucket from an observable rule; "productive" is residual, LABELED as such, never claimed as proven value. Buckets reconcile to the total (tested).
- API JUDGE IS OPT-IN + EXPLICIT-CONSENT: default no-judge; enabling sends data to the API with the user's own key; clear consent; no tracegauge server, no shipped key.
- JUDGE CAVEATS UNCHANGED by path: B3 domain-of-validity applies to local AND API verdicts. Same rubric/prompt as the validated judge, or flag re-validation.
- MOAT: local scoring still local; the ONLY data-egress is the opt-in API judge, with explicit consent, user's own key, direct to provider. Default install transmits nothing.
- Detectors frozen, reports immutable, no human labels.

## Tech stack
- Python, reuse tes/. Attribution from the digest (per-turn tokens + cache classes + waste events — all present post-P5/P6).
- API judge: a thin client calling the provider's API with the user's key, reusing the existing judge prompt/parse logic (layer2_judge). No new heavy deps beyond an HTTP client already present (httpx).
- pytest: attribution reconciliation, bucket-rule correctness, API-judge opt-in/consent, API-judge uses-same-rubric, no-egress-without-consent.

## Architecture (new/changed)
```
tes/
├── attribution.py      # NEW: token buckets per session, reconciling to total
├── judge.py            # CHANGED: add API-key judge path alongside local Ollama; same rubric
├── score.py            # CHANGED: attach attribution; judge via local OR api per config
├── cli.py              # CHANGED: tes serve --judge (local), API-judge enablement + consent
├── web/                # CHANGED: per-session attribution breakdown + judge verdict view
tests/
├── test_attribution_reconcile.py   # buckets sum to total, no double-count
├── test_attribution_rules.py       # each bucket from its observable rule (re-read = detector turns, etc.)
├── test_api_judge_optin.py         # default off; explicit consent; data-egress only on consent
├── test_api_judge_rubric.py        # API judge uses the SAME prompt/rubric as local
└── test_judge_caveats.py           # B3 DOV on both local + API verdicts
```

## Key design decisions (resolve early, escalate)
1. **Context re-send/growth definition** — the hardest, most important. Options: (a) cumulative cache_read as "context-carry"; (b) per-turn input growth rate (how fast context balloons); (c) "context efficiency" = useful-output per context-token-carried. Pick the MOST DEFENSIBLE, observable one and label it precisely. This is the bucket most prone to becoming a vibe — escalate the definition for consultant review BEFORE building the breakdown.
2. **Attribution basis** — cost-basis (dollars per bucket) or token-basis (tokens per bucket)? Recommend token-basis for the breakdown + show dollars alongside (reuse P5). Be consistent; don't conflate.
3. **"Productive" labeling** — confirm the residual is labeled "not attributable to detected waste," never "productive." The DOV wording matters.
4. **API judge provider/model** — which API model? Recommend the user's choice with a sensible default; but it must run the SAME rubric as validated. If the default API model would give materially different verdicts than the validated Qwen, that's a flag, not a silent swap.
5. **API judge consent UX** — exact consent copy (data leaves for the judge, your key, your call). Mirror the P7 contribution-preview honesty.
6. **Background API judge** — should `tes serve --background-judge` allow the API path? Cost implications (every session hitting the API). Recommend: allowed but with a clear cost warning, OR background stays local-only and API is for on-demand `tes score --judge`. Decide.

## Verification commands
```yaml
- name: attribution-reconciles
  cmd: python -m pytest tests/test_attribution_reconcile.py -v   # buckets sum to total
  required: true
- name: attribution-rules
  cmd: python -m pytest tests/test_attribution_rules.py -v        # each bucket from its observable rule
  required: true
- name: api-judge-optin
  cmd: python -m pytest tests/test_api_judge_optin.py -v          # off by default, consent required, no egress without it
  required: true
- name: api-judge-rubric
  cmd: python -m pytest tests/test_api_judge_rubric.py -v         # same rubric as validated judge
  required: true
- name: detectors-frozen
  cmd: git diff --exit-code tes/_waste_detectors.py && echo frozen
  required: true
- name: full-suite
  cmd: python -m pytest -q
  required: true
```

## Escalation rules
- The CONTEXT RE-SEND/GROWTH bucket definition: escalate for consultant review BEFORE building — it's the bucket most likely to become a meaningless-but-plausible number.
- If "productive" can't be cleanly defined as residual: escalate rather than claim positive value.
- If the API judge would need a DIFFERENT prompt/rubric than the validated Qwen judge (giving non-comparable verdicts): escalate — do not silently swap rubrics.
- If attribution can't reconcile to the total: STOP — unaccounted/double-counted tokens mean the breakdown is wrong.
- API egress only ever on explicit consent; if any code path could send data without consent: STOP.

## Budget
- Soft: 3-5 CC sessions. Local/$0 for attribution. API judge testing uses the user's key (minimal — a few test sessions); confirm before any real API calls in testing.

## Success criteria (verify ALL before done)
- Attribution breaks each session into reconciling buckets (re-read, retry, context-carry, output, residual-productive), each from an observable rule, summing to the total (test passes).
- "Productive" is labeled residual ("not attributable to detected waste"), never claimed as proven value.
- Judge available via BOTH local Ollama (`tes serve --judge` surfaced properly) AND opt-in API key (user's own key, explicit consent that data leaves for the judge, no tracegauge server/key).
- API judge uses the SAME validated rubric as the local judge; B3 caveats on both (tests pass).
- Default install: no judge, no egress. API judge only on explicit opt-in + consent (test passes).
- Dashboard shows the per-session attribution breakdown + judge verdict — the "where did my tokens go / was it justified" diagnostic.
- On the real store: report the attribution breakdown for a heavy session (the 1.4M infra one) — where DID the tokens go?
- Detectors frozen, full suite green, reports 01-11 untouched, git clean. New version (0.4.0 — minor, real features), clean-room, user publishes.

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md + reports 01/06/09 (judge validation) + 10 (waste) + spec.md + tes/cost.py + tes/judge.py + the digest schema. Internalize: attribution-measured-not-guessed, residual-not-productive, API-judge-opt-in-same-rubric.
2. DESIGN the attribution buckets, especially the context-re-send/growth definition. Write the exact observable rule for each bucket. HOLD for consultant review of the DEFINITIONS before building (this is the meaningful-number gate).
3. Build attribution.py + reconciliation test + per-bucket rule tests. HOLD for consultant read of the breakdown on a REAL heavy session.
4. Judge on-demand: surface local --judge; add opt-in API-key path (same rubric, explicit consent, no egress without consent). Tests: opt-in, consent, same-rubric, caveats.
5. Dashboard: per-session attribution breakdown + judge verdict. Honest labeling throughout.
6. Apply to real store; report attribution on heavy sessions. New version, clean-room, full suite. HOLD for consultant read before P8 done + user publish.
