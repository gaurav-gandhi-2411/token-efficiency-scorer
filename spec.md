# Project Spec: tracegauge — Dashboard UI Redesign (Iteration P9)

## Goal

Redesign the `tes serve` dashboard from the current functional-but-plain templates into an MLflow-style developer tool with consumer polish where it aids comprehension: clear visual hierarchy, cards, charts for the attribution breakdown, and a session-detail page that reads as a *diagnosis* (lead with the answer, support with the data). The redesign makes the P4-P8 diagnostic FEEL as good as it is — WITHOUT losing a single piece of the honesty that the plain version carried.

This is a presentation-layer phase. It changes how data is DISPLAYED. It does NOT change what is measured, scored, attributed, judged, or how — those are frozen. Same numbers, better-presented.

## The non-negotiable constraint (read first — this is the whole risk)

A UI redesign is the single most dangerous place for this project's honesty to silently erode. "Cleaning up" an interface is exactly how caveats get truncated, nuance gets simplified away, and warnings get softened for visual flow. So the hard rule: **every honest element that survived to the CURRENT rendered page MUST survive to the redesigned page, with equal or greater prominence.** Specifically, ALL of these must remain present and legible after the redesign (a checklist test verifies each):

1. **Domain-of-validity caveats** on every axis (token/trajectory/waste) — not hidden behind a tooltip the user won't hover, not truncated. May be collapsible IF the collapsed state still signals the caveat exists and one click reveals it — but the DEFAULT visible state must make clear the number is caveated.
2. **The dollar% AND token% shown together** in attribution — the divergence (95% tokens / 49% cost) IS the insight; a redesign must not show only one. Both, side by side, in any chart or card.
3. **"Over all billed tokens" basis label** on attribution + the "do not compare to real_tokens verdict" note — must survive, not get dropped for cleanliness.
4. **UNAVAILABLE rendered as complete/expected, NOT as an error** — the most common state (no judge) must look like a normal finished report, not a red broken thing. Polish makes this EASIER (a clean "judge not configured — enable with..." card), not harder.
5. **"Relative to YOUR OWN baseline, not absolute"** framing on token verdicts — survives.
6. **Self vs corpus vs building baseline source** label per session — survives.
7. **Waste proof-turns + evidence** accessible (can be behind a click/expand, but present).
8. **The API-judge consent + "may contain your code"** — if the UI surfaces judge enablement, the data-egress warning survives at full honesty.
9. **No composite score** — the redesign must NOT invent a single blended "tracegauge score" gauge/number to look slick. Three labeled axes + cost annotation, still separate.
10. **The deterministic one-line takeaway** — featured, not lost.

If a design choice forces dropping/softening any of these for visual reasons: the honesty wins, escalate. Prettier-at-the-cost-of-honest is a REGRESSION, not an improvement.

## Design decisions (made by consultant, since the user delegated design judgment — reviewable)

