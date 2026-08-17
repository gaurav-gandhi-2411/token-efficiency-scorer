# Q6 — trajectory timeline + sub-agent spawn trees: design (no code)

Design only, per Q6.5. Routed for review before any implementation starts.

## 6.1 — Audit: what `tes serve` renders today

**Stack, confirmed by reading `tes/web/server.py` and `tes/web/templates/`**:
Flask + server-rendered Jinja2 templates (`base.html` + 8 page templates).
**Zero client-side JavaScript anywhere in the current templates** — no
`<script>` tags, no charting library, no `canvas`/SVG-driven UI. Every
page is a plain HTML/CSS render per request; the only "interactivity" is
full-page navigation and one `POST /ask` form.

**`/session/<id>` (`session_detail.html`, 405 lines), what it shows today**:
task type, band verdict (with `cost_band`/`baseline_cost_median`), trajectory
axis (judge verdict via `TrajectoryRenderState` — current/stale/unavailable),
deterministic waste events (with proof-turn numbers, but rendered as a flat
list, not positioned on a timeline), cost annotation, and a 4-way attribution
breakdown (context-resend/growth/output/waste as % of tokens) with a takeaway
sentence and a per-row table.

**What's reusable**:
- The waste-event data model already carries `turns: [...]` (proof-turn
  numbers) — this is the same "anchor to a turn index" primitive a timeline
  needs; the timeline doesn't invent a new position concept, it visualizes
  one that already exists.
- `TrajectoryRenderState` (current/stale/unavailable) is the exact pattern
  to reuse for "is this number trustworthy right now" on every timeline
  annotation, not just the judge verdict.
- The attribution rows table's honest-percentage-with-takeaway pattern
  (a number, plus one sentence explaining what it means) is the template
  for how every timeline duration/cost annotation should be captioned.

**What's NOT reusable / genuinely new**: there is no existing per-turn
sequence data surfaced to the template at all — `session` is a single
flat DB row. A trajectory timeline needs the turn-by-turn structure that
today only exists transiently during scoring (`tes/adapt.py`'s single-pass
parse) and is not persisted. This is the same "RR1 lesson" this project
has hit repeatedly: information only available while the source JSONL is
still on disk must be persisted at score time, or the timeline breaks for
any session whose transcript has since been cleaned up. **This is the
single biggest open design question, addressed in 6.2 below — not
deferred silently.**

## 6.2 — Trajectory timeline: data model and rendering

**Persistence, extending the RR1 pattern already established for
`edit_operations`**: a new nullable `turn_sequence` column (JSON), populated
at score time by extending `tes/adapt.py`'s existing single-pass parse loop
— the same mechanism `edit_operations` (AB3) already uses, not a new
extraction pass. Each entry:

```json
{
  "turn_idx": 42,
  "role": "user" | "assistant" | "tool_use" | "tool_result",
  "kind": "reasoning" | "text" | "tool_call" | "tool_output" | "error",
  "tool_name": "Edit" | "Bash" | ... | null,
  "duration_ms": 1830,
  "is_error": false,
  "agent_name": "root" | "<sub-agent-id>",
  "parent_turn_idx": 41
}
```

`duration_ms` is wall-clock between this turn's timestamp and the
previous turn's — **explicitly NOT model "thinking time" vs. tool
wall-clock separated**, because the raw JSONL does not reliably distinguish
them; the timeline caption says "elapsed," never "compute time," matching
this project's standing discipline against inventing precision the source
data doesn't support (same discipline as `tes impact`'s explicit
`prior_content_unknown` flag).

