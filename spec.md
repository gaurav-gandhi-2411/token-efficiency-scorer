# Project Spec: tracegauge — Dashboard Intelligence + Sorting (Iteration 0.8.0)

## Goal

Bring the best features into the dashboard so users don't have to drop to the CLI for them, and make the session list sortable so users can find what they need at a glance. Two parts:

- **A — Session Intelligence in the dashboard:** a Patterns/Intelligence page (the ML archetypes + anomalies, visually) and an "Ask" chat panel (natural-language questions answered in the browser, grounded in real metrics). Today these are CLI-only (`tes patterns`, `tes ask`); surface them in the UI.
- **B — Sortable session list:** let users sort the session list by cost, date, waste, tokens, band verdict — so they can find the expensive ones, the wasteful ones, the recent ones, instantly. Today the order is fixed.

This is a presentation/surfacing phase — it exposes EXISTING engine + intelligence capabilities in the UI and adds sorting. It does NOT change scoring, attribution, the ML, the chat grounding, or any measurement. Same numbers, same honesty, more accessible.

## The non-negotiable constraint (the web "Ask" panel is the risk)

The web Ask panel runs the SAME conversational AI as the CLI `tes ask` — so it MUST carry the IDENTICAL honesty + privacy guards the CLI already proved out. No relaxation for being in a browser:
1. **Metrics-only egress** — the context sent to the LLM contains computed metrics + ML outputs, NEVER raw session content/code/prompts. (Same as CLI; tested.)
2. **Grounding** — answers ONLY from provided real metrics; "not measured" for out-of-scope; "I don't predict" for forecasts; no hallucinated numbers. (Same constrained prompt + the same grounding behavior.)
3. **API-judge / API-chat consent** — if the web Ask uses the API path, the per-session consent ("sends session data, may contain... actually METRICS not code, but data leaves") is shown in the UI before any send. Local (Ollama) path = no egress. Default = no silent send.
4. **Small-corpus floor** — the web Patterns view and Ask panel honor the same <30-content-session floor: "not enough sessions for stable patterns yet," NOT confidently-described noise clusters. (Same by-construction enforcement — archetypes absent from context below the floor.)
5. **Descriptive not predictive, validity reported** — the web Patterns view shows the archetypes with their validity (silhouette, N, the "descriptive only" DOV), the same honest framing as `tes patterns`. No quality labels, no prediction.

If the web surfacing weakens ANY of these (sends code, drops the floor, lets the chat predict, hides the consent): STOP, escalate. Surfacing must not erode the guards the CLI established.

## Current state
tracegauge 0.7.1 LIVE on PyPI — Session Intelligence (tes patterns / tes ask) works on clean install (numpy/sklearn now declared core deps; project-wide import-closure test guards recurrence). The ML (k=3, silhouette ~0.45, validated), the chat (grounded, metrics-only, honesty guards), the engine, the dashboard (session list + detail + baseline-status), the frictionless UX — all live. Intelligence is CLI-only; the session list is fixed-order. Detectors frozen, reports immutable.

## Scope
### In scope
A. **Dashboard Intelligence:**
   1. A **Patterns page** (nav item) showing the ML archetypes (names, sizes, dominant features), the anomalies (which sessions deviate + why), the validity (silhouette/N), and the "descriptive only" DOV. Honest framing identical to `tes patterns`. Reuses the existing intelligence cache.
   2. An **Ask panel** (on the Patterns page or its own) — a text input where the user types a question, it calls the SAME chat backend (metrics-only context, constrained prompt, local-or-API), and shows the grounded answer in the browser. Carries ALL the CLI guards.
   3. **Judge enablement from the UI** — the trajectory UNAVAILABLE card already explains the on-ramps; make it actionable where feasible (e.g. clear instructions / a "judge status" indicator showing whether Ollama is detected). Do NOT auto-send to an API judge without consent. (If full in-UI enablement is complex, a clear status + instructions is acceptable — escalate the scope call.)
B. **Sortable session list:**
   4. Make the session list sortable by: cost, date (scored_at), waste event count, real_tokens, band verdict. Clickable column headers (server-side sort via a query param, e.g. ?sort=cost&dir=desc — keeps it server-rendered, no SPA). Default sort = date desc (most recent first) or cost desc (most expensive first) — pick the most useful default.
   5. Sort must be honest: sorting by "band verdict" orders by the verdict, not by a quality judgment; sorting by cost is the annotation, still not a score. Labels unchanged.

### Out of scope
- Any change to scoring / attribution / the ML / chat grounding / cost / detectors (surfacing + sorting only; same numbers).
- A SPA / heavy JS framework (stay server-rendered Jinja2; sorting via query param; the Ask panel can use minimal JS for the input→fetch→render, but no framework).
- New egress beyond the already-consented API chat/judge. Weakening any CLI-established guard.
- Trends (still parked). Reports 01-11 immutable.

### Hard rules
- The web Ask carries IDENTICAL guards to CLI tes ask: metrics-only egress, grounding, "not measured"/"I don't predict", small-corpus floor, consent for API. Tested.
- Patterns view: descriptive not predictive, validity shown, honest DOV, no quality labels.
- Surfacing/sorting only — engine + ML + chat logic unchanged. Detectors frozen (git diff empty).
- Server-rendered; sorting server-side via query param; no SPA; no browser storage.
- Same honesty elements on session list survive (the existing 10 — baseline-source, UNAVAILABLE-neutral, etc.); sorting must not drop them.

