# Project Spec: tracegauge — Cost Translation (Iteration P5)

## Goal

Translate the token axis into dollars: every scored session shows its actual API cost AND how that compares to the user's own efficient baseline — e.g. "this debug-fix session cost ~$4.20, about 62% above your typical efficient run (~$2.60)." This is the "so what" layer that makes the self-baseline (P4) speak the language every developer understands: money.

Cost is computed from MEASURED tokens (it's the one axis that can be near-exact), so the discipline is to make it ACTUALLY exact — real per-model rates, correct cache accounting (read vs creation), per-turn pricing — not "roughly right." A developer will check this against their actual bill; it must hold up.

## The honesty constraint (cost must match reality)

Cost is the most checkable number tracegauge produces. Therefore:
- **Per-turn, per-model pricing.** CC sessions can mix models (Opus/Sonnet/Haiku across turns). Price each turn at ITS model's rate, not a blanket assumption.
- **Correct cache accounting — three distinct token classes, three rates:**
  - fresh input → full input rate
  - cache READ (reused cached content) → ~10% of input rate (90% discount)
  - cache CREATION (first-time caching) → a PREMIUM: 1.25× input (5-min cache) or 2× input (1-hr cache)
  - output → full output rate
  CC's raw logs record `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` separately per assistant turn. The cost model MUST use all four, not the P4 efficiency-measure's simplification (which nets cache_read out for a DIFFERENT purpose). Cost ≠ the real_tokens efficiency measure — they're computed differently and must not be conflated.
- **Honest about unknowns.** If a turn's model string is missing, or cache-creation cache-duration (5min vs 1hr) can't be determined, state the assumption used and flag the cost as approximate for that session rather than silently guessing. Default cache-creation to the 5-min (1.25×) rate unless the logs indicate otherwise, and SAY that's the assumption.
- **Bundled prices go stale.** The price table is bundled with a "prices as of <date>" stamp and is user-overridable. The displayed cost notes the price-table date so a user knows if it's current. Stale-but-labeled beats wrong-and-silent.

## Bundled default price table (verify against current Anthropic pricing at build time)
Per million tokens, standard rates (the executor MUST re-verify these against Anthropic's official pricing page at build time — do not trust this spec's numbers blindly; they're a starting point as of June 2026):
- Opus 4.x (4.6/4.7/4.8): $5.00 input / $25.00 output
- Sonnet 4.6: $3.00 input / $15.00 output
- Haiku 4.5: $1.00 input / $5.00 output
- Cache read: 10% of the model's input rate (90% discount)
- Cache creation: 1.25× input rate (5-min) default; 2× input rate (1-hr) if determinable
- Legacy models (Opus 4.1 $15/$75, Haiku 3 $0.25/$1.25, etc.): include a reasonable legacy table; price unknown/old model strings at the closest known rate and flag as approximate.
Model-string matching must be tolerant (e.g. "claude-opus-4-7-20260416" → Opus 4.x rate) and fall back gracefully (unknown → flag approximate, use a stated default).

## Current state
See CURRENT_STATE.md. tracegauge 0.2.0 published. P1-P4 complete:
- Self-baseline (P4): token axis scored vs the user's own lean waste-free runs per task type. baseline_source tracked. 89% of content sessions get a self-baseline verdict.
- SQLite store has per-session real_tokens + ThreeAxisResult; the DIGEST has per-turn token data (CC to verify the cache-class breakdown + model string survive into the digest).
- `tes serve` watcher + dashboard. Moat: local-only. Detectors frozen. Reports 01-11 immutable.

## Scope

### In scope
1. **Cost model** (`tes/cost.py`): per-turn, per-model, cache-class-correct dollar computation over a session's digest. Returns session_cost_usd + a breakdown (input/output/cache-read/cache-creation $ and tokens), + an `approximate` flag + the reason if approximate.
2. **Cost vs self-baseline:** compute the dollar cost of the user's self-baseline median + band for the task type (same lean-subset sessions, priced), so a session's cost can be shown as "$X, N% above/below your typical efficient run (~$Y)." The comparison anchors on P4's self-baseline, in dollars.
3. **Bundled price table** (`tes/data/prices.json`): current rates + "as of" date + cache multipliers + legacy table, user-overridable via config/CLI/env (`--price-table <path>` or `~/.tes/prices.json`).
4. **Surfacing:** per-session cost + the baseline-relative framing in CLI output and dashboard; the price-table date shown; the approximate flag surfaced when set.
5. **Store:** persist session_cost_usd + breakdown so the dashboard/trends don't recompute every read (and so a future trends phase has the series).
6. **Honesty:** cost carries its own domain-of-validity ("computed from measured tokens at <model> rates, prices as of <date>; cache read/creation accounted; approximate when model/cache-duration unknown"). Cost is NOT part of a composite — it's a dollar annotation on the token axis, not a fourth score.

### Out of scope
- Waste-event cost attribution (that's the next phase, P6 — waste prominence — which will use this cost model to price each waste event).
- Trends over time (P7).
- Subscription/plan cost modeling (Max/Pro flat fees) — tracegauge prices the TOKENS as if API-metered; note that a flat-plan user's marginal cost differs, but the token-cost is the honest "what this would cost at API rates" / "what this is consuming." State this framing; don't try to model plan economics.
- Changing the token efficiency measure, detectors, judge, or self-baseline math.
- Any network/data egress. Prices are bundled, not fetched at runtime.
- Modifying reports 01-11.

## Tech stack
- Python, reuse `tes/`. Cost computed from the digest's per-turn token+model data (CC verifies the digest preserves: per-turn input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, model string — if the digest dropped any, that's a digest-enrichment sub-task, escalate).
- prices.json bundled as package-data (like cc_baselines.json).
- pytest: cost math correctness (a known token+model breakdown → exact expected dollars), cache-class accounting, per-turn mixed-model, approximate-flagging, price-override, baseline-cost comparison.

## Architecture (new/changed)
```
tes/
├── cost.py             # NEW: per-turn per-model cache-correct cost; session cost + breakdown + approximate flag
├── data/prices.json    # NEW: bundled price table (rates, as-of date, cache multipliers, legacy)
├── score.py            # CHANGED: attach session_cost_usd + breakdown to the result (annotation, not a score)
├── self_baseline.py    # CHANGED: expose the self-baseline band IN DOLLARS for the comparison
├── store.py            # CHANGED: persist session_cost_usd + breakdown
└── web/                # CHANGED: show cost + "N% vs your efficient run" + price-date + approx flag

tests/
├── test_cost_math.py            # NEW: exact-dollar correctness incl. cache read/creation, mixed-model
├── test_cost_approximate.py     # NEW: unknown model / unknown cache-duration -> flagged, not silent
├── test_price_override.py       # NEW: user price table overrides bundled
└── test_cost_vs_baseline.py     # NEW: "N% above your efficient run" computed correctly
```

## Key design decisions (resolve early, escalate)
1. **Digest sufficiency (verify FIRST):** does the stored digest preserve per-turn cache_creation / cache_read / model? If the digest only kept summed real_tokens, cost can't be computed accurately from the store — CC must check and, if needed, enrich the digest (re-adapt from source JSONL). Report what the digest actually has BEFORE building cost.py. This gates everything.
2. **Cache-creation duration:** can the logs distinguish 5-min vs 1-hr cache writes? If not, default to 5-min (1.25×) and flag the assumption. State what's determinable.
3. **Baseline cost comparison basis:** compare a session's cost to the self-baseline MEDIAN cost (point estimate, "62% above your typical") and/or show the band ($Ylo–$Yhi). Recommend: show both — the % vs median as the headline, the band as context. Relative framing, never absolute "efficient/inefficient."
4. **Flat-plan framing:** how to phrase cost for Max/Pro users whose marginal token cost is $0. Recommend framing as "API-equivalent cost / token consumption" with a one-line note, not a claim about their actual bill. Decide the copy.
5. **Approximate threshold:** if >X% of a session's turns are approximate-priced (unknown model), flag the whole session cost approximate. Pick X.

## Verification commands
```yaml
- name: cost-math-exact
  cmd: python -m pytest tests/test_cost_math.py -v   # known breakdown -> exact dollars, cache read+creation correct
  required: true
- name: cost-approximate-honest
  cmd: python -m pytest tests/test_cost_approximate.py -v   # unknowns flagged, not silently guessed
  required: true
- name: price-override
  cmd: python -m pytest tests/test_price_override.py -v
  required: true
- name: cost-vs-baseline
  cmd: python -m pytest tests/test_cost_vs_baseline.py -v
  required: true
- name: detectors-frozen
  cmd: git diff --exit-code tes/_waste_detectors.py && echo frozen
  required: true
- name: full-suite
  cmd: python -m pytest -q
  required: true
```

## Escalation rules
- VERIFY DIGEST SUFFICIENCY FIRST (decision 1). If the digest lacks per-turn cache-class/model data, escalate before building — cost accuracy depends on it.
- RE-VERIFY bundled prices against Anthropic's official pricing at build time; don't trust the spec's numbers.
- If cost can only be computed approximately for most of the real store (e.g. digests pre-date the needed fields): report honestly and escalate on whether to re-adapt from source or ship cost only for sessions with sufficient data.
- BEFORE conflating cost with the real_tokens efficiency measure: they're different computations — keep separate.
- Detectors/judge/self-baseline math frozen.

## Hard rules
- COST MUST MATCH REALITY: per-turn per-model rates, cache read (10%) + cache creation (1.25×/2×) accounted correctly, output at full. The math is tested to exact dollars.
- HONEST UNKNOWNS: unknown model/cache-duration -> stated assumption + approximate flag, never silent guess.
- PRICE PROVENANCE: bundled prices stamped with an "as-of" date, user-overridable, date shown in output.
- COST IS AN ANNOTATION, NOT A SCORE: no composite; cost annotates the token axis. Relative framing vs the user's own baseline, never absolute.
- MOAT: prices bundled, no runtime fetch, no egress. Reports 01-11 immutable. No human labels.

## Budget
- Soft: 2-3 CC sessions. Local/$0.
- No GCP, no API spend.

## Success criteria (verify ALL before done)
- cost.py computes per-turn, per-model, cache-class-correct session cost; exact-dollar test passes (incl. cache read + creation).
- Mixed-model sessions priced per-turn correctly.
- Unknown model / cache-duration -> approximate flag + stated assumption (test passes).
- Bundled prices re-verified current + "as-of" date stamped; user override works (test passes).
- Per-session cost shown with baseline-relative framing ("$X, N% above/below your typical efficient run $Y"), anchored on P4 self-baseline, in dollars; price-date + approx flag surfaced.
- Cost persisted to the store. Cost carries its own domain-of-validity; not part of a composite.
- On the real store: report a sample of real session costs + the baseline-relative framing, and confirm cost is NOT conflated with real_tokens.
- Detectors frozen, full suite green, reports 01-11 untouched, git clean.

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md + report 08 + spec.md + tes/score.py + self_baseline.py + the adapter/digest code. Internalize: cost-matches-reality, per-turn per-model, cache read vs creation, annotation-not-score.
2. VERIFY DIGEST SUFFICIENCY: does the digest preserve per-turn input/output/cache_creation/cache_read/model? Report exactly what's there. HOLD — if insufficient, we decide enrich-vs-scope before building.
3. RE-VERIFY current Anthropic prices; build tes/data/prices.json (rates, as-of date, cache multipliers, legacy, override mechanism).
4. cost.py: per-turn per-model cache-correct cost + breakdown + approximate flag. Exact-dollar + approximate + mixed-model tests. HOLD for consultant read of the cost math.
5. Wire score.py (cost annotation) + self_baseline.py (baseline cost band) + store (persist). cost-vs-baseline test.
6. Dashboard + CLI: cost + "N% vs your efficient run" + price-date + approx flag, relative framing, no composite.
7. On the real store: sample costs + baseline-relative framing rendered. Full suite green. HOLD for consultant read before P5 done.
```
