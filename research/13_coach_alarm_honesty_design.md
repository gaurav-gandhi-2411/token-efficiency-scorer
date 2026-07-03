# 13 — Coach, Alarm & Projection Honesty Design (0.10.0)

Status: **REVIEWED AND APPROVED — cleared to build.** Build Order step 2 of
spec.md (0.10.0). This is the mandatory hold: the action layer
(coach/alarm/projection) is where tracegauge's credibility is most exposed, so
the grounding design was reviewed before a line of `coach.py`/`alarm.py`/
`budget.py` was written.

## Review decisions (all four recommendations approved as proposed)

1. **H4 (compaction-timing habit) deferred** — ship H1–H3 now; investigate the
   compaction signal as a fast-follow rather than block or fabricate detection.
2. **Flat-plan framing: always show both $ and tokens, reorder-only on
   config** — dollar figure never fully hidden from Max-plan users, just
   demoted to a parenthetical when `plan=max` is explicitly set.
3. **"Rate-limit-proximity" dropped**, substituted with
   context-size-relative-to-the-user's-own-history (already buildable from
   self-baseline data).
4. **Alarm magnitude gate: self-baseline p75 only** — no separate user-settable
   absolute $ ceiling; stays consistent with the existing `self_baseline.py`
   honesty precedent, no arbitrary invented threshold.

Proceeding to Build Order step 3: live monitor + alarm + tests.

## Addendum (2026-07-04) — pre-publish real-data review: COACH HELD, alarm+budget SHIP

Before publish, ran `tes coach`/`tes monitor`/`tes budget` against the verifying
developer's real store (not just synthetic tests) specifically to check whether
the coach's surviving recommendations (H1-H3, after H4 was deferred above) were
genuinely useful or thin filler that only looked fine because H4 was removed.
Verdict: **thin. Coach is held from 0.10.0** — `tes coach` and the `/coach`
dashboard route are NOT wired into this release; `tes/coach.py` and
`web/templates/coach.html` stay in the repo, unshipped, for a future fix pass.

**What the real-data check found:**
- **H1 never fires — not a low-N issue, a threshold-calibration issue.** Checked
  the actual resend-ratio split behind the fixed 60% threshold, per task type, on
  the real store: every single scoreable session across all 5 task types was
  above 60% resend (`high=N, other=0` for every type). There is no low-resend
  comparison group at all for this usage pattern (long iterative sessions, cache-
  heavy by nature) — the FIXED threshold doesn't discriminate. A threshold
  relative to the user's OWN resend distribution (e.g. their own top vs. bottom
  half) might discriminate where a fixed absolute one can't. Parked for the
  future pass, not solved here.
- **H3 fires but hides the real finding.** Checked whether "above-band sessions
  cost more" is near-tautological (bigger sessions naturally cost more). It
  isn't quite: above-band ml-eval sessions were 5.9x bigger in tokens but 8.6x
  more expensive; infra-deploy was 8.6x bigger but 10.7x more expensive — above-
  band sessions are disproportionately LESS $-efficient per token, not just
  longer. That's real, non-tautological signal. But the shipped message text
  never says this — it says "cost more... no single action attached" and punts
  to the session detail pages, which is why it reads as filler even though
  there's a real finding underneath. Fix: state the disproportionate-$/token
  finding explicitly, or drop the habit in its current generic form.
- **The one genuinely actionable habit (H2) gets buried.** H2 (recurring
  RR/RFR waste, with a specific "stop when a command fails twice" action) is
  real and specific, but tiny in raw $ terms (~$2.60 total on the real store)
  and ranks last (6th of 6) under pure-$-impact sorting — invisible under the
  default `top_n=3`. Fix: ranking must not pure-sort by raw $ impact; an
  actionable low-$ habit should outrank a repeated generic higher-$ one.
- **Gate-2 (alarm cause gate) has the same root cause as H1's problem** —
  checked 74 real above-p75 sessions for one that wasn't resend-dominant and
  found zero. Doesn't block shipping the alarm (gate 1, the user's own p75, is
  doing real and correctly-discriminating work, verified silent on real below-
  p75 sessions even at 96% resend ratio) — but the alarm's own module docstring
  (`tes/alarm.py`) now carries this caveat so a future reader doesn't assume
  both gates are equally load-bearing for every usage profile.