- **Style target:** MLflow-functional core (data-dense, developer-facing, fast, no heavy SPA) with consumer polish in the VISUAL HIERARCHY and the attribution charts. Reference feel: a clean dev-tool dashboard (think Linear/Vercel restraint) — generous whitespace, clear typography, cards for grouping, but NEVER at the expense of data density a developer wants.
- **Tech:** stay server-rendered Jinja2 (no SPA build step — keeps the moat-simple, dependency-light footprint). Polish via clean CSS + a LIGHTWEIGHT charting approach (inline SVG or a single small charting lib from cdnjs IF justified — escalate before adding a heavy JS dep; prefer inline SVG for the attribution bars, which are simple). NO localStorage/sessionStorage. Charts render from server-passed data.
- **Session list (landing):** a clean table/card hybrid — per session: task type, the cost, the band verdict (with baseline-source), waste indicator, and the one-line takeaway as the row's human-readable summary. Sortable/scannable. This is where the user lands; it should answer "which sessions cost the most / which have issues" at a glance.
- **Session detail (the diagnosis page):** lead with the ONE-LINE TAKEAWAY as a headline, then the COST attribution as the hero visual (a horizontal stacked bar or bars showing the buckets, dollar-ranked, with BOTH %s on hover/label), then the three axes each as a card carrying its verdict + caveat, then waste events (expandable with proof-turns), then the judge section (verdict or the enable-judge card). The page should read top-to-bottom as: "here's the answer (takeaway) -> here's where the money went (attribution) -> here's the detail (axes) -> here's the evidence (waste/judge)."
- **Attribution as the hero chart:** a horizontal stacked bar (cost-ranked), each segment a bucket, labeled with $ and both %s. This is the visual that makes "where did my tokens go" instant. Honest labels on every segment.
- **Baseline-status + trends:** baseline-status page gets the same card polish. (Trends stays PARKED — do not build trend charts; if a trends nav item exists, it shows the "building/parked" honest state.)
- **Color discipline:** use color to AID comprehension (e.g. waste = a distinct accent, cost severity = a gradient) but UNAVAILABLE is NEUTRAL (gray/calm), never red/alarm — it's expected, not an error. Don't use alarming red for "above_p75" either — it's "heavier than your lean runs," not "bad."

## Current state
tracegauge 0.4.0 BUILT + VERIFIED but PUBLISH HELD for this UI phase. P1-P8 complete. Current dashboard: functional Jinja2 templates (session list, session detail w/ attribution table, baseline-status), localhost-only, renders correctly but plain. All the honest elements (the 10 above) are present in the current plain version — the redesign must preserve every one. Detectors frozen, reports immutable.

## Scope

### In scope
1. Redesign the Jinja2 templates + CSS for: session list (landing), session detail (the diagnosis page), baseline-status. Clean visual hierarchy, cards, the attribution hero chart (inline SVG stacked bar), polish.
2. The attribution visual: cost-ranked stacked bar (or bar set), both %s per bucket, honest labels, dollar+token.
3. Preserve ALL 10 honesty elements (checklist test).
4. Keep it server-rendered, localhost-only, dependency-light. Inline SVG for charts preferred; escalate before any heavy JS dep.
5. Responsive enough to be usable (it's a local dev dashboard, not mobile-first, but shouldn't break at common window sizes).
6. A "honesty-survived" verification: a test (or a documented manual checklist run in the clean-room) confirming each of the 10 elements renders in the new templates.

### Out of scope
- Any change to scoring, attribution math, judge, cost, self-baseline, detectors (presentation only — same numbers).
- Building trends (parked — trends nav shows the parked/building state, no trend charts).
- A SPA / heavy frontend framework / build step.
- localStorage/sessionStorage (forbidden).
- New data egress (no CDN calls beyond optionally one charting lib from cdnjs IF escalated+justified; prefer inline SVG = zero external).
- Modifying reports 01-11.

### Hard rules
- HONESTY SURVIVES: all 10 listed elements present + legible in the redesign (checklist verified). Prettier never drops a caveat. Escalate if a design forces it.
- NO COMPOSITE SCORE invented for slickness. Three axes + cost annotation stay separate + labeled.
- UNAVAILABLE = calm/neutral/expected, never error-styled.
- PRESENTATION ONLY: same numbers, no scoring/math changes. Detectors frozen (git diff empty).
- Server-rendered, localhost-only, no new egress, no browser storage. Moat intact.
- Reports 01-11 immutable.

## Tech stack
- Jinja2 server-rendered templates + CSS (clean, modern, hand-written or minimal framework). Inline SVG for the attribution chart (simple stacked bars — no lib needed). If a chart genuinely needs a lib, escalate (prefer cdnjs, but prefer inline SVG more).
- Flask backend unchanged (it already passes the data; the redesign consumes the same context).
- pytest + a rendering checklist: assert the templates render with real data and each honesty element's text/marker is present in the output HTML.

