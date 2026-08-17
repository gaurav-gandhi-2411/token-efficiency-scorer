# Cross-agent dashboard competitive gap analysis (WW1–WW4)

**Status: GATHER + DESIGN ONLY.** No feature code, no PRs. Read-only research
against a third-party, MIT-licensed, open-source session-dashboard project (name withheld from this repo per operator instruction) via
`raw.githubusercontent.com` — anonymous, unauthenticated fetches only; the
repo was not forked, starred, cloned with credentials, or otherwise touched
in any way that would notify its owner. Files read: `adapters.mjs`,
`analytics.mjs`, `pricing.json`, `README.md` (all VERIFIED, full contents
read, source at commit on `main` as of 2026-08-17).

**No code was copied.** The reference dashboard is MIT-licensed; `tracegauge` is
AGPL-3.0-only. Everything below is a description of the reference dashboard's *documented
behavior* (its README's own claims, corroborated by reading its source to
confirm the behavior is real and not just a README claim) — not lifted code.
Anything this document recommends building would be a from-scratch,
independent reimplementation informed by the behavior description, same as
any other competitor-analysis document in this repo's `docs/audit/`.

---

## WW1.1 — JSONL fields the reference dashboard reads that tracegauge does not

VERIFIED by reading `tes/adapt.py` and `tes/_digest.py`: neither file
references `old_string`, `new_string`, `file_path`, or a tool call's
`args`/`input` payload at all — grep for all four returns zero matches.
tracegauge's adapter extracts token usage (`usage.input`/`output`/
`cache_read`/`cache_write`), message role/type, and tool *name* (for waste
detection — REDUNDANT-READ needs the tool name + result text, not its
arguments). It never reads what an `Edit`/`Write`/`MultiEdit`/`NotebookEdit`
call actually *did* to a file.

Fields the reference dashboard's `adapters.mjs`/`analytics.mjs` read that tracegauge's
adapter does not:

| Field | Where (the reference dashboard) | What it enables |
|---|---|---|
| `tool_use.input.old_string` / `.new_string` (Edit) | `analytics.mjs:stringEdit` via `extractEditOperations` | Line-level additions/deletions per edit |
| `tool_use.input.file_path` / `.path` / `.notebook_path` | same | Which file was touched (per-file/per-directory rollups) |
| `tool_use.input.edits[]` (MultiEdit) | same | Same as above, per sub-edit |
| `tool_use.input.content` (Write) | same | New-file content → additions (no prior content, so no deletions — the reference dashboard's own README states this ambiguity explicitly) |
| `tool_use.input.patch`/`.cmd` containing `*** Begin Patch` or `diff --git` (apply_patch / Codex `functions.exec`) | `analytics.mjs:parsePatch`/`decodeWrappedPatch` | Per-file unified-diff additions/deletions, including a patch nested inside a JS string literal |
| `cwd` (per-line, Claude Code) | `adapters.mjs:parseClaudeCodeFile` | Resolves the session's project directory when it isn't the top-level dir name |
| `ai-title`/`aiTitle`, `summary` | same | Human-readable session label (tracegauge derives `task_type` via classification instead — different purpose, not a real gap) |
| Later user-turn text matched against a correction-phrase regex | `analytics.mjs:CORRECTION_RE` | "corrections" workflow signal (see WW1.5) |
| `tool.resultTs - tool.ts` per call | `analytics.mjs:sessionIntelligence` | Per-tool-call latency (median tool latency signal) |

## WW1.2 — ROI feasibility, config surface, CLI sketch

**What tracegauge already has to compute plan ROI:** `session_cost_usd` per
session (already computed, already stored), `source_mtime` (real per-session
timestamp, already used by `tes cost`'s rolling windows). That's the entire
numerator side of an ROI calculation — cost already exists and is already
period-filterable. **What's missing:** a plan-cost config (plan name +
monthly spend) and the ROI division itself (`sum(session_cost_usd) over
window / plan_cost_for_window`).

**Design — config surface (new, small):**

```yaml
# ~/.tes/plan.yaml (or --plan-cost/--plan-name flags, env var fallback)
plan_name: "Claude Max"
monthly_cost_usd: 200
```

Single-plan only (tracegauge is Claude-Code-only, single-source — the reference dashboard's
per-source ROI split doesn't apply; see WW2.2 on why NOT building multi-source
support is itself the right call for this tool's positioning).

**Design — CLI output sketch** (illustrative shape, not real output — no
values below are measured; a real implementation would need real numbers
before shipping, per this repo's own metric-provenance rule):

```
tes cost --week --roi

──────────────────────────────────────────────────────────────────────
COST -- last 7 days
──────────────────────────────────────────────────────────────────────
Total: $XXX.XX  (N sessions)
Plan: Claude Max ($200/mo -> $46.15 for this 7-day window)
ROI: $XXX.XX API-equivalent / $46.15 plan cost = N.Nx
  (API-equivalent value, not a bill you'd actually pay -- see README's
  existing "Cost is not a score" framing, which this inherits unchanged.)
```

## WW1.3 — Which edit tool-call payloads actually appear (real data)

VERIFIED against every real Claude Code JSONL file on this machine (883
files under `~/.claude/projects`, 434,774 lines scanned, all `tool_use`
blocks counted):

| Tool | Count | Reconstructable? |
|---|---|---|
| `Edit` | 10,175 | Yes — `old_string`/`new_string` present, exact line-level diff |
| `Write` | 3,059 | Additions only — no prior content in the payload; matches the reference dashboard's own documented ambiguity exactly (a `Write` "contains the new content but not necessarily the file it replaced") |
| `MultiEdit` | 0 | n/a on this machine (current Claude Code versions use `Edit` per-edit rather than a batched `MultiEdit` call) |
| `NotebookEdit` | 0 | n/a on this machine |
| `apply_patch` | 0 | n/a — Codex/OpenAI-specific tool name, never appears in Claude Code transcripts by construction |

**What's ambiguous:** exactly what the reference dashboard's own README states — a `Write`
call's additions are countable (new content is in the payload) but its
deletions are not (old content isn't sent to the tool, so a full-file
rewrite via `Write` looks identical to a brand-new file in the payload
alone). `Edit` has no such ambiguity — both `old_string` and `new_string`
are always present.

## WW1.4 — Codex CLI on this machine

VERIFIED: `~/.codex/sessions` does not exist on this machine (checked
directly — `No such file or directory`); no `CODEX_HOME` env var set either.
Codex CLI's format is therefore documented from the reference dashboard's adapter code
only (`adapters.mjs:parseCodexFile`/`codexAdapter`), not from a real local
sample — flagged as such, not presented as independently confirmed:

- Path: `$CODEX_HOME/sessions` (default `~/.codex/sessions/YYYY/MM/DD/rollout-<uuid>.jsonl`).
- Line shapes: `session_meta` (id, cwd, timestamp), `turn_context` (model),
  `event_msg` with `token_count`/`total_token_usage` (input/output/cached),
  `response_item` wrapping `message`/`reasoning`/`function_call`/
  `function_call_output`/`web_search_call`, and a `compacted` marker.
  Some lines wrap the payload in a top-level `payload` key; the reference dashboard's
  parser tolerates both wrapped and unwrapped ("older codex versions have
  no payload wrapper" — its own comment).
- Edit payloads arrive as `function_call`/`custom_tool_call` with
  `arguments`/`input` containing either a `patch`/`cmd` string (apply_patch
  format, `*** Begin Patch` / `*** Update File:` markers) or a
  `diff --git` unified diff — both handled by the same `parsePatch()`.

## WW1.5 — Workflow signals vs. tracegauge's existing waste detection

| Reference-dashboard signal | tracegauge equivalent today | Genuinely new? |
|---|---|---|
| Rework loop (re-edit same file, same session) | None — attribution buckets are token-shaped (context resend/growth/output/waste), not file-shaped | **New** |
| Churn (repeat file touches across sessions) | None | **New** |
| Abandoned session (ends without a final assistant response) | None (waste detection is about redundant tool calls, not session outcome) | **New** |
| Correction ("no", "wrong", "undo" in a later user turn) | None | **New** |
| Time to first edit | None | **New** |
| Cache efficiency (cache-read / total input) | Already covered, differently: `context_resend_pct` from `tes.attribution.attribution_fractions` measures almost the identical thing (cache-read-dominated resend as a fraction of billed tokens) — same underlying signal, already shipped, different name/framing | **Already covered** |
| Repeated identical tool call, no state change | `REPEATED-FAILED-RETRY` waste detector (byte-verbatim, existing) | **Already covered** |
| Same file read twice, no edit between | `REDUNDANT-READ` (PATH-A/PATH-B, existing) | **Already covered** |

Net: 5 of 8 signals are genuinely new (all file/edit-centric, which
tracegauge's token-bucket attribution model doesn't touch at all); 3 are the
same underlying measurement tracegauge already ships under a different name.

## WW1.6 — No code copied

Stated per WW1's rule and repeated here for the record: everything above is
reimplementation-from-documented-behavior. `pricing.json`'s specific
regex/rate values were **not** copied into any tracegauge file (tracegauge
has, and keeps, its own independently-sourced, independently-dated
`tes/data/prices.json`).

---

## WW2 — Rank

### 2.1 Ranking by (value × differentiation) / effort

| Candidate | User question it answers | Value | Differentiation vs. the reference dashboard | Effort | Rank |
|---|---|---|---|---|---|
| Plan-cost ROI (`--roi` on `tes cost`) | "Is my subscription worth it at API-equivalent prices?" | High — direct, concrete, dollar-denominated | Low (the reference dashboard already does exactly this) | Low (both inputs already exist) | **1** |
| Per-file edit/churn reconstruction (Edit/Write parsing → additions/deletions/rework/churn) | "Which files keep getting reworked?" | High | Low differentiation from the reference dashboard alone, but genuinely extends tracegauge's own existing waste-detection story | Medium (new adapter-layer parsing, new schema) | **2** |
| Corrections / abandoned-session workflow signals | "How often do I have to correct the agent, or give up?" | Medium — coaching-flavored, softer than a hard number | Low | Medium (needs the same event-level access as above, plus a correction-phrase heuristic — inherently fuzzier than token-count-based signals) | **3** |
| Multi-source (Codex/OpenClaw/Hermes) support | "Is Claude Code or Codex more efficient on my workload?" | Low for this tool's actual user (tracegauge is a Claude-Code-specific measurement tool, not a cross-agent dashboard) | Would directly copy the reference dashboard's positioning rather than differentiate from it | High (new adapters, new cross-source pricing table, new comparison UI) | **Do not build** |
| Time-to-first-edit, tool latency | Narrower, lower-value questions with less obvious action attached | Low | Low | Low-Medium | **Not ranked separately — bundle into #2 if #2 ships, skip otherwise** |

### 2.2 Rigor-positioning check (dilution flag)

tracegauge's own established posture (silhouette thresholds, Wilson CIs,
bootstrap regression testing, explicit "domain of validity" statements) is a
**measurement-rigor** positioning — every number ships with its own honesty
caveat. Checked each candidate against that bar:

- **Plan-cost ROI**: clean — it's a ratio of two already-measured dollar
  figures, no new uncertainty introduced. **Not a dilution.**
- **Edit/churn reconstruction**: clean if additions/deletions are reported
  as exactly what they are (line-count deltas from tool payloads, not a
  proxy for code quality or correctness) — same caveat the reference dashboard's own
  README already states ("These are coaching heuristics, not claims about
  code authorship or correctness"). **Not a dilution, if that caveat ships
  with it.**
- **Corrections / abandoned-session heuristics**: **flagged.** A regex
  match on "no, that's wrong" is a much fuzzier signal than anything else
  this project ships — no false-positive-rate measurement, no calibration
  corpus, nothing resembling the FPR-grid discipline this project's own
  `_regression.py` and Phase 9's HH3.2 note both require before a
  comparative/behavioral claim ships. Building this without that
  measurement would be exactly the "adds a number without uncertainty
  attached" dilution WW2.2 asks to flag.
- **Cache efficiency, rework, churn (pure counts)**: clean — these are
  direct counts from parsed data, not inferred judgments. **Not a
  dilution.**

### 2.3 Recommend: what ships in 0.12.0, what waits

**Ships (both pass Phase 8's test — see 2.4 — and the rigor check above):**
1. Plan-cost ROI on `tes cost` (rank 1) — cheapest, cleanest, most directly
   answers a real question tracegauge's own README's "why this exists"
   framing already gestures at (cost annotation without a spend-comparison
   feature is a gap in the existing story, not a new one).
2. Per-file edit/churn reconstruction (rank 2) — the one candidate that
   both extends tracegauge's existing waste-detection story (churn/rework
   are the file-level sibling of REDUNDANT-READ's turn-level signal) and
   clears the rigor bar as a plain count.

**Waits:**
- Corrections / abandoned-session signals — real user value, but ships only
  after a false-positive-rate measurement on the correction-phrase
  heuristic (same discipline this project already requires elsewhere), not
  on the `K=3`-style "common, defensible default" basis the reference dashboard itself
  uses unmeasured.
- Multi-source (Codex/OpenClaw/Hermes) support — not ranked for building at
  all under current evidence; see 2.4.

### 2.4 Phase 8's test, applied

Multi-source support is real, feasible (the reference dashboard's own adapter pattern is a
usable template), and **zero validated demand found this pass** — no search
for an external, unprompted user request for this was run in WW1 (out of
scope for a gather-only pass; if it ships, that search happens first, not
after). By the identical test Phase 8 used to kill "Option C" and Phase 9
used to kill HH2/HH3.1-2: real feasibility without validated demand is
architecture for its own sake, not a response to present demand. Kill it
here, for the same reason, until that search is actually run and comes back
positive.

---

## WW3 — Sanity check: the $452.81 vs. $6.66/session gap

**3.1 — Diagnosis.** Neither seeded data nor a bug. `tes cost --week`'s
$452.81 came from scoring this exact session (`77bbcf41-e753-4ed6-989e-
7d871cafd560` — the live transcript of this very engagement) via `tes score
--no-judge` during VV1.1's pre-tag verification, reused for VV1.5's
published-artifact check. VERIFIED root cause, traced to source:

- `sessions.real_tokens` for this session = 20,161,605 — but `real_tokens`
  is **not** the cost denominator. `tes.baselines.compute_real_tokens`'s own
  docstring: "Excludes cache_read re-accumulation" — it's a token-*economy*
  metric, deliberately excluding cheap-but-voluminous cache-read resend, to
  avoid a long session looking artificially "expensive" on the token-economy
  axis just because it's long.
- The actual cost denominator, `attribution.total_billed_tokens`, recomputed
  directly against the source file: **1,941,900,563** (1.94 billion) —
  `context_resend_tokens` alone is 1,920,725,117 of that (98.9%,
  matching the persisted `context_resend_pct=0.989` on this row).
- $452.81 / 1.94B × 1,000,000 ≈ $0.233 effective blended $/M — sane and
  below tracegauge's own sonnet-5 rate table's input rate ($2/M), consistent
  with a session where nearly all billed volume is cache-read-priced context
  resend (~$0.20/M) plus a smaller, pricier slice of context-growth
  (cache-write, ~$2.50/M) and output (~$10/M). The blended rate lands where
  the arithmetic says it should; no double-counting, no unit-scale bug.
- The 68x gap vs. the $6.66/session historical average is explained by this
  session's length alone: 11,286 JSONL lines / 21.5MB, spanning this entire
  multi-day, multi-phase engagement — a genuine, extreme outlier by
  duration, not by anything wrong with the computation. A long-running
  agentic session re-sends its entire accumulated context on every turn, so
  cumulative billed tokens grow much faster than session count would
  suggest; this is mechanism, not anomaly.

**3.2 — Not seeded**, so this doesn't apply, but the underlying caution is
taken: checked every README/CHANGELOG example this session's earlier
verification steps produced (`grep` for `452`, `432.74`, the exact token
counts) — none of it landed in shipped docs. This session's own transcript
was used only as a functional smoke-test fixture (does the pipeline run,
do columns populate), never as an illustrative "here's real output" example.
Noted for future verification runs: prefer a shorter, more typical real
session when the captured output is meant to be illustrative, not just a
smoke test.

**3.3 — Real, explained above.** Token counts and mechanism reported in
full; not a bug.

**3.4 — N/A** (not a bug; WW1/WW2 continued without pausing, per 3.4's
condition never being triggered).

One incidental finding worth naming, not fixing here: `real_tokens` and
`total_billed_tokens` differing by ~96x for the same session is, on its own,
a genuinely confusing pair of names for someone reading the schema cold —
`real_tokens` sounds like it should be close to what's actually billed, and
isn't, by design. Not a bug, not in scope for this document, but worth a
naming/docstring pass at some point (not filed as an issue here — this
document is gather-only).

---

## WW4 — Queued, not now (design only)

### 4.1 — Closing #12 and #17 with the required-parameter pattern

Both issues are the same class RR2/UU2 already fixed structurally elsewhere
in this codebase:

- **#12** (`tes/budget.py`'s rolling-window projection filters on
  `scored_at`, not `source_mtime`): not a defaultable-`db_path` bug like
  RR2/UU2 — it's a wrong-column bug (filtering on when the row was scored
  instead of when the session actually happened, same divergence class
  `tes cost` already fixed for itself in 0.11.0, per that release's own
  CHANGELOG entry pointing at this exact issue). Design: change the
  window filter from `scored_at >= cutoff` to `source_mtime >= cutoff`,
  mirroring `tes/cost.py`'s already-shipped, already-tested filter exactly
  — this is a one-line predicate change plus updated tests, not a new
  mechanism. Not the UU2 pattern (no db_path defaulting involved) — flagged
  here only because the user's WW4.1 grouped them; the actual fix shape
  differs per issue.
- **#17** (`export-contribution`'s default `--output` path doesn't vary with
  `--db-path`): this one **is** the UU2 pattern. Design: apply the same
  fix — make the output-path resolution take the resolved `db_path` as an
  input (co-locate/name the default export path after the source DB, the
  same way `_cache_path()` now does), and require that resolved path
  explicitly at the point of writing rather than defaulting independently.
  Mirrors RR2/UU2's fix in `tes/intelligence/cache.py` almost exactly —
  same bug shape, same fix shape, different file.

Neither implemented here — design only, per WW4's own "queued, not now."

### 4.2 — Stale branch audit (read-only, run this session)

Script written to
`C:\Users\gaura\AppData\Local\Temp\claude\C--Users-gaura-ml-projects\77bbcf41-e753-4ed6-989e-7d871cafd560\scratchpad\check_stale_branches.sh`
(not committed to either repo — a one-off audit tool, not project
infrastructure). For each local branch except the default: computes a
content-diff against the default branch (informational only — squash-merge
means a non-zero diff here does NOT mean unmerged) and queries `gh pr list
--head <branch> --state all` for the authoritative signal (a branch's PR
state = MERGED is reliable regardless of squash-merge history).

**Result — every branch in both repos checks out as merged, VERIFIED via
PR state, nothing deleted:**

- **adk-tracegauge**: 14 local branches (`chore/release-0.4.0`,
  `chore/release-0.4.1`, `docs/agent-attribution-readme`,
  `docs/no-triviality-merge-carveout`, `docs/pairing-key-harness-table`,
  `docs/phase8-9-audit-record`, `docs/python-3-14-note`,
  `feat/cost-regression-gate`, `feat/price-vendor-divergence-guard`,
  `feat/quickstart-command`, `feat/sub-agent-attribution`,
  `fix/0.3.1-first-run-and-py314`, `fix/0.3.2-quickstart-release`,
  `fix/version-single-source`) — all 14 have an associated PR with
  `state: MERGED`.
- **token-efficiency-scorer**: 15 local branches (`chore/dual-license-cost-
  module`, `chore/portfolio-manifest-ci`, `chore/release-0.11.0`,
  `chore/release-0.11.1`, `ci/pypi-trusted-publishing`, `docs/document-
  remaining-commands`, `docs/period-cost-readme`, `docs/release-0.10.2-
  audit`, `docs/releasing`, `feat/period-cost-cli`, `feat/price-vendor-
  divergence-guard`, `feat/quickstart-command`, `fix/0.10.2-pricing-
  defects`, `fix/legacy-row-backfill-messaging`, `fix/tes-stale-aetherart-
  project`) — all 15 have an associated PR with `state: MERGED`.

29 branches total (local, and their identically-named `origin/` remotes),
zero ambiguous, zero found unmerged. Genuinely safe cleanup candidates —
**nothing deleted**, per WW4.2's explicit instruction; this is the read-only
report only.