**Legacy rows**: sessions scored before this column existed have
`turn_sequence IS NULL`. The timeline section renders a plain, honest
message ("no turn-level data for this session — scored before this
feature shipped") instead of an empty or broken chart — same pattern as
every other legacy-row story in this changelog's history, not a new one.

**Rendering — the one real architectural decision this design doc is
flagging for review, not deciding unilaterally**: a turn-by-turn timeline
with collapsible tool output and hover-for-duration needs *some*
client-side interactivity that 405 lines of pure server-rendered HTML
doesn't currently have anywhere in this codebase. Two honest options,
not a foregone conclusion:

1. **Pure CSS, zero JS** (`<details>`/`<summary>` for collapsible tool
   calls, CSS-only hover tooltips via `title`/`:hover` + `::after`).
   Matches the current stack exactly, "boring technology by default."
   Ceiling: no smooth zoom/pan on a very long session (500+ turns), no
   client-side filtering by role/tool without a full page reload.
2. **A small amount of vanilla JS** (no framework, no bundler — one
   `<script>` block, matching the zero-build-step simplicity of the rest
   of `tes serve`) for filter-by-role and expand/collapse without a
   round-trip. Stated advantage: real usability on long sessions (a 300+
   turn debugging session is exactly the case a timeline is most useful
   for, and exactly the case flat HTML struggles with).

**Recommendation: start with option 1** (pure CSS), ship it, and revisit
option 2 only if a real long-session usability complaint materializes —
this is the same "smallest first" discipline XX1 already used (ROI +
coverage shipped before `tes impact`, not simultaneously). Not decided
unilaterally here; flagged for GG's call before implementation.

## Sub-agent spawn trees

**Data**: `agent_name` + `parent_turn_idx` above already encode the tree —
no separate data model needed. A sub-agent spawn is a `tool_call` turn
whose `tool_name` indicates delegation (e.g. `Task`/`Agent`), followed by
turns carrying a different, non-null `agent_name`.

**Rendering**: indentation depth = tree depth (matches how this same
information already renders in `tes impact`'s directory-churn indentation
convention — reuse, not a new visual language). A collapsed sub-agent
branch shows a one-line summary (turn count, wall-clock span, tool names
used) — expand to see its own full timeline, recursively. **Cost/token
totals per sub-agent branch use the SAME persisted-at-score-time
discipline** — no on-demand recomputation that could silently break when
the source JSONL is gone.

## 6.3 — Information architecture, Calibration identity applied

Reusing the already-approved Calibration tokens (`assets/brand/BRAND.md`)
exactly, not introducing new ones:

- **Color**: `needle` (`#C9622B`) for the "currently selected/hovered"
  turn marker only — a live reading, never a verdict, per BRAND.md's own
  rule that `needle` never doubles as pass/fail. `calibrated`/`regression`
  reserved for turns the deterministic waste detector or judge axis has
  actually flagged (a `REPEATED-FAILED-RETRY` turn renders in
  `regression`, not a generic "error" red invented for this page).
  `graphite`/`tick` for the timeline's own rule lines and turn-index
  ticks — this is exactly the gauge component's own "tick ring" role,
  reused at 1:1 fidelity (turn index in place of a gauge's value ticks).
- **Type**: turn counts/durations/costs (the "readings") in Space
  Grotesk, matching BRAND.md's rule that Space Grotesk is reserved for
  values that *are* a reading, never body copy. Tool names, role labels,
  and captions in IBM Plex Sans/Mono.
- **Layout**: summary before detail (per the artifact-design UI principle
  already in force elsewhere in this project) — the page opens with
  totals (turn count, wall-clock span, error count, sub-agent count) above
  the timeline itself, not the reverse.

**Every number on screen carries its uncertainty where one exists (Q6.3's
own requirement)**: duration is captioned "elapsed, not isolated compute
time" (see 6.2); a sub-agent branch's cost total is captioned with the
same `cost_domain_of_validity`/unpriced-model handling `tes cost` already
uses — no new, uncaptioned number is introduced anywhere on this page that
doesn't already have a caption pattern established elsewhere in this
project.

## 6.4 — Differentiator: rigor over chart count

The reference dashboard (see `docs/audit/COMPETITIVE_GAP_ANALYSIS.md`)
labels its own signals "coaching heuristics, not claims" — an honest
disclaimer, but a blanket one that applies uniformly to everything it
shows, regardless of how strong the underlying evidence actually is for
any *specific* signal. This project's differentiator is not a bigger
disclaimer — it is doing what this whole engagement has already done
for every other feature: **state, per number, exactly what is measured
and what is not**, at the same granularity `tes cost`'s coverage
reporting and `tes impact`'s `prior_content_unknown` fraction already do.
A waste event gets its real proof-turn citation (already true today,
carried onto the timeline unchanged). A duration gets "elapsed," not
"thinking time." A sub-agent cost total gets the same unpriced-coverage
caveat the top-level cost already carries. No blanket disclaimer replaces
this — the specificity IS the rigor claim, and it has to be earned turn
by turn, not asserted once at the top of the page.

## 6.5 — Status

Design only. No code written. Routed for review — the one open decision
(6.2's pure-CSS vs. small-vanilla-JS choice) needs GG's call before
implementation starts; everything else in this doc (data model, legacy-row
handling, Calibration application, sub-agent tree structure) is a
proposal ready to implement once approved.
