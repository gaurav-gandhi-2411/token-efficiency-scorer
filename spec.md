# Project Spec: tracegauge — Self-Baselining (Iteration P4)

## Goal

Fix the "76%-unavailable" problem by scoring each developer's sessions against **their own** lean, waste-free sessions of the same task type — instead of against the bundled high-waste infra corpus (the B2/B5 single-developer limitation). This makes the token axis informative for most of a user's work, on their own terms, with their data never leaving their machine (moat intact, no judge required).

The measurement model shift: token-economy verdicts become **relative to the user's own better runs** of each task type. "within_band" now means "comparable to your leaner waste-free sessions of this type"; "above_p75" means "heavier than your typical efficient run." This is honest *relative* efficiency — NOT an absolute "good/bad" verdict, and the product must say exactly that.

## The core honesty constraint (read first)

Self-baselining measures "efficient relative to YOUR OWN lean waste-free runs of this type." It does NOT measure:
- Absolute efficiency (no universal "good" exists without a broad multi-developer corpus — that's P-corpus-contribution, later).
- Efficiency vs your *best possible* (only vs your observed lean runs).
- Anything for a task type where you don't yet have enough waste-free sessions (cold-start UNAVAILABLE).

The domain-of-validity string changes accordingly: "Calibrated to YOUR OWN leaner, waste-free sessions of this task type (N sessions). Relative-to-your-own-baseline, not an absolute efficiency verdict." Claim exactly this — no more.

## The two design traps this phase must solve (the heart of P4)

**Trap 1 — "average = efficient" collapse.** Waste detectors fire on only ~1.4% of sessions, so ~98% are "waste-free." If the baseline = median of all waste-free sessions, it becomes "your median session," and "efficient" collapses to "your typical" — rewarding consistency, not efficiency. SOLVED by (a) the lean-subset rule below, and (b) a deterministic outlier exclusion.

**Trap 2 — cold-start.** A new user has no history. The token axis must show a clear "building your baseline (need N more <type> sessions)" state — NOT a broken blank — and optionally fall back to the bundled corpus baseline WITH the honest infra-corpus caveat until the self-baseline exists.

## The self-baseline definition (locked from consultant: option b + lean-subset ii)

For a given user + task type, the self-baseline is computed from the user's own sessions as follows:
1. **Waste-free gate:** include only sessions with ZERO deterministic waste events (RFR + RR). This excludes detectably-wasteful sessions. (Deterministic, GPU-free.)
2. **Outlier exclusion (anti-trap-1 part A):** from the waste-free set, exclude token-count outliers above the user's own distribution (e.g. above their own p90, or > median + k*IQR) — these are likely the wasteful sessions the detectors missed. (Deterministic.)
3. **Lean-subset (anti-trap-1 part B, the locked decision ii):** baseline from the LEANER portion of what remains — e.g. the lower-token half (or lower tertile) of the waste-free, outlier-excluded sessions of that type. "Efficient" = comparable to your better runs, not your average run.
4. **Baseline statistic:** median + [p25, p75] band OF THE LEAN SUBSET (same band approach as B2, computed on the lean subset).
5. **Min-N gate:** require a minimum count of sessions in the lean subset (NOT just total) before the baseline is trustworthy. Below that → token axis UNAVAILABLE ("building your baseline: N more <type> sessions needed"). The min-N must account for the lean-subset being a fraction of total waste-free sessions (so the total-session requirement is higher than B2's raw min-N).
6. **Real-tokens measure:** unchanged from B2 (cache-corrected: sum AI-turn input − cache_read + output). The measure is locked; only the reference population changes.

## Current state

See CURRENT_STATE.md. tracegauge 0.1.0 is published to PyPI. P1-P3 + B1-B5 complete:
- `tes/` SDK + `tes score` + `tes serve` (watcher + localhost dashboard + SQLite store).
- Token axis currently scores against the BUNDLED `cc_baselines.json` (the infra-heavy single-developer corpus) → 76% of real sessions land UNAVAILABLE (scope gate) or are scored against a non-representative reference.
- Deterministic waste detectors (RFR, RR) — GPU-free, byte-verbatim-frozen.
- SQLite store accumulates per-session ThreeAxisResult + the incremental ledger.
- Reports 01-11 immutable. Moat: localhost-only, no data off-machine, redaction on.

## Scope

### In scope
1. **Self-baseline computation** (`tes/self_baseline.py`): from the user's SQLite store, per task type, compute the lean-subset self-baseline per the locked definition above. Recompute incrementally as new sessions accumulate.
2. **Scoring against self-baseline:** the token axis scores a session against the USER'S OWN self-baseline for its task type, when available. Falls back per the cold-start policy when not.
3. **Cold-start policy:** clear "building your baseline (need N more)" UNAVAILABLE state; optional fallback to the bundled corpus baseline WITH the honest infra-corpus caveat (decide default — see decisions).
4. **The anti-trap mechanics:** waste-free gate + outlier exclusion + lean-subset + lean-subset-min-N, all deterministic, all GPU-free, all tested.
5. **Domain-of-validity update:** the token caveat string reflects self-baselining ("relative to YOUR OWN lean waste-free runs, N sessions; not an absolute verdict"). When falling back to corpus, the string reverts to the infra-corpus caveat. The string always states WHICH baseline was used.
6. **Recompute trigger:** the self-baseline updates as the store grows (e.g. on each watcher scan or on a threshold of new sessions). Incremental, not full-recompute-every-time where avoidable.
7. **Dashboard surfacing:** the dashboard shows which baseline a session was scored against (self vs corpus vs building), and a "baseline status" view per task type (how many sessions you have, how many in the lean subset, whether the self-baseline is active yet).

### Out of scope
- Corpus contribution / multi-developer pooled baselines (that's the next phase — P5).
- Cost translation (#1 → P-cost, next).
- Changing the real-tokens measure, the waste detectors, or the judge.
- Any data leaving the machine (self-baseline is computed locally from the user's own store).
- Modifying reports 01-11 or the bundled corpus baseline (keep it as the cold-start fallback).
- Human labels / accuracy claims.

## Tech stack
- Python, reuse `tes/`. Self-baseline computed from the existing SQLite store (the data's already there — per-session real_tokens, task_type, waste_event_count).
- numpy/stdlib statistics for the percentile/lean-subset math.
- pytest: the anti-trap mechanics need rigorous tests (synthetic session distributions proving the lean-subset + outlier exclusion behave correctly; cold-start UNAVAILABLE; min-N gating).

## Architecture (new/changed)
```
tes/
├── self_baseline.py    # NEW: compute per-user per-type lean-subset self-baseline from the store
├── baselines.py        # CHANGED: load_baselines gains a "source" notion (self vs bundled corpus)
├── score.py            # CHANGED: token axis picks self-baseline if available, else cold-start policy
├── store.py            # CHANGED (maybe): query helpers for "all waste-free sessions of type T"
└── web/                # CHANGED: dashboard shows baseline source + per-type baseline status

tests/
├── test_self_baseline.py        # NEW: lean-subset, outlier exclusion, min-N, the anti-trap proofs
├── test_cold_start.py           # NEW: insufficient history -> UNAVAILABLE-building / corpus-fallback
└── test_baseline_source_honesty.py # NEW: domain-of-validity string states which baseline was used
```

## Key design decisions (resolve early, escalate)
1. **Lean-subset cut:** lower-HALF or lower-TERTILE (by real_tokens) of the waste-free, outlier-excluded sessions of a type? Tertile is stricter ("like your best third") but needs more sessions; half is more attainable. Recommend deciding with the consultant after seeing the user's actual per-type session counts in the store (the data tells us what's feasible). Likely: lower-half, with the cut configurable.
2. **Outlier-exclusion rule:** p90-cap, or median+k·IQR? State the rule + why. Must be deterministic and defensible (excludes the obviously-heavy sessions without hand-tuning).
3. **Lean-subset min-N:** how many sessions in the LEAN SUBSET before the self-baseline activates? If lean = lower-half and min-N(subset)=8, that needs ~16+ waste-free sessions of a type. State the number + the resulting total-session requirement, and make cold-start messaging reflect it ("need ~N more debug-fix sessions").
3. **Cold-start fallback default:** when no self-baseline yet, (a) show UNAVAILABLE-building (purest — don't score against a non-representative corpus), or (b) fall back to the bundled corpus baseline WITH the loud infra-caveat (gives *a* number, honestly caveated). Recommend (a) as default with (b) as an opt-in flag — scoring against the infra corpus is what we're trying to move AWAY from, so defaulting to it undercuts the phase. Decide.
4. **Recompute cadence:** recompute the self-baseline on every watcher scan (simple, possibly wasteful) vs on a new-session threshold (e.g. every 5 new sessions of a type) vs cached-with-invalidation. Pick the simplest correct option.
5. **Per-session baseline provenance:** store WHICH baseline (self/corpus/building) each session was scored against, so re-scoring after the self-baseline activates is sensible and the dashboard is honest. (Ties into the store schema.)

## Verification commands
```yaml
- name: lean-subset-correctness
  cmd: python -m pytest tests/test_self_baseline.py -v   # lean-subset + outlier-exclusion + anti-trap proofs
  required: true
- name: cold-start
  cmd: python -m pytest tests/test_cold_start.py -v       # insufficient history -> building/fallback, not blank
  required: true
- name: baseline-honesty
  cmd: python -m pytest tests/test_baseline_source_honesty.py -v  # caveat states which baseline used
  required: true
- name: anti-average-trap
  cmd: python -m pytest tests/test_self_baseline.py -k anti_trap -v  # uniformly-sloppy synthetic user does NOT score "within band" on sloppy sessions
  required: true
- name: behavior-unchanged-detectors
  cmd: git diff --exit-code tes/_waste_detectors.py && echo "detectors frozen"
  required: true
- name: full-suite
  cmd: python -m pytest -q
  required: true
```

## Escalation rules
- BEFORE locking the lean-subset cut + min-N: report the ACTUAL per-type session counts from a real store (the user's own ~700-session store is perfect data) — the feasible cut/min-N depends on how many waste-free sessions per type actually exist. HOLD on the numbers.
- If the anti-average-trap test can't be made to pass (uniformly-sloppy user still scores "within band"): the lean-subset/outlier rules are insufficient — escalate, don't ship a baseline that rewards consistency.
- BEFORE changing the bundled corpus baseline or the real-tokens measure: out of scope — escalate.
- Detectors stay frozen (verification enforces).

## Hard rules
- HONESTY: the token caveat ALWAYS states which baseline was used (self / corpus-fallback / building). Self-baseline claims "relative to your own lean runs," never absolute.
- MOAT: self-baseline computed locally from the user's own store; nothing leaves the machine.
- ANTI-TRAP: the lean-subset + outlier exclusion must prevent "average = efficient" — proven by the anti_trap test (a synthetic uniformly-heavy user must NOT score within-band on heavy sessions).
- COLD-START is a clean "building your baseline" state, never a broken blank.
- Detectors + real-tokens measure frozen. Bundled corpus baseline retained as fallback. Reports 01-11 immutable. No data egress. No human labels.

## Budget
- Soft: 2-3 CC sessions. All local/$0 (computation over the existing SQLite store).
- No GCP, no API spend.

## Success criteria (verify ALL before done)
- Self-baseline computes per user + task type from the store: waste-free gate + outlier exclusion + lean-subset + median/[p25,p75], with lean-subset min-N gating.
- Token axis scores against the self-baseline when available; cold-start policy (building / opt-in corpus fallback) otherwise — never a broken blank.
- Anti-average-trap test passes: a synthetic uniformly-heavy user does NOT get "within_band" on heavy sessions (the lean-subset + outlier exclusion bites).
- Cold-start test passes: insufficient history → clear "building (need N more)" state.
- Domain-of-validity always states which baseline was used; self-baseline string claims relative-to-your-own-lean-runs, not absolute.
- On the user's real ~700-session store: report how many task types now have an ACTIVE self-baseline and what the new unavailable-rate is (the headline metric — did we move it below 76%?).
- Detectors frozen, real-tokens unchanged, full suite green, reports 01-11 untouched, git clean.

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md + reports 08 (B2 baselines) + 11 (corpus limitation) + spec.md. Internalize: relative-not-absolute, the two traps, moat, frozen detectors.
2. PROBE the real store: report actual per-type counts — total sessions, waste-free sessions, and (simulated) lean-subset sizes per task type — from the user's ~700-session store. This data decides the feasible lean-subset cut + min-N. HOLD for the cut/min-N decision.
3. Build self_baseline.py: waste-free gate + outlier exclusion + lean-subset + band + min-N. Unit tests INCLUDING the anti-average-trap synthetic proof. HOLD for consultant read.
4. Wire score.py: self-baseline-if-available else cold-start policy; baseline provenance stored; domain-of-validity string per source. Cold-start tests.
5. Dashboard: show baseline source per session + per-type baseline-status view (sessions, lean-subset size, active/building).
6. Re-score the user's store against self-baselines; report the new unavailable-rate vs 76%. Full suite green. HOLD for consultant read before P4 done.
