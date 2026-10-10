# The live alarm: what it compares against, and how that was chosen

The live alarm (`tes monitor`, `tes serve --alarm`, the watcher) tells you that the
session you are in right now is already larger than your usual, and that re-sent context dominates it, so
`/compact` would help. This page says exactly what "larger than your usual" means, why, and what it costs in
false alarms. It is opt-in: `tes serve` only checks with `--alarm`; `tes monitor` checks the active session once.

## The rule

The alarm fires when BOTH hold:

1. **Magnitude.** The live session's `real_tokens` (input minus cache reads plus output, the same measure as the
   verdict bands) is strictly greater than a threshold taken from **your own recent sessions**.
2. **Cause.** Context re-send is the largest component of the session's billed tokens (re-send > output + fresh
   input), so compaction is relevant. (On the one heavy user this was measured on this condition was true for 76
   of 76 sessions with tokens, so today it removes nothing there; it is kept because it is a correct check for
   one-shot generation-heavy sessions, which should stay silent.)

The threshold is the **p85** of the comparison pool, chosen below. The pool is:

* sessions that ended within the last **30 days**,
* from the **same model era** (the normalized model id that produced most of the session's real tokens, for
  example `claude-sonnet-5` vs `claude-sonnet-5-5`; token scale moves between generations),
* scored by the current adapter (rows from the pre-dedupe adapter, `adapter_version` older than 2, are excluded:
  their tokens are inflated about 2.4x),
* all of the user's own sessions, not the "lean half" the verdict self-baseline uses.

A fallback chain picks the first tier with at least **10** sessions:

| tier | pool | shown as |
|---|---|---|
| `recent_era_type` | recent, same era, same task type | `p85 of your last 30 days of <era> <type> (n=..)` |
| `recent_era` | recent, same era, any task type | `... of <era> sessions` |
| `recent_type` | recent, any era, same task type | `... of <type> sessions` |
| `recent` | recent, any era, any type | `... of sessions` |
| `shipped` | the bundled reference band for the type: upper 95% bound of its p75 | says "not your own history" |
| `disabled` | none of the above qualifies | the alarm is silent and `tes monitor` prints why |

`tes monitor` always prints the threshold and its provenance, or the one-line reason the alarm is disabled, e.g.
`No alarm: alarm disabled: needs at least 10 of your sessions from the last 30 days (have 3, 1 of type ml-eval)
and the bundled reference has no usable ml-eval band.` `--json` carries the same in `alarm_baseline` (see
`docs/JSON_OUTPUT.md`). `tes monitor` exits 3 only when the alarm fired.

