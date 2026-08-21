# tracegauge

![tracegauge](assets/brand/badge.svg)

Three-axis efficiency scoring for Claude Code sessions — token economy, trajectory quality, deterministic waste. Local by default — no server, no telemetry, nothing transmitted unless you opt in. Two opt-in paths currently do anything: a local contribution export (content-free, stays on your machine); and an API judge that sends session snippets directly to your model provider on per-session explicit consent. A third capability — community corpus contribution (content-free, would transmit to a tracegauge-operated corpus on explicit consent, in exchange for a cross-developer percentile baseline) — is fully built and tested but **not currently active**: no public corpus is operated, so `tes corpus contribute` sends nothing regardless of consent. See [PRIVACY.md](PRIVACY.md).

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://pypi.org/project/tracegauge/)
[![PyPI](https://img.shields.io/pypi/v/tracegauge.svg)](https://pypi.org/project/tracegauge/)

> **Note — 0.10.2 (live on PyPI since 2026-08-15) was a BREAKING bug-fix release for SDK users.**
> Unknown/unresolvable models used to silently return a guessed price (defaulting to
> `claude-sonnet-4-6`'s rate); they now return an explicit unpriced result instead
> (`priced=False`, `total_usd=0.0`). If you call `tes.cost.compute_turn_cost`/
> `compute_session_cost` directly and read `total_usd`, see the `[0.10.2]` entry in
> [CHANGELOG.md](CHANGELOG.md) for the exact migration path if you're upgrading from
> `0.10.1` or earlier.

---

## Features

tracegauge is feature-complete — the current release bundles the full validated toolchain (B1–B5 research arc + every shipped phase):

- **Self-baseline token scoring** — your sessions are scored against *your own* lean, waste-free sessions per task type, not a one-size-fits-all corpus. Falls back to the bundled reference corpus until your self-baseline activates.
- **Dollar cost attribution** — six reconciling buckets (B1–B6) that split every billed token into where the money actually went; token% and cost% shown side by side so the cache-read divergence (lots of tokens, little cost) is visible.
- **Deterministic waste detection** — frozen, observable-invariant detectors (repeated-failed-retry, redundant-read) with proof turns and per-event wasted cost. No LLM judgment, no false-positive guessing.
- **Trajectory judge** — purposefulness verdict from a local Ollama model ($0, GPU) *or* an opt-in API judge that sends snippets to your model provider only on explicit per-session consent. Renders UNAVAILABLE as a complete, expected state when no judge is configured.
- **Diagnostic dashboard** — `tes serve` runs a localhost-only (`127.0.0.1`) web dashboard that auto-scores finished sessions and shows the three axes, attribution, and waste with every domain-of-validity caveat carried to the surface. No composite/blended score — each axis stands on its own.
- **Community baseline (built, not yet active)** — `tes corpus contribute` would send content-free session aggregates (numbers and categories only — see [PRIVACY.md](PRIVACY.md)) to a tracegauge-operated community corpus, and in return `tes corpus` would show your context-efficiency percentile against other opted-in developers, alongside — never replacing — your own self-baseline. The code, the content-free guard, the consent flow, and `tes corpus withdraw` are all built and tested (601 tests green), but **no corpus is currently provisioned** — until one is, `tes corpus contribute` sends nothing, by construction (see PRIVACY.md).

Local by default: scoring and the dashboard make zero external network calls. The only working egress today is the opt-in API judge (your key, your consent, per session). The community corpus contribution above would be a second opt-in egress path once activated — see PRIVACY.md.

---

## Try it right now — no local judge, no API key, no network call

```bash
pip install tracegauge
tracegauge quickstart
```

Two commands. Scores a bundled sample Claude Code session (token economy, deterministic waste detection, cost annotation) and prints a real three-axis report — nothing read from your machine, no local Ollama probe, no consent prompt. **Measured live, not estimated: 1.2s wall-clock from a genuine fresh `pip install --user` on Windows to the printed report.**

## Quick start

The tool already knows where your sessions live (`~/.claude/projects`). You don't type paths or memorize flags — **just run `tes`**.

```bash
pip install tracegauge

# Just run it — bare `tes` launches the localhost dashboard (http://127.0.0.1:4747/)
tes

# Score your most recent session — no path needed
tes score

# Pick from a list of your recent sessions
tes score --pick

# Run the trajectory judge — auto-detects a local Ollama judge or an API key,
# and guides you to the single simplest setup step if neither is present
tes score --judge
```

That's the whole frictionless path. Power-user / scripting forms still work:

```bash
tes serve                                  # same as bare `tes`, with flags (--port, --cc-path, …)
tes score <path>.jsonl                     # score a specific file
tes score ~/.claude/projects/<project>/    # score every session in a directory
tes score <path> --json                    # machine-readable output
tes --version
```

Bare `tes` (and `tes serve`) start two things: a background scan loop that auto-scores finished Claude Code sessions (token economy + deterministic waste, judge OFF by default), and a web dashboard on `http://127.0.0.1:4747/` where scores accumulate. Session resolution for `tes score` is: explicit PATH > `--pick` > most recent session.

---

## Scope & Limitations

Read this before installing. These are not caveats to hide — they're the honest picture of what the tool measures and where the calibration comes from.

**Corpus caveat (token baselines).** The token economy baselines are derived from one developer's 75 quality-gated Claude Code sessions, skewed toward high-intensity infrastructure and ML-ops work (GCP, Cloud Run, training pipelines). B5 generalization validation across 172 independent developers (1,053 SWE-chat CC sessions) found the generalizable repeated-failed-retry rate is ~1.4% — versus 6.6% in the calibration pool, which is a high-waste infra outlier. A developer doing ordinary coding work may score below-band on the token axis without being inefficient; the baseline encodes "efficient under expert prompting on heavy infra work," not a universal reference.

> Contains information from [SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat) (Baumann et al., 2026, arXiv:2604.20779), made available under the [Open Data Commons Attribution License (ODC-BY) 1.0](https://opendatacommons.org/licenses/by/1-0/). See [DATA_SOURCES.md](DATA_SOURCES.md) for the full attribution and what was derived from it.

**No human accuracy validation.** The trajectory judge (Qwen3-30B) is coherence-validated against a reference LLM (Spearman ρ ≈ 0.79), not calibrated to human expert labels. Positive verdicts (MUCH_BETTER/BETTER) are cross-model corroborated at 84–96%. Negative verdicts (WORSE/MUCH_WORSE) are model-dependent — treat them as a signal to review, not a ground truth.

**Tiered judge.** Token economy and deterministic waste run locally with no GPU and no network — these axes are always available. The trajectory quality axis requires a local Ollama judge (~18 GB VRAM for Qwen3-30B). Without it, trajectory prints UNAVAILABLE, which is the expected complete state for most users, not an error.

**What waste detection covers.** The two waste detectors catch observable-invariant patterns only: exact-match retry loops with no state change, and redundant file reads where the content was unchanged. Judgment-of-progress waste (was this cycle productive? was this approach the right one?) is not covered — that requires human labeling and is out of scope.

**Local by default.** All scoring is local. No telemetry, no phone-home, no external network calls from scoring or the dashboard (except the optional local Ollama endpoint). The localhost bind is enforced by construction, not configuration. The only thing that currently leaves your machine is opt-in and separate from scoring: the API judge (your key, your consent). A second opt-in path, community corpus contribution, is built but not active — see below.

**Optional export (off by default, nothing transmitted).** `tracegauge export-contribution` writes a redacted, content-free local file you inspect and control — numeric token counts, the 5 known task types, detector names, and an opaque random UUID. No code, no prompts, no file paths, no session IDs, no error text, no timestamps. This command itself never transmits anything — the file is yours; the tool never reads it back or uploads it. A separate, further opt-in command, `tes corpus contribute`, is built to send that same content-free data to a tracegauge community corpus in exchange for a cross-developer baseline — but **no corpus is currently provisioned**, so that command sends nothing regardless of consent. See [PRIVACY.md](PRIVACY.md) for the complete field list, the send-time re-verification, the withdrawal path, and the dormancy notice.

---

## The three axes

No composite score. Three independent labeled signals, each with its own domain of validity.

### Token economy

Compares the session's real token count (AI turns only; cache-read inflation removed) against the p25–p75 band for the same task type (ml-eval, debug-fix, infra-deploy, research-recon, feature-build). Verdicts: `above_p75`, `within_band`, `below_p25`, `unavailable`.

`unavailable` when the session is below the per-type p10 turn floor (scope gate) — the session is too short relative to the reference mass to produce a meaningful comparison. Not an error.

**Domain of validity:** calibrated to a high-waste infra/ML-ops corpus (one developer, 75 sessions). Interpret alongside the trajectory verdict.

### Trajectory quality

A local Qwen3-30B judge scores the session's trajectory on purposefulness: `MUCH_BETTER` / `BETTER` / `SIMILAR` / `WORSE` / `MUCH_WORSE`.

Requires a local GPU (~18 GB VRAM). Without the judge, this axis is `UNAVAILABLE` — token and waste axes still run fully.

**Domain of validity:** positive signal cross-model corroborated (B3 report); negative signal is model-dependent. No human gold labels.

Just add `--judge` — the tool detects what's available and does the work:

```bash
tes score --judge
```

What `--judge` does, in order:
1. **Local Ollama judge running?** → use it (free, ~18 GB VRAM; `ollama pull qwen3:30b-a3b` to install — see https://ollama.ai).
2. **No local judge, but `ANTHROPIC_API_KEY` set?** → *offers* the API judge and shows a consent screen. **Nothing is sent until you confirm** — auto-detecting a key never auto-sends data.
3. **Neither?** → prints the single simplest setup step. It never fails cryptically. Token + waste axes always run regardless.

To use the opt-in API judge directly (no GPU needed):
```bash
tes score --api-judge                      # uses ANTHROPIC_API_KEY from the env
tes score <path> --api-judge --api-judge-key YOUR_KEY
# Sends session data — including 300-char snippets that may contain your code — to your provider.
# Uses the same validated v3 rubric. Requires explicit consent per session (shown at prompt).
# The API model is not part of the B3 cross-model corroboration — verdict is indicative.
# See PRIVACY.md for what is sent.
```

> **Consent is never silent.** Frictionless means the tool finds the judge for you — not that it sends your data without asking. Every byte of egress passes the per-session consent screen requiring an explicit `y`. The judge also stays OFF by default in the background watcher (a GPU/cost footgun guard).

### Deterministic waste

Two observable-invariant detectors with proof turns attached to every event:

- **REPEATED-FAILED-RETRY** — same shell command + same error output + no state change between retries. Validated across 172 developers (SWE-chat CC). ~1.4% of ordinary CC sessions; ~6.6% in our calibration pool (a high-intensity infra outlier).
- **REDUNDANT-READ** — same file content read twice with no edit between reads (PATH-A: CC's own "File unchanged" verdict; PATH-B: content-match, gap ≤ 5 turns). Dual-format regex handles both pre- and post-v2.1.38 CC output.

**Domain of validity:** observable-invariant only. Fires conservatively — misses judgment-of-progress waste by design.

---

## Token attribution

The session-detail view in `tes serve` breaks billed token spend into six named buckets — context re-send (cache reads), context growth (cache writes), output, fresh input, redundant-read waste, and retry-loop waste — reconciling exactly to total billed tokens.

Dollar and token percentages are shown side-by-side because they diverge significantly: cache re-reads may be 95% of tokens but only 49% of cost (billed at 0.1×), while output at 1% of tokens can be 30% of cost (billed at full rate). The dollar column is what matters for spend; the token column is what the verdict axis measures. These should not be compared directly.

Attribution is computed from the source JSONL on demand. A deterministic one-line takeaway is generated from the bucket values, with a data-gated lever hint when a bucket genuinely dominates (e.g. "Cost: context (49% re-send + 21% growth) and output (30%); detectable waste $0.15. — a long context drove most of the cost; checkpointing or /compact mid-session reduces re-send.").

---

## `tes serve` — always-available local service

```bash
tes serve [--port PORT] [--scan-interval SECONDS] [--stability-window SECONDS] \
          [--cc-path PATH] [--db-path PATH] [--background-judge]
```

- **Watcher**: scans `~/.claude/projects` every 2 minutes (configurable), scores any session file stable for 5+ minutes (token + waste; judge OFF by default).
- **Dashboard**: `http://127.0.0.1:4747/` — session list, per-session three-axis detail with domain-of-validity notes inline, trend views.
- **Store**: SQLite at `~/.tes/tes.db` (WAL mode; watcher writes and dashboard reads concurrently without locks).
- **Manual scores share the dashboard**: `tes score <path>` results also write to the store.

Moat properties: binds `127.0.0.1` only (never exposed to external interfaces), redaction on by default at ingestion, no external network calls.

To enable the trajectory judge in the background watcher:
```bash
tes serve --background-judge
# WARNING: runs qwen3:30b-a3b (~18 GB VRAM) on your GPU for every new session continuously.
```

---

## `tes cost` — period spend report

```bash
tes cost --week                 # rolling last 7 days
tes cost --month                # rolling last 30 days
tes cost --since YYYY-MM-DD      # from this date through now
```

Total spend, session count, and a per-project breakdown for a period — distinct from `tes budget`'s rolling self-trend *projection* (where your pace is heading); `tes cost` reports what you actually spent. `--week`/`--month` are rolling N-day windows ending now, not calendar-aligned (a calendar week/month needs a timezone and first-day-of-week convention this tool has no basis to guess).

Real output, from the published `tracegauge==0.11.0` artifact, four real seeded sessions across two projects (one older than the 7-day window):

```
──────────────────────────────────────────────────────────────────────
COST -- last 7 days
──────────────────────────────────────────────────────────────────────

Total: $6.17  (3 sessions)

By project:
  aura-ml-projects-token-efficiency-scorer  $    3.40  (1 session)
  --Users-gaura-ml-projects-adk-tracegauge  $    2.77  (2 sessions)
──────────────────────────────────────────────────────────────────────
```

`tes cost --month` against the same data correctly picks up the older session too:

```
──────────────────────────────────────────────────────────────────────
COST -- last 30 days
──────────────────────────────────────────────────────────────────────

Total: $12.27  (4 sessions)

By project:
  aura-ml-projects-token-efficiency-scorer  $    9.50  (2 sessions)
  --Users-gaura-ml-projects-adk-tracegauge  $    2.77  (2 sessions)
──────────────────────────────────────────────────────────────────────
```

**Filters on `source_mtime`, not `scored_at`** — the session file's own real last-write time (when the usage actually happened), not when `tes score`/`tes scan` happened to run. Under a batch-scoring workflow (scoring a week's worth of sessions in one sitting), these diverge: a `scored_at`-based filter would cluster a week of real spend onto one scoring-run instant, or drop it outside the requested window, silently misattributing spend across period boundaries. `source_mtime` reflects when the money was actually spent, regardless of when you got around to running the scorer. (`tes budget`'s existing rolling-window projection has this same divergence — tracked, not fixed, as [#12](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer/issues/12).)

Sessions with no cost data yet are counted separately, not silently treated as `$0`. Real output, published `0.11.0`:

```
Total: $2.50  (1 session)
  (1 additional session in this period have no cost data yet -- excluded from the total above, not counted as $0)
```

**Known gap, not built:** no per-model breakdown. Would need a new schema column and adapter change, and nobody in the originating GitHub issue ([#78148](https://github.com/anthropics/claude-code/issues/78148)) asked for it — left explicit rather than silently omitted.

### Unpriced coverage

Shown automatically whenever coverage is below 100% — never hidden behind a flag, since an incomplete total should always be visible as incomplete. Reports what fraction of the period's *sessions* and *tokens* are actually priced (two different denominators — a handful of huge unpriced sessions can dominate the token figure while barely moving the session count), and names the specific unresolved model string(s) when known:

```
Priced coverage: 50% of sessions, 100% of tokens
  Unpriced model(s): claude-future-9
```

A session scored before `0.12.0` has no persisted model name for its own unpriced gap (the same lesson `0.11.1`'s attribution-persistence fix already established: information only available while the source file is readable must be saved at score time, not re-derived later) — that gap is still counted honestly, just flagged as unattributable rather than silently folded into a list that would then look complete:

```
Priced coverage: 40% of sessions, 85% of tokens
  Unpriced model(s): gpt-6-preview
  (some unpriced sessions predate model tracking -- can't name their model)
```

### `tes cost --roi` — plan-cost ROI

```bash
tes cost --week --roi
tes cost --week --roi --plan-config /path/to/plan.json   # override the default location
```

"Is my subscription worth it at API-equivalent prices?" Requires a plan config at `~/.tes/plan.json` (or `TES_PLAN_PATH`/`--plan-config`) — a **history**, not a single static cost, since a plan can change mid-window and each day should price at whichever plan was actually active that day, prorated by exact elapsed time (not calendar-day counting, which would inflate a rolling window that doesn't start/end at midnight — every real invocation):

```json
{"plans": [
  {"name": "Claude Pro", "monthly_cost_usd": 20, "effective_from": "2026-01-01"},
  {"name": "Claude Max", "monthly_cost_usd": 200, "effective_from": "2026-07-01"}
]}
```

Real output, this session's own transcript scored fresh and compared against a real 7-day-window `plan.json`:

```
Plan: Claude Max ($46.67 for this window)
ROI: $496.29 API-equivalent / $46.67 plan cost = 10.6x
  (API-equivalent value at measured token rates, not a bill you'd actually pay under a flat plan.)
```

**Refuses to print a ratio the data can't support** — never a misleading number:
- No `plan.json` configured: prints setup instructions instead of a ratio.
- Zero priced sessions in the window: "no priced sessions in this period — nothing to compare against plan cost."
- The window falls entirely before your first `plan.json` entry (prorated plan cost is $0): same refusal — a `$X / $0` ratio isn't a real number either.

---

## `tes impact` — code-impact reconstruction

```bash
tes impact
tes impact --top 20
```

Corpus-wide, from `Edit`/`Write`/`MultiEdit`/`NotebookEdit` tool-call payloads reconstructed and persisted at score time — additions/deletions per file, and a plain, transparent churn ranking (most-edited files and directories). **Deliberately no composite "risk score"**: a hand-weighted blend of signals presented as one number is exactly the kind of invented-precision this project avoids elsewhere (see the CHANGELOG's `[0.12.0]` entry for the reasoning) — every figure here is a direct count or a plainly-labeled fraction.

Real output, this session's own transcript:

```
291 edit operation(s) across 1 session(s) with impact data
  +14235 / -1994 lines
  66% of additions are from Write/NotebookEdit calls, whose payload never carries the file's PRIOR content -- additions are exact, but a full-file rewrite looks identical to a brand-new file, so this fraction is inherently uncertain in that specific way.

Most-edited files:
    20 edits  +494/-188  (1 session(s))  tes/cli.py
    15 edits  +137/-58  (1 session(s))  tes/score.py
    15 edits  +193/-127  (1 session(s))  tes/intelligence/cache.py
```

**Extraction scope, checked against this project's own real corpus** (883 real transcript files, 434,774 lines): `Edit` (10,175 real occurrences) and `Write` (3,059) are fully supported. `MultiEdit`/`NotebookEdit` are real Claude Code tool names with **zero occurrences in that same real-corpus check** — extraction is written (best-effort, defensive) but every operation from either is flagged internally and surfaced in the output's own inline fraction whenever it contributes to a total, never presented with the same confidence as Edit/Write-derived numbers. `apply_patch`/`str_replace_editor` (Codex/computer-use tool names, not Claude Code ones) are out of scope entirely.

**The `Write` ambiguity, stated plainly, not swallowed**: a `Write` call's payload contains the new file content but never the content it replaced — additions are exact, deletions are always 0 for that operation, and the report states what fraction of the total additions figure rests on this assumption (see the real output above: 66% on this real corpus).

**No cost-per-edit or cost-per-100-lines ratio here.** A bootstrap-CI implementation was built and measured (`docs/audit/EDIT_RATIO_BOOTSTRAP_COVERAGE.md`) before being adopted — coverage came in below nominal at every tested sample size, so no ratio statistic ships until a method with verified coverage exists. A number with a measured-wrong confidence interval is worse than no interval at all.

**If you're upgrading**: sessions scored before this feature has no persisted edit data (same `0.11.1` lesson — `old_string`/`new_string`/`file_path` are only readable while the source transcript exists, so this can't be backfilled from already-scored rows). `tes impact` counts these separately and says so plainly rather than silently omitting them from the total.

---

## `tes budget` — rolling self-trend pace

```bash
tes budget                      # rolling last 7 days (default)
tes budget --window-days 30
```

A rolling-window spend PROJECTION — distinct from `tes cost`'s period REPORT above. `tes cost` answers "what did I actually spend"; `tes budget` answers "where is my pace heading," extrapolating your own trailing-window spend linearly to the window's end. Always labeled with its sample size and window, never phrased as a promise ("trending toward," never "you will spend"). Real output, published `tracegauge==0.11.0`, a real 365-day window against real session data:

```
──────────────────────────────────────────────────────────────────────
BUDGET / PACE
──────────────────────────────────────────────────────────────────────

At this pace (~$5604.50 so far across 841 sessions, 70.1 of 365 days) you're trending toward ~$29179.77 over a 365-day window -- based on your last 70.1 days, not a forecast of future work; work volume varies.

──────────────────────────────────────────────────────────────────────
```

At the default 7-day window, with no cost data that recent, `tes budget` says so plainly rather than fabricating a projection from stale data:

```
No sessions with cost data in the last 7 days -- nothing to project yet.
```

**Filters on `scored_at`, not `source_mtime`** — when `tes score`/`tes scan` happened to run, not when the session itself happened. This is a real, known divergence from `tes cost`'s design (which deliberately uses `source_mtime` instead — see above): under a batch-scoring workflow, every session scored in one sitting gets the same `scored_at` timestamp, so this projection's trailing window can cluster a batch of real, older spend onto one recent instant, or miss it entirely once it ages out of the window — describing "cost incurred in scoring runs over the last N days," not necessarily "cost incurred by real usage in the last N days." Documented here honestly as this command's current, real behavior, not its intent. Tracked as [#12](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer/issues/12); not fixed here.

## `tes monitor` — live in-progress check

```bash
tes monitor [--stability-window SECONDS] [--plan usage_based|max]
```

A one-shot check of whatever Claude Code session is currently being written under `~/.claude/projects` — scores it as-is (a partial transcript, not a finished session), prints an estimated cost/context figure explicitly labeled "in progress," and checks the same data-gated cost/context alarm the background watcher uses. A session more recently modified than `--stability-window` (default 300s) is considered "active"; if none is, it says so and does nothing further.

Real output, published `tracegauge==0.11.0`, against a genuinely active session on this machine (redaction warnings are the adapter finding and stripping real secret-shaped strings from the transcript before scoring — the tool's own redaction-on-by-default behavior, not an error):

```
[adapter] WARNING: redacted 1 occurrence(s) of pattern 'anthropic_key'
[adapter] WARNING: redacted 3 occurrence(s) of pattern 'generic_key_assignment'
...
Session: 96fd948b-9197-46b2-9e9f-4dbfd7fc3a88  (infra-deploy)
  ~$681.70 (estimated, in progress)
  ~20,400,400 context tokens (estimated, in progress)
  99% context re-send (measured)

Live estimate of an IN-PROGRESS session -- cost and context size are provisional and will change as the session continues. Computed with the same frozen attribution/cost math used on completed sessions, applied to the partial transcript seen so far. Never a final or billed figure.

[ALARM] This session is at ~$681.70 (estimated, in progress) and ~20,400,400 context tokens (estimated, in progress), 99% of which is re-sent context (measured) -- well above your own typical infra-deploy session (p75: 447,157 tokens). Consider `/compact`.
```

The alarm compares the live estimate against your own self-baseline (per task type), the same baseline `tes score`'s band verdict uses — it fires only once enough of your own history exists to make that comparison meaningful, never against a fixed universal threshold.

## Session intelligence — `tes patterns` and `tes ask`

The most differentiated thing either package does, and previously the least visible: unsupervised clustering of your own session corpus into behavioral archetypes, plus a constrained conversational explainer over the result.

### `tes patterns`

```bash
tes patterns [--recompute]
```

Runs (or displays the cached result of) validated KMeans clustering over your session corpus, plus statistical anomaly detection. Results cache to a file named after and co-located with your TES database (`<db-name>.intelligence_cache.json` — e.g. `~/.tes/tes.intelligence_cache.json` for the default DB) and are reused by `tes ask` below.

Requires **30+ content sessions**. Attribution fractions (the features clustering runs on) are computed once and persisted directly to the database at score time — a session scored by `tes score`/`tes serve` clusters correctly regardless of whether its original source JSONL still exists on disk later.

**If you're upgrading from `0.11.0` or earlier: read this before running `tes patterns`.** Sessions scored before this version cannot be backfilled — their attribution was never persisted, and recovering it now would require re-reading the original transcript file, which for most real setups no longer exists (transcripts age out, get cleaned up, or move). There is no partial recovery either: the fractions clustering needs (context re-send/growth, output, waste) aren't derivable from anything else this tool stores about a session — checked directly, not assumed (the closest available data, `waste_events`, records dollar cost per detected event, not the raw token breakdown clustering needs, and covers only 1 of the 4 features either way). **Concretely, on the machine this was measured on: 321 of 321 previously-scored content sessions (100%) are permanently unusable for clustering post-upgrade — the pattern corpus starts over at 0 and rebuilds only from sessions scored from this version forward.** Your own numbers will differ, but the mechanism is the same: nothing is lost or corrupted, `tes score`/`tes patterns`/`tes ask` on already-scored sessions keep working exactly as before this upgrade — only the *clustering* feature specifically restarts its corpus. If a legacy session's file is confirmed unreachable, the tool says so plainly rather than guessing:

```
Not enough content sessions for pattern analysis yet (12 < 30 needed) -- 8 previously-scored session(s) can't count because their original transcript file no longer exists on disk (scored before this version started saving what it needs at score time; those specific sessions can't be recovered -- re-scoring requires the same file, which is gone). Your pattern corpus rebuilds from sessions scored from now on; nothing else to do.
```

**How the archetypes are derived** (methodology, not invented): KMeans clustering over a 13-feature vector per session (attribution percentages — context re-send/growth/output — plus log-scaled size features), with `k` chosen by silhouette score over `k ∈ [2, 8]`, validated for stability via 10 reseeded runs (coefficient of variation `< 0.15` required to call the result "stable"). Each archetype's *name* is generated automatically from its centroid's most discriminating features relative to the corpus mean — never hand-labeled, and evaluative words (efficient/wasteful/good/bad) are prohibited by construction; a name describes measured shape, not quality.

**A real, live result** — 38 real Claude Code sessions, scored fresh this session (`tes score --no-judge` against two real project directories, `--recompute`), demonstrating the score-time-persistence fix directly: `[features] extracted 38 / 39 sessions (persisted=38, stubs=1, no_source=0, failed=0)` — every one of those 38 came from the database, zero source-file re-reads. `k=3`, silhouette `0.479` (above the `0.20` "meaningful structure" bar), stability CV `0.000` (perfectly stable across all 10 reseeded runs):

```
ARCHETYPES (measured behavioral patterns -- not quality labels):

  [0] medium high context re-send sessions
      22 sessions (57.9%)  context_resend=98.9%  context_growth=0.9%  output=0.2%  waste_flag=no
      task mix: debug-fix:7  ml-eval:6  feature-build:6  research-recon:3

  [1] small high context re-send sessions
      14 sessions (36.8%)  context_resend=97.4%  context_growth=2.1%  output=0.5%  waste_flag=no
      task mix: ml-eval:8  debug-fix:2  feature-build:2  research-recon:2

  [2] medium with detected waste sessions
      2 sessions (5.3%)  context_resend=98.3%  context_growth=1.1%  output=0.2%  waste_flag=yes
      task mix: ml-eval:1  debug-fix:1

ANOMALIES: 2 of 38 sessions (5.3%) are statistical outliers for their cluster.
```

**What you do with this:** the archetypes are a description of your own measured behavior, not a scorecard — there's no "good" archetype to aim for. This particular corpus's three archetypes separate mainly on *size* and *waste presence*, not on dramatically different working styles (consistent with this project's own earlier B-phase clustering research on a larger corpus, `research/12_session_intelligence.md`) — useful context before over-reading meaning into which archetype a given session falls into.

### `tes ask`

```bash
tes ask "What kind of sessions do I run?"
tes ask "Do I have any waste patterns in my sessions?" --api   # opt-in, consent-gated
```

A conversational explainer, constrained by construction to answer only from the same measured metrics/pattern output `tes patterns` produces — never invents analysis, predicts future cost, or judges session quality. Tries local Ollama first (`qwen3:8b` by default, or whatever 7B+ model is available); with no local judge but `ANTHROPIC_API_KEY` set, offers the API path with an explicit per-call consent prompt (sends metrics only, never session content/code).

Real output, published `0.11.0`, local Ollama, against real session data:

```
Looking up your session data...

From your measured data, 12.8% of content sessions (41 out of 321) have detected waste, totaling 74 waste events. However, there is not enough content sessions for detailed pattern analysis yet (less than 30 sessions needed for patterns to emerge). As your session corpus grows, tracegauge will provide more insights into waste patterns.

(answered from measured metrics -- local Ollama)
```

The constraint is real, not just a system-prompt claim — asking something genuinely unmeasured gets refused rather than answered with a plausible-sounding guess:

```
$ tes ask "How much of my token spend is context re-send versus actual output?"

I don't have that measured -- tracegauge hasn't collected that metric. The data provided includes total token counts and cost metrics, but not a breakdown between context re-send and actual output tokens. You may need to analyze token usage patterns or use additional tools to differentiate between these categories.

(answered from measured metrics -- local Ollama)
```

## What this does NOT do

- No composite efficiency score. The three axes are independent by design — a single number would hide the axis-specific domain limitations.
- No "catches all inefficiency." The waste detectors fire on observable-invariant patterns only.
- No accuracy guarantee on the trajectory axis. It's an LLM judge, coherence-validated, not human-calibrated.
- No cloud scoring. The scoring pipeline is fully local. `tracegauge export-contribution` (P7) provides a local-file-only contribution export: opt-in, content-free, nothing transmitted. Server-side aggregation of that data (`tes corpus contribute`) is built and tested but **not currently active** — no corpus is provisioned, so it sends nothing regardless of consent — and would never be a substitute for the local self-baseline even once activated.
- No cross-agent support yet. The CC adapter is Claude Code–specific; OpenCode/Codex/Aider would need their own adapters and re-validation.

---

## SDK usage

```python
from tes import load_baselines, score_session, JudgeConfig
from tes.adapt import adapt_session
from tes.baselines import BUNDLED_BASELINES_PATH
from tes.waste import detect_repeated_failed_retry, detect_redundant_read, build_waste_entry

baselines = load_baselines(BUNDLED_BASELINES_PATH)
record = adapt_session("path/to/session.jsonl")  # secrets redacted at ingestion

session_id = record["session_id"]
turns = record["digest"]["turns"]
waste_entry = build_waste_entry(session_id, turns)

# Optional: trajectory judge (returns None → UNAVAILABLE when no local judge)
from tes.judge import score_trajectory

judge_entry = score_trajectory(record)

result = score_session(record, baselines, judge_entry=judge_entry, waste_entry=waste_entry)
print(result.band_verdict)  # "within_band" | "above_p75" | "below_p25" | "unavailable"
print(result.judge_verdict)  # "BETTER" | None
print(result.waste_event_count)  # int
print(result.token_domain_of_validity)  # caveat string, always populated
```

---

## Validation

The scoring components were validated through a five-phase credibility arc (B1–B5) before packaging. Key results:

- **Token baselines (B2):** 75 quality-gated CC sessions, 5 task types, scope gates at per-type p10 turn floor. See [research/08-baselines.md](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer/blob/master/research/08-baselines.md).
- **Trajectory judge (B3):** Cross-model corroboration. Positive verdicts: 84% strict / 96% top-2. Negative verdicts model-dependent. No human gold. See [research/09-cross-model.md](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer/blob/master/research/09-cross-model.md).
- **Deterministic waste (B4):** RFR fired 12/181 pool sessions (6.6%). RR fired 20/181 (11.0%). Observable-invariant boundary documented. See [research/10-deterministic-waste.md](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer/blob/master/research/10-deterministic-waste.md).
- **Generalization (B5):** RFR and PATH-A validated across 172 developers (1,053 SWE-chat CC sessions — [ODC-BY licensed, see DATA_SOURCES.md](DATA_SOURCES.md)). Rate gap (6.6% pool vs 1.4% SWE-chat) explained by corpus characterization — pool is a high-waste infra outlier. Cross-agent generalization inconclusive (parquet lacks tool_result rows for OpenCode/Codex). See [research/11-generalization.md](https://github.com/gaurav-gandhi-2411/token-efficiency-scorer/blob/master/research/11-generalization.md).

---

## License

[AGPL-3.0-only](LICENSE) — free to use and self-host; any modified version distributed as a network service must publish its source under the same license. Exception: `tes/cost.py` and `tes/_digest.py` are additionally available under [Apache-2.0](LICENSE-APACHE). This lets downstream packages — e.g. [adk-tracegauge](https://github.com/gaurav-gandhi-2411/adk-tracegauge) — depend on the cost-computation module without inheriting AGPL's copyleft terms. Every other file in this repository remains AGPL-3.0-only.

---

## Roadmap

- **Corpus de-biasing:** `tracegauge export-contribution` (P7) writes a local content-free digest for voluntary contribution. Server-side aggregation, pooled baselines, and legal review are follow-on work.
- **Smaller judge:** a laptop-runnable quantized model for the trajectory axis (requires a new B3-equivalent corroboration run, not a swap).
- **Cross-agent support:** adapters for OpenCode, Codex, Aider once tool_result data is available for re-validation.
- **`tes install-hook`:** explicit opt-in SessionEnd hook for zero-latency scoring (modifies `~/.claude/settings.json` only on user request).

Recommended user follow-ups (not built): register `tracegauge.dev`; lawyer review of AGPL terms before any commercial raise.