**Decision:** ship 0.10.0 as live monitor + alarm + budget only. The coach's
credibility risk (a thin default view read by a curious user as "the whole tool
is shallow") outweighs shipping it behind a quiet flag — a curious user finds
`tes coach` regardless, so "hidden but present" isn't materially safer than
"present." Cleanest and most honest: don't ship a feature that's being held.

## 0. What's already there vs. what this phase adds

Reused, unchanged:
- `tes/attribution.py` — 6-bucket token attribution (context re-send / growth /
  output / fresh / RR waste / RFR waste), frozen math.
- `tes/cost.py` — per-turn dollar cost, `domain_of_validity` already states
  "API-equivalent token cost; flat-plan users' marginal cost differs" (report.py
  already prints this caveat verbatim today).
- `tes/self_baseline.py` — per-task-type lean-subset p25/median/p75, with an
  honest `'building'` state when `lean_n < min_lean_n` (never fabricates a band
  from insufficient data).
- `tes/intelligence/anomaly.py` — the existing "no cry-wolf" precedent: Tukey
  outer fence computed **per-cluster from the user's own distances**, not a
  fixed arbitrary threshold. This is the template the alarm design below copies.
- `tes/store.py` — SQLite ledger; `session_cost_usd`, `waste_event_count`,
  `waste_events` (JSON, includes proof turns and per-event cost), `band_verdict`,
  `real_tokens`, `task_type`, `scored_at`, `turn_count` all persisted per session.

Gap found while reading for this design (documented here so it isn't silently
assumed away):

1. **Attribution buckets (context-resend tokens, context-growth tokens) are NOT
   persisted per session** — only computed on demand from the reconstructed
   digest (`compute_attribution(digest, ...)`). The store has no
   `context_resend_tokens` column.
2. **No confirmed compaction-event marker.** I grepped this machine's real CC
   session JSONLs (`~/.claude/projects/**/*.jsonl`) for `isCompactSummary` /
   `compactSummary` / a compact `subtype` field. Every hit was a false positive —
   substring matches inside file content the sessions happened to read/write
   (including, amusingly, my own grep commands echoed back in this session's own
   transcript). I found **no reliable, schema-level signal for "the user ran
   /compact here."**
3. **No rate-limit signal anywhere in the local transcript.** Grepped for
   `rate_limit`/`ratelimit` — all hits were source-code content from unrelated
   projects (a `rate_limit.py` file, a `RateLimitError` import, etc.), not HTTP
   response headers or usage-API data. tracegauge is local-only (no egress) by
   hard rule, so there is no path to a real rate-limit-proximity number without
   either a marker that doesn't seem to exist or new egress that isn't allowed.

Both gaps changed what I'm proposing below relative to the spec's illustrative
examples. I did not want to design around data we don't actually have and call
it "measured" — that would be exactly the fabrication the hold exists to catch.

## 1. Habit Coach

### 1.1 Habit catalog — only types with a confirmed measured signal

| Habit | Signal | Source |
|---|---|---|
| H1 — high context re-send | `context_resend_tokens / total_billed_tokens` per session | `tes.attribution.compute_attribution`, computed on demand from `source_path` (see 1.3) |
| H2 — recurring waste (RR/RFR) | `waste_event_count`, `waste_events[].wasted_cost_usd` | already persisted, zero new engine work |
| H3 — sessions above your own baseline band | `band_verdict` (self-baseline) vs. session cost | already persisted (`band_verdict`, `session_cost_usd`) |
| H4 — compaction timing | **DEFERRED** — see 1.4 | not shippable honestly yet |

Each shipped habit (H1–H3) is a template of this shape, always in this order:

```
[BASIS]  measured over N of your last M {task_type} sessions
[FACT]   the specific measured number(s)
[ACTION] the one concrete thing to do
[CAVEAT] "based on your own sessions — not a guarantee for future sessions"
```

Concrete examples (numbers illustrative, always computed live, never hardcoded):

- H1: *"In 7 of your last 12 `infra-deploy` sessions, context re-send was
  >60% of billed tokens (measured). Those 7 sessions cost ~$4.10 on average vs.
  ~$1.30 for the other 5 (measured across your own sessions — not a guarantee).
  Action: run `/compact` earlier in long sessions of this type."*
- H2: *"REPEATED-FAILED-RETRY waste fired in 4 of your last 15 sessions,
  $2.85 total wasted cost (measured, from the waste events already detected).
  Action: when a command fails twice with the same error, stop and read the
  error instead of re-running it — the detector's own proof turns show this
  pattern each time."*
- H3: *"5 of your last 10 `infra-deploy` sessions scored ABOVE your own
  baseline band; those cost ~$3.20 more on average than in-band sessions
  (measured, your own data, not a guarantee). No specific action attached —
  see the session detail pages for what made them heavier."*

### 1.2 Silence / data-gating rule

`MIN_N_FOR_HABIT = 5` — a habit is only surfaced when the pattern is observed
in ≥5 of the user's own sessions of the relevant scope (task_type where
applicable). Below that, the coach says nothing about that habit — same
"silence over a made-up tip" rule as the spec states, same shape as
`self_baseline`'s `'building'` state (never fabricates a band from `lean_n <
min_lean_n`; just says "not enough data yet"). This threshold is mine to set
per the spec's autonomy grant (thresholds are explicitly listed as decide-and-act);
flagging the number here so it's visible in review, not because it needs
sign-off.

Ranking: compute all supported habits, keep those passing the N-gate, sort by
measured `$` impact (or token impact when cost is `cost_approximate`/unavailable
for that subset), surface top `N_COACH_HABITS` (default 3).

### 1.3 Implementation note (not requesting sign-off, just stating the plan)

H1 needs the attribution split per session, which isn't persisted. Rather than
adding new columns to the frozen-adjacent store schema right now, `tes coach`
computes attribution on demand by re-reading `source_path` for the sessions in
scope (same pattern already used by `backfill_cost`/`backfill_waste` in
`store.py` — re-adapt from source, don't touch the engine). This avoids a
schema migration for a first cut; if `tes coach` proves too slow over large
histories, persisting the buckets is a pure-performance follow-up, not a
correctness question, and would be proposed separately.

### 1.4 H4 (compaction timing) — deferred, not silently dropped

The spec's flagship example ("sessions where you compacted earlier cost X%
less") needs to know *when* compaction happened inside a session. I could not
confirm CC's transcript format exposes this. Two paths forward, neither of
which I want to build without a decision:

- **(a) Investigate further at build time** (Build Order step 4): check newer
  CC versions / other message types (`system`, `queue-operation`) more
  carefully for a real marker before concluding it doesn't exist.
- **(b) Proxy heuristic**: a large drop in a turn's total input tokens
  (fresh + cache_read + cache_creation) relative to the previous AI turn, with
  no session boundary, is circumstantial evidence of a compaction (or a
  `/clear`). If used, it must be labeled **"inferred from a context-size drop,
  not a directly observed compact event"** — never stated as measured fact.

Recommendation: ship H1–H3 in 0.10.0; treat H4 as a fast-follow once (a) or (b)
is resolved, rather than delay the whole phase or fabricate confidence we don't
have. Flagging for the hold because it changes the spec's own headline example.

## 2. Alarm (live monitor)

### 2.1 Live-measurable signals

While a session is in progress (watcher extended to score the in-progress file
before the stability window elapses, read-only, same file-tail mechanism):
- **live estimated cost** — running sum of `compute_turn_cost` over turns seen
  so far.
- **live context size** — the most recent AI turn's total input tokens
  (`fresh + cache_read + cache_creation`) — this is what's being resent right
  now, not a cumulative sum across turns.
- **live re-send ratio** — `cache_read / (fresh + cache_read + cache_creation)`
  on the current turn, i.e. how much of what's being paid for right now is
  re-sent context vs. new.

All three are estimates of an in-progress session and are always labeled
`"~$X (estimated, in progress)"` / `"~N tokens (estimated, in progress)"` —
never presented as final. This mirrors `cost.py`'s existing approximate-cost
labeling convention (`cost_approximate` flag + `domain_of_validity` string).

### 2.2 Data-gating ("no cry-wolf") — reuses self-baseline, not an invented number

Two-part AND gate, both required to fire:

1. **Magnitude gate**: live cost (or live context size, for flat-plan) for this
   task_type exceeds the user's own `self_baseline` `p75` for that type — reusing
   `TypeBaseline.p75` / `compute_baseline_cost_band`, already built. If the
   self-baseline for that type is still `'building'` (`lean_n < min_lean_n`),
   the alarm is silent for that type — same honesty posture as everywhere else
   self-baseline is `'building'`. No fallback to an arbitrary global dollar
   figure.
2. **Cause gate**: context re-send is the dominant component of live cost/size
   (`context_resend > output + fresh_input`, live) — i.e. `/compact` is actually
   a relevant suggestion, not a generic nag on a big-but-legitimately-fresh
   session (e.g. a huge one-shot generation task shouldn't get a "/compact"
   nudge — compacting wouldn't help it).

Only when BOTH gates pass does the alarm fire, framed as a suggestion:

> *"This session is at ~$8.10 (estimated, in progress) and ~320K context
> tokens, 92% of which is re-sent context (measured) — well above your own
> typical `infra-deploy` session (your p75: ~$3.40 / ~140K). Consider
> `/compact`."*

This is the same statistical shape as `anomaly.py`'s per-cluster Tukey fence:
data-driven, per-scope (per task_type here, per-cluster there), and honest
about being relative to the user's own distribution, not a universal claim.

### 2.3 Flat-plan awareness — needs an explicit decision, escalating this specifically

There is **no way to detect billing plan from local transcript data** (nothing
egresses, and CC's local JSONL doesn't carry a plan/subscription field I could
find). Three options, my recommendation is (c):

- (a) Auto-detect — rejected, no signal exists.
- (b) Require explicit config (`tes config set plan=max`, default
  `plan=usage_based`) and switch framing entirely based on it.
- (c) **(recommended) Always show both framings, let configuration only change
  emphasis.** Default alarm message shows dollars AND tokens/context together
  (as in the 2.2 example above) regardless of configuration — this is never
  dishonest to a flat-plan user because the dollar figure is explicitly labeled
  "estimated, API-equivalent" (matching `cost.py`'s existing DOV language) and
  sits next to the token figure they can act on regardless of plan. If the user
  explicitly sets `plan=max` via config, the alarm **reorders** to lead with
  tokens/context and demotes the dollar figure to a parenthetical
  `"(API-equivalent: ~$8.10, not necessarily what you're billed)"` — but the
  underlying gate logic (2.2) and the honesty labels don't change, only display
  order/emphasis.

**Rate-limit-proximity, as named in the spec, is dropped from this design** —
there is no local, honest signal for actual rate-limit state (see gap #3
above), and no egress is allowed to fetch one. The honest local substitute is
context-size-relative-to-your-own-history, which is what 2.1/2.2 already
deliver. I'm calling this out explicitly because it's a scope change from the
spec's literal wording and belongs in this review, not decided unilaterally.

### 2.4 Delivery & default state

Terminal print (stderr, on the watcher's own scan cadence) + a dashboard live
indicator (poll, server-rendered — no SPA, per spec's zero-dep default). Off by
default; opt-in via a config flag (`tes config set alarm=on` or a CLI flag) —
matches project convention of judge-off-by-default-in-background and
opt-in-by-default posture elsewhere (contribution, background judge).

## 3. Budget / pace projection

Rolling window (default 7 days, configurable) over `sessions.scored_at` +
`session_cost_usd` (or `real_tokens` when cost is unavailable/approximate for
too much of the window). Linear extrapolation of the user's own rate over the
window to the window's end, e.g.:

> *"At this week's pace (~$14.20 so far across 6 sessions, Mon–Thu) you're
> trending toward ~$24 by end of week — based on your last 4 days, not a
> forecast of future work; work volume varies."*

Always: states N (sessions/days observed), states the window, ends with the
non-forecast caveat verbatim (fixed suffix, tested for presence, same pattern
as `test_caveats_present.py` checks for DOV substrings today). Never "you will
spend $X." `tes budget` (CLi) + a dashboard pace view.

## 4. Test plan (mirrors spec's listed tests, made concrete)

- `test_coach_grounded.py` — every H1–H3 recommendation string contains: N,
  the measured number, and the caveat suffix; a habit below `MIN_N_FOR_HABIT`
  produces no entry at all (not an entry with N=2); no hardcoded percentage
  appears anywhere in `coach.py` (grep-the-source guard, same spirit as
  `test_dep_closure.py`'s AST walk).
- `test_alarm_measured.py` — alarm fires only when both gates (2.2) pass;
  silent when self-baseline for the type is `'building'`; silent when
  re-send isn't the dominant component even if cost is high; both dollar and
  token framings present in every fired message (flat-plan-safe by
  construction, not by branching logic that could be gotten wrong).
- `test_projection_labeled.py` — output always contains the non-forecast
  caveat and the window/N; never contains a bare "$X" without "trending
  toward" / "based on" framing nearby.
- `test_live_cost_estimated.py` — live cost/context strings always carry
  "estimated" + "in progress"; a completed-session path (existing report.py)
  is unaffected (regression).
- `test_prior_features_intact.py` — full existing suite still green; `git
  diff --exit-code tes/_waste_detectors.py` empty; dashboard/CLI routes from
  0.8.0/0.9.0 still respond.

## 5. Summary of what needs a decision (the actual ask of this hold)

1. **H4 (compaction-timing habit) deferred** — ship H1–H3 now, resolve H4's
   signal question as a fast-follow. OK to proceed on this basis?
2. **Flat-plan framing = always show both, reorder-only on explicit config**
   (2.3, option c) — OK, or do you want (b) (hard branch on config) instead?
3. **"Rate-limit-proximity" dropped** from the alarm's flat-plan framing (no
   honest local signal exists) — replaced by context-size-relative-to-own-history.
   OK to proceed without it, or is there a signal I'm missing?
4. **Alarm reuses self-baseline p75 as its magnitude gate** (2.2) rather than
   a separate configurable absolute threshold — OK, or do you also want a
   user-settable absolute ceiling (e.g. "always alarm past $20 regardless of
   my own baseline") as a belt-and-suspenders option?

Everything else in this doc (habit ranking count, N-floor value, budget window
default, alarm delivery mechanism) is a reversible default under the spec's
own autonomy grant and is stated here for visibility, not sign-off.
