# Cache churn

When Claude Code resumes a prompt it normally *reads* the already-cached prefix at a fraction of the
input price. Sometimes the prefix is not found and the whole thing is *written* again. Those
re-writes are the "cache churn". `tes score` finds them in a session transcript, says how much more
they cost than reading would have, and says what the transcript lets it say about why.

Nothing here is stored: the analysis is computed from the session's JSONL (and its `subagents/`
transcripts) each time you run `tes score` or open the session in the dashboard.

## What counts as an event

The unit is a **chain**: one cache lineage, i.e. the main transcript or ONE subagent transcript. A
subagent's first request does not read its parent's cache, so chains are never mixed. Inside a chain,
turn `t` (one API response, de-duplicated by `message.id`) is an event when all of these hold:

1. `C(t-1) >= 5,000`, where `C = cache_read + cache_creation` of the previous turn (a prefix worth
   preserving existed);
2. `cache_read(t-1) > 0` and `cache_read(t) < 0.5 * cache_read(t-1)` (the read collapsed);
3. `cache_creation(t) > 0.2 * prompt(t)`, where `prompt = input + cache_creation + cache_read` (a
   large share of this prompt was written again).

Turn 0 of a chain has no predecessor and is never an event. A big read of a new file with the old
prefix intact fails condition 2 and is not an event: that is growth, not churn. The constants are
named in `tes/cache_churn.py` (`MIN_PREFIX_TOKENS`, `READ_DROP_RATIO`, `REWRITE_SHARE`). On the
maintainer's own sessions the ratio `cache_read(t) / C(t-1)` was bimodal (nothing between 0.3 and
0.9), so the event set barely moves with the thresholds (the maintainer's unpublished local
measurement: -2.1% / +0.2% events when all three were made stricter / looser together).

**Churn tokens** are `min(cache_creation(t), max(0, C(t-1) - cache_read(t)))`: only the part of the
old prefix that had to be written again, not new conversation content. **Churn dollars** are those
tokens times (the turn's own blended cache-write rate minus the model's cache-read rate), priced with
the same price table `tes cost` uses (per-model read multiplier, the 5-minute / 1-hour write tiers,
long-context tiers). A model that is not in the price table is **unpriced**: its events are counted,
shown as unpriced, and left out of the dollar total, which is then a floor. It is never `$0.00`.

## Causes

Each event is given at most one cause, from the first rule that matches:

| Cause | Rule |
|---|---|
| `COMPACTION` | A compaction marker (`system` record of subtype `compact_boundary`, or a `user` record flagged `isCompactSummary`) lies between the two turns. |
| `MODEL_SWITCH` | The model of turn `t` differs from turn `t-1` (each model has its own cache). |
| `TTL_GAP` | The gap between the two requests exceeded the cache lifetime in force. |

Everything else is reported in an **`UNEXPLAINED`** bucket with its dollars and **no cause claim**.
Other things that can invalidate a prefix (a changed tool set, image eviction, server-side context
handling) leave no marker in the transcript, so the tool does not guess.

