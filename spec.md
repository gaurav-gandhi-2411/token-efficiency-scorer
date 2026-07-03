# Project Spec: tracegauge — Live Monitor, Cost Alarm & Habit Coach (Iteration 0.10.0)

## Goal

Expand tracegauge from a post-hoc DIAGNOSTIC into a diagnostic + COACH + ALARM — without losing anything. Everything built stays (honest measurement engine, attribution, self/community baseline, Session Intelligence, the dormant corpus). This phase ADDS the last mile from measurement to action, attacking the pain the market actually screams about:

- **Live monitoring + cost alarm** — watch the ACTIVE session as it runs and warn BEFORE the bill: "you're at $8 / 300K context, mostly re-send — consider /compact." Attacks the "surprise bill / no predictability" pain.
- **Habit coach** — from the attribution + patterns already computed, surface the developer's top FIXABLE habits ranked by $ saved, with the specific action. Attacks the "what do I actually change" pain.
- **Budget / pace tracking** — "at this week's pace you're on track for $X, Y% over your usual." Honest self-trend, not fabricated forecast.

The market pain (grounded in research): the "tokenpocalypse" (June 2026 usage-based billing shift), surprise bills, cost unpredictability, and the finding that the gap between a $20 and $200 month is habits, not work difficulty. tracegauge already MEASURES the root cause (context re-send). This phase turns measurement into PREVENTION + BEHAVIOR CHANGE.

## The central risk — the coach and alarm must be as HONEST as the diagnostic

This is the phase where tracegauge's entire credibility is most at risk, because coaching and alerting are where tools LIE — they over-claim savings, invent recommendations, fabricate predictions, and cry wolf. If the coach says "do X to save $Y" and it's not true, every honest number underneath is now suspect. So the hard rule, non-negotiable:

**Every recommendation, alert, and projection must be grounded in MEASURED data and carry its uncertainty. The coach RECOMMENDS from measured patterns; it never fabricates a saving it can't substantiate. The alarm fires on MEASURED thresholds; it never cries wolf. The projection is the user's OWN measured trend, labeled as a trend not a promise.**