## Architecture (changed)
```
tes/web/
├── templates/          # REDESIGNED: session_list, session_detail, baseline_status (+ a base layout)
│   ├── base.html       # NEW: shared layout, nav, the CSS
│   ├── session_list.html
│   ├── session_detail.html
│   └── baseline_status.html
├── static/             # CSS (+ any minimal assets; no heavy JS)
└── server.py           # unchanged data passing (or minor: pass chart-ready data structures)

tests/
├── test_ui_honesty_survives.py   # NEW: each of the 10 elements present in rendered output
└── (existing render tests updated for new templates)
```

## Verification commands
```yaml
- name: honesty-survives
  cmd: python -m pytest tests/test_ui_honesty_survives.py -v   # all 10 honesty elements render
  required: true
- name: templates-render
  cmd: python -m pytest tests/ -k "render or template or dashboard" -v
  required: true
- name: detectors-frozen
  cmd: git diff --exit-code tes/_waste_detectors.py && echo frozen
  required: true
- name: no-scoring-change
  cmd: python -m pytest -q   # full suite; scoring/attribution tests unchanged + green
  required: true
```

## Escalation rules
- If ANY design choice forces dropping/softening one of the 10 honesty elements: STOP, escalate — honesty wins over aesthetics.
- BEFORE adding any heavy JS/charting dependency (vs inline SVG): escalate.
- BEFORE inventing any composite/blended single-score visual: STOP — explicitly forbidden.
- If tempted to restyle UNAVAILABLE or above_p75 as alarm/error/red: STOP — they're expected/neutral states.
- Presentation only — if a "fix" requires touching scoring/attribution/judge math: out of scope, escalate.

## Budget
- Soft: 3-5 CC sessions. Local/$0. No GCP, no API, no server.

## Success criteria (verify ALL before done)
- Redesigned session list (landing), session detail (diagnosis page), baseline-status — MLflow-functional + consumer polish (hierarchy, cards, the attribution hero chart).
- Attribution rendered as a cost-ranked visual (inline SVG stacked bar) with BOTH dollar% and token% per bucket + honest bucket labels.
- Session detail reads top-down: takeaway -> attribution -> axes -> evidence.
- ALL 10 honesty elements present + legible (test passes): the 3 DOV caveats, dollar+token together, billed-basis label, UNAVAILABLE-as-complete, relative-not-absolute, baseline-source, waste proof-turns, API-judge code-exposure warning, NO composite, the one-line takeaway.
- UNAVAILABLE + above_p75 are neutral/calm, not error/alarm styled.
- Server-rendered, localhost-only, no browser storage, no new egress (inline SVG, or escalated+justified single lib).
- Same numbers as 0.4.0 (presentation only); scoring/attribution/judge/detector tests unchanged + green; detectors frozen.
- Clean-room: the redesigned dashboard renders from the installed wheel on real sessions (templates IN the wheel — remember the P3 template-packaging bug; verify templates ship).
- Reports 01-11 untouched. Full suite green. Version bump (0.4.0 still unpublished — this redesign ships AS 0.4.0, or bump to 0.5.0 — decide at publish).

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md + spec.md + the CURRENT tes/web/templates + server.py. Inventory the 10 honesty elements AS THEY CURRENTLY RENDER (so the redesign has a concrete preservation target). HOLD — show me the inventory (where each of the 10 currently lives) before redesigning, so we agree on what must survive.
2. Build base.html layout + CSS (the visual system: typography, cards, color discipline, UNAVAILABLE-neutral). HOLD for my read of the base look (describe or show the rendered shell) before applying to all pages.
3. Redesign session_detail (the diagnosis page) — takeaway headline, attribution hero SVG chart (cost-ranked, both %s), axis cards with caveats, waste expand, judge card. The honesty checklist test. HOLD for my read of the rendered detail page on a REAL session.
4. Redesign session_list (landing) + baseline_status.
5. honesty-survives test (all 10) + full suite + detectors frozen.
6. Clean-room: redesigned dashboard renders from the installed wheel (templates ship — verify, per the P3 bug). HOLD for my read of the rendered pages from the clean-room before publish.
```