The gap is measured start to start (the timestamp of the user or tool-result record that triggered
each request, falling back to the assistant record's own timestamp). The lifetime in force is
inferred from the most recent turn that wrote to the cache and records the 5-minute / 1-hour split
of its writes (`usage.cache_creation`): 1 hour if at least half of its writes were 1-hour writes,
otherwise 5 minutes. Turns without that split are never assumed to be 5-minute writes; if the chain
has no turn with the split at all, the lifetime is unknown (see Limits). A gap is over the lifetime when it is strictly greater than
it (`TTL_GRACE_S` in `tes/cache_churn.py` can add a margin; the default is 0, the documented
boundary). Transcript timestamps are when records were written, not when requests arrived, so the
5-minute edge is blurred over roughly 300 to 360 seconds: on the maintainer's snapshot a 30 s grace
would move 18 of 918 `TTL_GAP` events (about $9 of $584) to `UNEXPLAINED` (the maintainer's
unpublished local measurement).

## Avoidable or not (a judgement)

The output separates a *plausibly avoidable* part from the rest. This is a judgement, not a
measurement, and the output says so:

* plausibly avoidable: a `TTL_GAP` of 5 minutes to 1 hour on a chain whose cache lifetime is 5
  minutes (a longer lifetime would have kept it), and any `MODEL_SWITCH`;
* not avoidable by a setting: a gap of more than 1 hour, and `COMPACTION`;
* neither: `UNEXPLAINED`.

## TTL advice, priced per session

Claude Code's documentation names the controls for the cache lifetime: `promptCacheTtl` /
`CLAUDE_CODE_PROMPT_CACHE_TTL` for the main conversation and `subagentPromptCacheTtl` /
`CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` for everything else (subagents, compaction, forks), each `5m`
or `1h`, in Claude Code 2.1.242 and later (same page, retrieved 2026-10-10). A 1-hour lifetime is not free: 1-hour writes cost 2.0x the
input price instead of 1.25x.

So tracegauge never prints a generic "use 1h". For each session it prices the counterfactual:
`premium` = every 5-minute write in the scope repriced at the 1-hour rate, `saved` = the churn of the
plausibly-avoidable 5-minute-tier `TTL_GAP` events (assumed to become reads), `net = saved - premium`.
The advice appears only when `net > 0` for that session, states the net dollars and its assumptions,
and is withheld when any model in the scope is unpriced. It does not model second-order effects
(another request refreshing an entry, sibling subagents sharing one).

On the maintainer's 81 sessions the aggregate answer for subagents was negative: repricing all 5-minute
subagent writes cost about $480.72 against about $316.76 of avoided re-writes (net about -$163.96), and
the net was positive in only 3 of the 45 sessions that had subagent writes (the maintainer's
unpublished local measurement; a single user and machine). That is why the advice is per session.

## What the dashboard and JSON show

* `tes score`: a short CACHE CHURN section after the waste section, only when there are events.
* `tes score --json`: a `cache_churn` object with a fixed key set, see [JSON_OUTPUT.md](JSON_OUTPUT.md).
* The dashboard session page: a "Cache churn" card with the totals, the split by cause and by scope
  (main chain vs subagents), the largest events with their two proof turns, and any priced advice. It
  says so when the transcript is not available.
* `tes cost` is unchanged.

Every event carries its two proof turns (`turn k-1` and `turn k`): `cache_read`, `cache_creation`,
`cache_creation_1h`, the model, the transcript record position and the API message id, plus the gap in
seconds and the lifetime used. No transcript text is ever included.

## Limits

* The transcript has no per-request lifetime, no cache breakpoints and no system prompt or tool list.
  Changes there show up only as `UNEXPLAINED`.
* Timestamps are record write times; see the blur above.
* The summarisation request of a compaction, and other helper requests that are not written to the
  transcript (titles, forks), are invisible, so compaction cost is understated: only the rebuild
  after it is seen.
* The cache is shared by parallel sessions in the same directory, so a chain's reads can be refreshed
  by a session the transcript does not show. A gap-based cause is a proxy.
* The 1-hour main / 5-minute subagent pattern is what a subscription session writes. A session that
  uses an API key writes 5-minute entries everywhere and will show a different mix.
* Records without the `usage.cache_creation` split (older Claude Code versions, other writers) have
  an unknown cache tier. Their writes are carried as "unsplit" (`unsplit_cache_write_tokens` in the
  JSON), are left out of the 5-minute premium of the advice and out of the lifetime inference. When
  a chain has the split on some turns, the tier comes from those turns (the nearest earlier one, else
  the first later one). When a chain has it on none, the lifetime is unknown: a gap over 5 minutes is
  reported as `UNEXPLAINED` with avoidability `unknown` and a note saying why, never as a `TTL_GAP`
  and never as plausibly avoidable, and no TTL advice is built from it. The dollar figure of such an
  event still prices the re-write at the 5-minute rate, so it is a lower bound if those writes were
  1-hour writes.
* Subagent transcripts are the files `<session>/subagents/agent-*.jsonl`, the same flat set `tes cost`
  reads; a file nested deeper under `subagents/` is in neither the cost nor the churn figures.
* A message that appears in two transcript files of one session is counted once.
* The `/usage` command of Claude Code adds a `Prompt cache (main)` line with the session hit ratio
  and miss count, and that line names a `likely cause` for the last miss when Claude Code can
  identify one (the likely-cause text needs Claude Code v2.1.260 or later; source: [How Claude Code
  uses prompt caching](https://code.claude.com/docs/en/prompt-caching.md), retrieved 2026-10-10).
  It covers the last miss of the main conversation. tracegauge works from the transcript instead
  and adds the history across sessions, dollars per session, subagent chains and the priced
  break-even above.

## Validation status

* **Measured from usage fields:** whether an event happened, its tokens and its dollars are read
  directly from the `usage` numbers in the transcript.
* **Not yet validated:** the *cause*. `TTL_GAP`, `MODEL_SWITCH` and `COMPACTION` are rules applied to
  markers and timestamps. On the maintainer's snapshot all 40 eligible post-compaction turns were events
  and the event rate steps sharply at the documented lifetimes (the maintainer's unpublished local
  measurement), which is consistent with the rules but is not a controlled test. A scripted
  controlled run for the TTL cause and a labelled sample for the other causes are still to be done.
* **Single user:** all numbers above come from one person's sessions on one machine. They show that
  the detector fires and what it costs there, not how common churn is for other users.
* **Tests:** the unit tests use synthetic transcripts with injected gaps, switches and compactions
  (`tests/test_cache_churn.py`). The seeded-injection recall there is a regression check of the code
  on clear-cut synthetic cases: the generator shares the detector's ideas and constants and has no
  near-misses, so a high recall is not an accuracy estimate and says nothing about real sessions.
* **Harder synthetic cases (observations, not tuned):** the same test module plants 240 borderline
  cases per mode (60 seeded chains, 4 each) and records what the detector does. A cache lost after
  an idle gap of 300 to 360 s on a 5-minute chain: 240 of 240 are found and all 240 are `TTL_GAP`
  (every such gap is strictly over 300 s); with `ttl_grace_s=30` they split 118 `TTL_GAP` and 122
  `UNEXPLAINED`, which is the blur of the timestamps showing, not an error rate. A read kept at
  0.40 to 0.499 of the previous read (just below the 0.5 ratio): 240 of 240 found, all
  `UNEXPLAINED` (no marker explains a partial loss). A read kept at 0.50 to 0.60 (just above): 0 of
  240 found. The last two show the threshold, which is a definition: a partial loss on either side
  of 0.5 is the same physical event, and only one side is counted. In the maintainer's real
  sessions no pair fell between 0.3 and 0.9 of the previous prefix, so this edge is rarely
  exercised there (the maintainer's unpublished local measurement).