Concretely:
1. **Coach recommendations are grounded + quantified honestly.** "Your context grew to ~400K before compaction in N of your last M sessions; sessions where you compacted earlier cost ~X% less (measured across YOUR sessions)." NOT "compact to save 40%!" (a fabricated universal number). Every recommendation traces to the user's OWN measured data, states N, and carries the "based on your sessions, not a guarantee" caveat. If the data doesn't support a recommendation, the coach says nothing — silence over a made-up tip.
2. **The alarm fires on MEASURED live thresholds, honestly framed.** "This session is at $8.00 and 320K context (92% re-send)" is a MEASURED fact. The suggestion ("consider /compact") is framed as a suggestion, not a command, and only fires when the measured pattern actually warrants it (data-gated, like the P8 hints — silent when there's no real signal). No crying wolf on a normal session.
3. **Projections are the user's OWN trend, labeled.** "At this week's pace ($X so far, N sessions) you're trending toward ~$Y by week's end — based on your last N days, not a forecast of future work." Honest self-extrapolation with its DOV. NOT "you will spend $Y" (a false certainty).
4. **Live cost is ESTIMATED and labeled.** Real-time cost during an active session is an estimate (the session isn't done, prices are as-of-date). Label it "~$X (estimated, in progress)". Never present a live number as final/billed.
5. **The alarm respects the flat-plan reality.** Max-plan users pay a flat fee — marginal token cost is $0 to them. The alarm must know this: for flat-plan users, frame in TOKENS/context (the honest metric) and rate-limit-proximity, not dollars-you-arent-paying. Cost-dollar alarms are for API/usage-based users. This distinction already exists in the cost annotation — carry it into the alarm.

If any recommendation/alert/projection could over-claim a saving, fire without measured basis, present a projection as certainty, or show live cost as final: STOP, escalate. The coach's credibility IS the product's credibility.

## Current state
tracegauge 0.8.0 LIVE (diagnostic engine, attribution, Session Intelligence, dashboard, frictionless UX). 0.9.0 corpus built-but-dormant in repo. The engine measures where tokens go (attribution: context re-send / growth / output / waste / fresh), scores vs self-baseline, clusters sessions, and has a grounded chat that refuses to invent. All of that STAYS. This phase adds the action layer on top.

## Scope
### In scope
1. **Live session monitor** — watch the ACTIVE session file as it grows (the watcher already tails ~/.claude/projects; extend it to score the in-progress session incrementally, not just completed ones). Compute live estimated cost + context size + re-send ratio as the session runs.
2. **Cost/context alarm** — configurable thresholds (cost $, context tokens, re-send ratio); when a live session crosses one AND the pattern warrants it, surface a MEASURED alert with an honest suggestion. Flat-plan-aware (tokens/rate-limit framing vs dollars). Data-gated (no crying wolf). Delivery: terminal notification and/or the dashboard live view; user-configurable, off or on by choice.
3. **Habit coach** — from the existing attribution + Session Intelligence, compute the user's top N FIXABLE habits ranked by measured $ (or token) impact, each with: the measured basis (N sessions, the pattern), the specific action, and the honest "based on your data" caveat. Surface via `tes coach` (CLI) + a dashboard Coach panel. Silent on habits the data doesn't support.
4. **Budget / pace tracking** — track spend/tokens over a rolling window; project the user's OWN trend to period-end with its DOV. `tes budget` + dashboard. Honest self-extrapolation, labeled.
5. **Keep everything** — diagnostic, attribution, self/community baseline, Session Intelligence, chat, dashboard, corpus (dormant), frictionless UX — all unchanged and intact. This is additive.
6. Tests: coach-recommendations-grounded-in-measured-data (no fabricated savings), alarm-fires-only-on-measured-threshold + data-gated + flat-plan-aware, projection-is-labeled-trend-not-forecast, live-cost-labeled-estimated, everything-prior-still-works (regression).

### Out of scope
- Model routing RECOMMENDATIONS that require external model benchmarks (a later horizon — the "general observability" phase; this phase coaches on the user's OWN measured habits, not model-choice advice needing external data).
- Fabricated/universal savings numbers ("save 40%") — only measured-from-your-own-data figures.
- Predicting future WORK or costs beyond honest self-trend extrapolation.
- Activating the corpus (still dormant, separate gated decision).
- Any change to the frozen engine (attribution/cost/detectors/self-baseline math) — the action layer CONSUMES measured data, never alters it.
- Reports 01-11.

### Hard rules
- COACH/ALARM/PROJECTION grounded in MEASURED data + carry uncertainty; no fabricated savings, no crying wolf, no false-certainty forecasts, live cost labeled estimated. Silence over a made-up tip.
- FLAT-PLAN AWARE: token/rate-limit framing for flat-plan users, dollars for usage-based. Never alarm a Max user about marginal dollars they don't pay.
- ADDITIVE: everything prior stays intact + working (tested). Engine/detectors/reports frozen (git diff _waste_detectors.py empty).
- Live monitoring is LOCAL (reuse the watcher; no new egress). Alerts are local. No corpus activation.
- import-closure green (any new dep declared); publish-immediately.

## Tech stack
- Python, tes/. Live monitor extends tes/watcher.py (already tails the session dir + has incremental scoring) to score the IN-PROGRESS session. Cost/attribution reuse the frozen engine on the partial session. Coach reuses attribution + tes/intelligence. Alerts: terminal (stderr/notification) + dashboard live view (SSE or poll — server-rendered, no SPA). Budget: rolling window over the store.
- No heavy new deps expected (reuse httpx/flask/the engine). A desktop-notification lib MIGHT be proposed for the alarm — escalate before adding; a terminal print + dashboard indicator is the zero-dep default.
- pytest: grounding/honesty guards for coach+alarm+projection, flat-plan-awareness, regression on all prior features.

## Architecture
```
tes/
├── watcher.py          # EXTEND: score the in-progress session incrementally (live)
├── live_monitor.py     # NEW: live estimated cost/context/re-send for the active session
├── alarm.py            # NEW: threshold config + data-gated, flat-plan-aware, measured alerts
├── coach.py            # NEW: top fixable habits ranked by measured $ impact, grounded + caveated
├── budget.py           # NEW: rolling-window spend/token tracking + honest self-trend projection
├── cli.py              # + tes coach, tes budget, tes monitor (live), alarm config
├── web/                # + a live monitor view + Coach panel + budget/pace view (honest labels)
tests/
├── test_coach_grounded.py       # every recommendation traces to measured data; no fabricated savings; silent when unsupported
├── test_alarm_measured.py       # fires only on measured threshold; data-gated (no cry-wolf); flat-plan-aware
├── test_projection_labeled.py   # projection is labeled self-trend + DOV, never false certainty
├── test_live_cost_estimated.py  # live cost labeled estimated/in-progress, never final
└── test_prior_features_intact.py# diagnostic/attribution/intelligence/dashboard all still work (regression)
```

## Verification commands
```yaml
- name: coach-grounded
  cmd: python -m pytest tests/test_coach_grounded.py -v      # no fabricated savings; measured basis; silent when unsupported
  required: true
- name: alarm-measured
  cmd: python -m pytest tests/test_alarm_measured.py -v      # measured threshold, data-gated, flat-plan-aware
  required: true
- name: projection-labeled
  cmd: python -m pytest tests/test_projection_labeled.py -v  # self-trend labeled, not forecast
  required: true
- name: prior-intact
  cmd: python -m pytest tests/test_prior_features_intact.py -v  # everything before still works
  required: true
- name: detectors-frozen
  cmd: git diff --exit-code tes/_waste_detectors.py && echo frozen
  required: true
- name: import-closure
  cmd: python -m pytest tests/test_all_tes_imports_are_declared.py -v
  required: true
- name: full-suite
  cmd: python -m pytest -q
  required: true
```

## Escalation rules (autonomous mode — escalate ONLY these)
- PUBLISHING to PyPI (irreversible; user's token).
- Any coach recommendation / alarm / projection that could OVER-CLAIM a saving, fire without measured basis, or present a forecast as certainty — escalate the honesty design BEFORE it ships (this is the phase's central risk; the grounding design for coach+alarm gets consultant review).
- A new dependency (desktop-notification lib, etc.) — declare + import-closure; escalate the choice (prefer zero-dep terminal+dashboard).
- Touching the frozen engine/detectors/reports, or activating the dormant corpus — out of scope, escalate.
- Otherwise DECIDE AND ACT: thresholds defaults, alert UX, coach phrasing, dashboard layout, budget window — your call; report.

## Budget
- $0 (all local — live monitor reuses the watcher, alerts local, coach/budget over the local store). No corpus, no backend.

## Success criteria (verify ALL)
- Live monitor: the active session is scored incrementally; live estimated cost + context size + re-send ratio available while it runs, labeled "estimated / in progress."
- Alarm: fires on measured thresholds, data-gated (silent on normal sessions), flat-plan-aware (tokens/rate-limit for flat-plan, dollars for usage-based), honest suggestion framing. User-configurable, off by default or opt-in.
- Coach: `tes coach` + dashboard panel surface top fixable habits ranked by MEASURED impact, each with its basis (N, pattern), specific action, and "based on your data" caveat; silent on unsupported habits. NO fabricated/universal savings.
- Budget/pace: rolling-window tracking + honest self-trend projection labeled with its DOV; never false certainty.
- EVERYTHING PRIOR intact + working (diagnostic, attribution, self/community baseline, Session Intelligence, chat, dashboard, corpus-dormant, frictionless UX) — regression tested.
- Engine/detectors/reports frozen; import-closure green; local-only (no egress, no corpus activation); full suite green.
- Built, clean-roomed (--no-default-packages: coach/alarm/live-monitor/budget work from the wheel; prior features intact), PUBLISHED, fresh-install confirmed.

## Build order (orchestrator decides reversible details; the coach/alarm honesty design is escalation-gated)
1. Read CURRENT_STATE.md + spec.md + tes/watcher.py + the attribution/cost engine + tes/intelligence. Confirm context + the coach/alarm-must-be-as-honest-as-the-diagnostic boundary in 5-7 lines.
2. DESIGN the coach + alarm + projection honesty: exactly how each recommendation is grounded in measured data, how savings are quantified (measured-from-own-data only), the data-gating for the alarm (no cry-wolf), the flat-plan-awareness, the projection labeling. HOLD — escalate this design for consultant review BEFORE building (the honesty of the action layer is the central risk; the grounding gets reviewed before code).
3. Build the live monitor + alarm (measured, data-gated, flat-plan-aware) + tests. HOLD — show consultant: a real live-session monitor run (estimated cost labeled, alarm firing on a genuinely heavy session AND staying SILENT on a normal one — the no-cry-wolf proof).
4. Build the coach + budget (grounded recommendations, honest projection) + tests. HOLD — show consultant: `tes coach` on the real store (real habits, measured basis, caveats, silent-when-unsupported) + a projection labeled as trend.
5. Dashboard: live monitor view + Coach panel + budget/pace view (honest labels throughout). Regression-confirm all prior views intact.
6. Full suite + prior-intact regression + import-closure + detectors frozen + clean-room. Bump 0.8.0 -> 0.10.0, CHANGELOG. ESCALATE the publish.