## Tech stack
- Jinja2 templates + the existing Flask server (add routes/params; reuse tes/intelligence/ for the Patterns data + chat). Sorting = server-side sort on the query param. Ask panel = a small JS fetch to a new endpoint that calls the chat backend, renders the answer. Minimal JS, no framework, no browser storage.
- Reuse: tes/intelligence/cache (archetypes), tes/intelligence/chat (the grounded explainer — SAME code path as CLI), the existing session-list query (add ORDER BY on the sort param).
- pytest: web-Ask-carries-guards (metrics-only, grounding, floor, consent), sort-correctness, sort-preserves-honesty-elements.

## Architecture
```
tes/web/
├── server.py            # + /patterns route (archetypes+anomalies+validity), + /ask endpoint
│                        #   (calls tes/intelligence/chat — SAME backend as CLI), + sort param on session list
├── templates/
│   ├── patterns.html    # NEW: archetypes, anomalies, validity, DOV, the Ask panel
│   ├── session_list.html# + sortable column headers (query-param links), default sort
│   └── base.html        # + Patterns nav item
└── static/              # minimal JS for the Ask panel (input -> fetch /ask -> render)
tests/
├── test_web_ask_guards.py     # web Ask: metrics-only, grounded, not-measured/predict, floor, consent
├── test_web_patterns.py       # patterns page renders archetypes+validity+DOV, honest framing, floor honored
└── test_session_sort.py       # each sort key orders correctly; honesty elements survive sorting
```

## Verification commands
```yaml
- name: web-ask-guards
  cmd: python -m pytest tests/test_web_ask_guards.py -v   # identical guards to CLI tes ask
  required: true
- name: web-patterns-honest
  cmd: python -m pytest tests/test_web_patterns.py -v     # descriptive, validity shown, floor honored
  required: true
- name: session-sort
  cmd: python -m pytest tests/test_session_sort.py -v     # sorts correct + honesty survives
  required: true
- name: import-closure
  cmd: python -m pytest tests/test_all_tes_imports_are_declared.py -v   # the 0.7.1 guard — any new dep declared
  required: true
- name: detectors-frozen
  cmd: git diff --exit-code tes/_waste_detectors.py && echo frozen
  required: true
- name: full-suite
  cmd: python -m pytest -q
  required: true
```

## Escalation rules (autonomous mode — escalate ONLY these)
- PUBLISHING 0.8.0 to PyPI (irreversible; user's token).
- If the web Ask could send CODE (not metrics), drop the small-corpus floor, let the chat predict/invent, or auto-send to API without consent — STOP, escalate (the guards must not erode in the web surface).
- If judge in-UI enablement turns out complex/risky — escalate the scope call (status+instructions is an acceptable fallback).
- New dependency for the Ask-panel JS or anything — declare it AND it must pass the import-closure test (the 0.7.1 lesson); if it's a JS/CDN dep, escalate (prefer none).
- Touching frozen detectors / engine / ML / chat-grounding logic — out of scope, escalate.
- Otherwise DECIDE AND ACT: UI layout, sort default, Ask-panel UX, page structure — your call; report.

## Budget
- Soft: 3-5 CC sessions. Local/$0 (web Ask testing uses local Ollama or minimal API with the user's key — confirm before real API calls).

## Success criteria (verify ALL)
- Dashboard has a Patterns page: archetypes + anomalies + validity + honest DOV, descriptive-not-predictive, small-corpus floor honored.
- Dashboard has an Ask panel: grounded answers in the browser via the SAME chat backend, carrying ALL CLI guards (metrics-only egress, grounding, not-measured/predict, floor, API consent) — tested identical to CLI.
- Judge status/enablement surfaced in the UI (full enablement or clear status+instructions — whichever scope lands).
- Session list sortable by cost / date / waste / tokens / verdict (server-side, query param); honest labels + the existing honesty elements survive sorting.
- Surfacing/sorting only — engine + ML + chat unchanged (same numbers); detectors frozen; import-closure test green (any new dep declared); reports 01-11 untouched; full suite green.
- 0.8.0 built, clean-roomed (--no-default-packages env: patterns page, Ask panel, sorting all work from the installed wheel), PUBLISHED, fresh-install confirmed.

## Build order (orchestrator decides details autonomously)
1. Read CURRENT_STATE.md + spec.md + tes/web/server.py + templates + tes/intelligence/chat + cache. Confirm context + the web-Ask-carries-identical-guards constraint in 4-6 lines.
2. Sortable session list (B) — server-side sort param, clickable headers, default, honesty elements + labels preserved. Tests. (Do this first — lower risk, immediately useful.)
3. Patterns page (A1) — archetypes/anomalies/validity/DOV, honest framing, floor honored. Tests.
4. Ask panel (A2) — new /ask endpoint calling the SAME chat backend; minimal JS input→fetch→render; ALL guards carried. The guard tests (metrics-only, grounded, floor, consent) are the gate here — HOLD and show me the web-Ask guard test results + a real browser Q&A (incl. an out-of-scope "I don't predict") before proceeding.
5. Judge status/enablement in UI (A3) — or status+instructions fallback; escalate if complex.
6. Full suite + import-closure + detectors frozen + clean-room (--no-default-packages). Bump 0.7.1 -> 0.8.0, CHANGELOG. ESCALATE the publish.