Knobs (`AlarmConfig`, all optional; the old two-field constructor still works): `percentile` (0.85),
`window_days` (30), `min_n` (10). `check_alarm(live, self_baseline, config)` without a `threshold` keeps the
previous behaviour (the verdict self-baseline's p75) for existing callers; every shipped entry point passes one.

## Why the old comparison was replaced

The alarm used to compare against `self_baseline`'s p75, which is the p75 of the **lean half** of your waste-free
sessions over all time. That number sits near your overall median, so the alarm fired on most sessions: a
retrospective run over a real user's 50 most recent sessions (Phase 0 set B, old accounting, a stale store) fired
on **40/50 = 80%** (Phase 0 audit, workstream C2, `check_alarm` over `score_live_session`; not re-run here, old
accounting no longer exists). It also mixed model eras, and its rows were inflated by the usage double-count.
Under the corrected accounting on this branch the old design is mostly silent instead: on the same 50 sessions its
self-baseline was active for 1 of 98 checks (the stability guard rejects the wide bands), so it fired 0/50 and,
on a copy of the Phase 0 store, 0/50 because all 1,452 rows are stale. Neither extreme is useful.

## How the percentile was chosen (data)

Setting: Phase 0 set B, the 50 most recent main sessions of one developer. 48 have tokens; one more was
extracted with 0 tokens and one has no assistant usage at all, so 49 sessions were extracted and the 2 that cannot
exceed any threshold count as "no fire" in the denominator of 50. Counts below written "of 49" are over the 49
extracted sessions (the 0-token one is placed in the `recent` tier, giving 9 rather than 8 there). Each finished session is scored once as if it were the live
session, with `score_live_session` and `tes.alarm_baseline.resolve_threshold`, against other sessions only.
**This is a retrospective analogue, not a live alarm**: the monitor checks only the currently active session,
once, with a partial transcript; here the finished session is scored whole, so its token count is its final
count. Two settings: *chronological* (only sessions that ended before the scored one: what was known then) and
*leave-one-out* (all other sessions, recency window mirrored around the scored session). Fire rate, k/50 with
Wilson 95% interval, with the re-send condition:

| percentile | chronological | leave-one-out | top-decile by tokens flagged (chrono / LOO) |
|---|---|---|---|
| p60 | 17/50 = 34% [22, 48] | 17/50 = 34% [22, 48] | 5/5 / 5/5 |
| p70 | 13/50 = 26% [16, 40] | 13/50 = 26% [16, 40] | 5/5 / 5/5 |
| p75 | 12/50 = 24% [14, 37] | 11/50 = 22% [13, 35] | 5/5 / 5/5 |
| p80 | 9/50 = 18% [10, 31] | 9/50 = 18% [10, 31] | 5/5 / 5/5 |
| **p85** | **8/50 = 16% [8, 29]** | **7/50 = 14% [7, 26]** | **5/5 / 4/5** |
| p90 | 7/50 = 14% [7, 26] | 5/50 = 10% [4, 21] | 4/5 / 3/5 |
| p95 | 5/50 = 10% [4, 21] | 2/50 = 4% [1, 13] | 3/5 / 1/5 |

Without the re-send condition every cell is identical (the condition is true for all 48 set-B sessions that have
tokens). A synthetic runaway re-send session (twice the largest session in the pool, 99% re-send) fired in 49/49
contexts at p80 to p95; `tests/test_alarm_baseline.py` also scores a generated 80-turn runaway transcript.

* **Target <= 20%:** met from p80 up. **p85 is the default**: p80 meets it with 18% but sits on the 20% line by
  construction (an own-sessions p80 flags about 20% of that same population), so ordinary sampling noise crosses
  it; p85 flags about 15% by construction, observed 16% and 14%. p90 gives up top-decile sensitivity
  (4/5 and 3/5) for 4 to 6 fewer fires. The Wilson intervals are wide (n = 50, one user): the point estimates are
  under 20% in both settings, the intervals do not exclude 30%.
* **A p75 alarm cannot be <= 20% on the population it was built from.** A percentile of your own sessions
  exceeds that percentile for about (1 - p) of those same sessions by construction, so p75 gives about 25%
  (observed 24% and 22%). Getting under 20% on this user takes p80 or higher; that is arithmetic, not a finding
  about the user.
* **Sensitivity is token-based.** The alarm gates on tokens, so it flags the top decile by tokens (5/5, 4/5) but
  only 1/5 of the top decile by API-equivalent cost: the most expensive sessions are mostly Opus-era sessions with
  moderate token counts. The session that is in both top deciles is flagged at every percentile from p80 to p90
  in both settings (1/1). A cost-aware gate is not part of this change.
* **Window and minimum n.** 7 days left 4 of 49 sessions with no usable history (chronological); 14, 30, 60 days
  and unbounded gave identical results on this one month of data, so the data cannot separate them; 30 days is a
  round, not-starved choice. `min_n` 8 versus 10 changed fire counts by one (10 vs 9 at p80). With ten values a
  p85 is the 9th-smallest: coarse.
* **Eras here:** `claude-sonnet-5` (37 sessions), `claude-opus-5-5` (16), `claude-sonnet-5-5` (23 in the 79
  studied). Era explains about 3% and task type about 9% of the variance of log tokens on this user, so per
  type-and-era pools are mostly too thin (4 of 49 sessions reached tier 1; 24 used `recent_era`).

For comparison on the same 50 sessions: the shipped bands' p75 (`cc_baselines.json`, 48 of the 50 are inside the
population that built it, so in-sample) 7/50 = 14%; the same style of band rebuilt leave-one-out 10/50 = 20%.
Cold start (no history, shipped tier with the upper 95% bound of p75): 3/50 = 6% (active cells cover 40 of the
49 sessions); the plain p75 would be 7/50 = 14%.

## Limits

* One developer, one month, 50 sessions: this is a calibration check, not a validation. The target is a fire rate
  on a heavy user's history, not a measured precision: nothing here says the fired sessions were wasteful.
* Sessions are not independent (one repo's sessions share context), so the intervals are optimistic.
* The synthetic-injection and top-decile checks show sensitivity to large sessions; they say nothing about
  whether `/compact` would have helped.
* A user whose model changes mid-session is assigned the era that produced most of the tokens.
* Pool rows saved before the `dominant_model` column existed have an unknown era and only serve the era-agnostic
  tiers.
