# Project Spec: token-efficiency-scorer — `tes serve`: Local Scoring Service + Dashboard (Iteration P2)

## Goal

Turn the manual P1 CLI into a polished, always-available local service: `tes serve` launches a background watcher that automatically scores finished Claude Code sessions, plus an MLflow-style web dashboard on localhost where scores accumulate over time. Zero-config experience: `pip install`, `tes serve`, open the browser, watch your CC sessions get scored automatically.

The deployment stays self-hosted and moat-preserving (localhost only, no data off-machine, redaction on). The background loop auto-scores the two FAST LOCAL AXES (token economy + deterministic waste) on every finished session; the trajectory-quality judge is OFF by default in the background (it needs an 18GB GPU and a background loop running a 30B model on every session is a footgun) and runs only on manual `tes score` or explicit opt-in.

This is also the natural home for the corpus-contribution path (design-only this phase): a local service accumulating scored sessions is the foundation for eventual opt-in redacted-signal contribution that de-biases the B5 high-waste-infra-outlier calibration.

## The disciplines carried from P1 (non-negotiable)

1. **Output honesty.** Every axis carries its domain-of-validity (the ThreeAxisResult constants from P1) — in the dashboard UI AND the API/JSON, not just the CLI. UNAVAILABLE stays UNAVAILABLE, never coerced. No composite score. Waste events keep proof-turns. The dashboard must not strip caveats for visual cleanliness — a chart of "efficiency over time" that hides the scope-gate or the corpus caveat is worse than no chart.
2. **The moat by construction.** The watcher and dashboard bind to localhost only. No data leaves the machine. No telemetry, no phone-home. Redaction on by default at ingestion. A verification check confirms the server binds 127.0.0.1, not 0.0.0.0.
3. **Behavior preservation.** P2 changes WHEN/HOW scoring is triggered and WHERE results are stored — it does NOT change the scores. The P1 `tes/score.py` pipeline is called unchanged; the same session produces the same ThreeAxisResult whether scored manually (P1) or by the watcher (P2). A test confirms watcher-scored == manually-scored.
4. **The judge footgun guard.** Background auto-scoring runs token + waste ONLY. The judge is OFF in the background unless the user explicitly opts in (`--background-judge` or a config flag), and opt-in surfaces a clear "this runs a 30B model on your GPU for every session" warning. Manual `tes score` is unchanged (judge runs if available).

## Current state

See CURRENT_STATE.md. P1 complete:
- `tes/` SDK package: adapt, classify, baselines, waste, judge, score (ThreeAxisResult), report. 93 tests green.
- `tes score <path>` CLI: manual three-axis scoring, tiered judge, caveats inline, `--json`, redaction on.
- Moat verified (no-network on local axes), behavior-preservation golden in place.
- Judge: Qwen3-30B-A3B via local Ollama, tiered (UNAVAILABLE when absent).
- Reports 01-11 immutable.

## The "detect finished sessions" problem (resolve EARLY — feasibility)

A CC session is a JSONL file that grows during work. "Finished" has no guaranteed clean marker. The robust design is **scheduled-scan + file-stability**, with a session-end hook as an OPTIONAL enhancement IF CC supports it (VERIFY — do not assume):
- **EARLY TASK:** investigate whether current Claude Code exposes a session-end hook / lifecycle event (settings hooks, a SessionEnd event, etc.). Report what exists. If a clean hook exists, support it as an optional fast-path trigger. If not, scheduled-scan + file-stability is the sole mechanism — and that's fine.
- **File-stability heuristic (the reliable core):** a session JSONL not modified for N minutes (configurable, default e.g. 5 min) is "stable enough" to score. This is the no-assumptions baseline that works regardless of CC's hook support.
- **Scheduled scan:** every N minutes, scan the CC projects dir, find sessions that are new-or-changed-since-last-scan AND now file-stable, score them.

## Incremental scoring (required — no full re-scans)

The watcher MUST track what it has already scored and only score new-or-changed sessions:
- A "scored ledger" in the store: session_id + source-file mtime/hash + scored-at timestamp.
- On each scan: a session is scored only if (not in ledger) OR (file changed since last scored). Otherwise skipped.
- This keeps the scan O(new sessions), not O(all history), and prevents hammering the judge (if opted-in) on already-scored sessions.

## Scope

### In scope
1. PERSISTENCE: local SQLite (`~/.tes/tes.db` or configurable). Tables: scored sessions (full ThreeAxisResult serialized + domain-of-validity + waste proof-turns + source path + scored-at + source mtime/hash for the incremental ledger). Zero external DB.
2. WATCHER: `tes serve` starts a background scan loop (scheduled-scan + file-stability; + session-end hook if CC supports it per the early investigation). Incremental via the ledger. Scores token + waste axes (NOT judge, by default) via the unchanged P1 pipeline. Writes results to SQLite.
3. WEB DASHBOARD: MLflow-style local web UI on localhost:PORT (default e.g. 4747, configurable), launched by the same `tes serve`. Shows:
   - A list/table of scored sessions over time (session id, task type, scored-at, the three axis verdicts at a glance, waste event count).
   - A per-session detail view: full three-axis report with ALL caveats (the domain-of-validity strings), waste events with proof-turns, token band, trajectory verdict-or-UNAVAILABLE.
   - Trend/aggregate views (e.g. waste events over time, task-type distribution) — WITH caveats visible, never a decontextualized "efficiency trending up" headline.
   - Clear UNAVAILABLE rendering (trajectory UNAVAILABLE-in-background by default; token UNAVAILABLE when scope-gated) — shown as complete/expected, not as errors.
4. ONE COMMAND: `tes serve` starts watcher + web UI together (MLflow-style). Flags: `--port`, `--scan-interval`, `--stability-window`, `--cc-path` (default auto-detect ~/.claude/projects), `--background-judge` (opt-in, with warning), `--no-judge` irrelevant in background (off by default anyway).
5. MANUAL PATH PRESERVED: `tes score <path>` (P1) still works unchanged, judge-if-available, and its results ALSO write to the same SQLite store so manual + auto scores share one dashboard.
6. JUDGE OPT-IN (background): `--background-judge` enables judge in the watcher, gated behind a clear one-time warning about continuous GPU use. Without it, dashboard trajectory = UNAVAILABLE (background), with the same honest message as P1.
7. REDACTION: on by default at ingestion, before anything is stored or shown.
8. CORPUS-CONTRIBUTION (design-only): document in README/roadmap how opt-in redacted-signal export would work from the SQLite store (the de-biasing path). Do NOT build the upload pipeline. Just ensure the store schema doesn't preclude it.

### Out of scope
- Any non-localhost binding, remote access, multi-user, auth (it's a local single-user dev tool).
- Hosted judge / data-leaves-machine (deferred, moat).
- Smaller-judge swap (re-validation phase).
- Building the corpus-upload pipeline (design-only).
- Cross-agent (non-CC).
- Changing any score / baseline / judge config / detector (P2 changes orchestration + storage + UI, NOT the scoring).
- Modifying reports 01-11.
- Auth/accounts/cloud — none.

## Tech stack
- Python. Web UI: a lightweight local server — Flask or FastAPI + a simple templated frontend (server-rendered or a minimal JS); MLflow-style means functional/clean, not a heavy SPA. Pick the lightest thing that gives a usable localhost dashboard; escalate if reaching for a heavy framework.
- SQLite via stdlib `sqlite3` (no ORM needed, or a thin one).
- Watcher: a scan loop (threading/scheduler) — simple and robust over a complex daemon. `watchdog` (filesystem events) is OPTIONAL; scheduled-scan + stability is the reliable baseline.
- Reuse `tes/` scoring unchanged.
- pytest: watcher-scored == manually-scored (behavior preservation); incremental-ledger correctness; localhost-bind verification; caveats-present in dashboard payload.

## Architecture (target — orchestrator may refine)
```
tes/
├── (P1 modules unchanged: adapt, classify, baselines, waste, judge, score, report)
├── store.py            # SQLite: schema, write ThreeAxisResult, scored-ledger, query for dashboard
├── watcher.py          # scan loop: discover CC sessions, file-stability, incremental via ledger, score token+waste, write store
├── hooks.py            # OPTIONAL: CC session-end hook integration IF it exists (per early investigation)
└── web/
    ├── server.py       # localhost web app (Flask/FastAPI), binds 127.0.0.1 ONLY
    ├── templates/      # session list, session detail (with caveats), trends
    └── static/

cli.py                  # add `tes serve` (watcher + web together); `tes score` unchanged + now also writes to store
pyproject.toml          # add web/sqlite deps; `tes serve` entry
tests/
├── test_store.py                 # write/read ThreeAxisResult round-trip, ledger correctness
├── test_watcher_incremental.py   # only new/changed sessions scored; no re-score of unchanged
├── test_watcher_behavior_preservation.py  # watcher-scored == tes score manual, same session
├── test_localhost_bind.py        # server binds 127.0.0.1, not 0.0.0.0
├── test_dashboard_caveats.py     # dashboard payload carries domain-of-validity per axis
└── test_judge_off_in_background.py # background loop does NOT call judge unless opted in
```

## Key design decisions (resolve early, escalate)
1. CC SESSION-END HOOK: investigate + report whether current CC has a usable session-end hook. Design the hook fast-path ONLY if it genuinely exists; otherwise scan+stability is the sole mechanism. Do not assume.
2. WEB FRAMEWORK: lightest option that delivers a clean localhost dashboard (Flask + server-rendered templates is likely simplest; FastAPI if an API is wanted for the SDK too). State the choice + why. Avoid heavy SPA frameworks unless justified.
3. STABILITY WINDOW + SCAN INTERVAL defaults: pick sane defaults (e.g. 5-min stability, 2-min scan) and make them configurable. State the reasoning.
4. STORE SCHEMA: must serialize the full ThreeAxisResult including domain-of-validity strings + waste proof-turns, plus the incremental ledger fields (mtime/hash). Design so opt-in corpus export is possible later (don't preclude it).
5. DASHBOARD HONESTY: how trends/aggregates show caveats. A "waste over time" chart is fine; a "your efficiency score" gauge that blends axes is NOT (no composite, P1 discipline). Decide the views; keep each axis labeled + caveated.
6. JUDGE-IN-BACKGROUND OPT-IN UX: the warning copy + how the dashboard shows trajectory-UNAVAILABLE-because-background vs trajectory-UNAVAILABLE-because-no-judge (subtly different; both honest).

## Verification commands
```yaml
- name: watcher-behavior-preservation
  cmd: python -m pytest tests/test_watcher_behavior_preservation.py -v   # watcher score == manual tes score, same session
  required: true
- name: incremental-ledger
  cmd: python -m pytest tests/test_watcher_incremental.py -v             # unchanged sessions not re-scored
  required: true
- name: localhost-only
  cmd: python -m pytest tests/test_localhost_bind.py -v                  # binds 127.0.0.1, not 0.0.0.0
  required: true
- name: dashboard-caveats
  cmd: python -m pytest tests/test_dashboard_caveats.py -v               # domain-of-validity in dashboard payload
  required: true
- name: judge-off-in-background
  cmd: python -m pytest tests/test_judge_off_in_background.py -v
  required: true
- name: full-suite-still-green
  cmd: python -m pytest -q                                              # P1's 93 + P2 tests all pass
  required: true
- name: installable-serve
  cmd: pip install -e . && tes serve --help
  required: true
```

## Escalation rules
- After the CC-hook investigation: report whether a session-end hook exists; HOLD on the trigger design if it changes the watcher architecture.
- BEFORE choosing a web framework heavier than Flask/FastAPI+templates: escalate.
- If watcher-scored != manually-scored for any session: STOP — P2 must not change scores.
- BEFORE binding anything other than localhost, adding telemetry, or any network egress: not in scope — escalate.
- BEFORE building the corpus-upload pipeline: design-only this phase; escalate if tempted to build.

## Hard rules
- MOAT: localhost bind only (127.0.0.1), no data off-machine, no telemetry/phone-home, redaction on by default. Verification enforces the bind.
- JUDGE OFF IN BACKGROUND by default; opt-in only, with a clear continuous-GPU warning.
- BEHAVIOR PRESERVATION: P2 calls the P1 pipeline unchanged; same session -> same scores. No score/baseline/detector/judge-config changes.
- OUTPUT HONESTY in the UI: every axis caveated; UNAVAILABLE preserved + shown as complete-not-error; no composite/blended score; waste proof-turns shown.
- INCREMENTAL: never full-re-score; ledger-gated.
- Reports 01-11 immutable. No human labels. .env in-process only.

## Budget
- Soft: 3-5 CC sessions (web UI + watcher + store + tests is the largest build yet).
- Anthropic API unchanged. GCP: none expected (background runs local axes; judge opt-in uses the user's own local Ollama, not our GPU). Escalate if any GPU need arises.

## Success criteria (verify ALL before done)
- `pip install -e .` then `tes serve` launches watcher + localhost web dashboard in one command.
- Watcher auto-detects finished CC sessions (scan+stability; + hook if CC supports it) and scores token+waste incrementally into SQLite; unchanged sessions not re-scored (ledger test passes).
- Watcher-scored results == manual `tes score` results for the same session (behavior-preservation test passes).
- Web dashboard on localhost shows: session list over time, per-session three-axis detail with ALL caveats, trend views with caveats visible; UNAVAILABLE rendered as complete/expected.
- Judge OFF in background by default (test passes); `--background-judge` opt-in works with the GPU warning; manual `tes score` judge behavior unchanged.
- Server binds 127.0.0.1 only (test passes); no telemetry/egress; redaction on by default.
- Dashboard payload carries domain-of-validity per axis (test passes); no composite score anywhere in the UI.
- Manual `tes score` results also land in the store (shared dashboard).
- Full test suite green (P1's 93 + P2 additions). Installable. Reports 01-11 untouched. Git clean.
- README updated: `tes serve` usage, the judge-off-in-background default + opt-in, domains of validity, corpus-contribution roadmap (design-only).

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md + reports 10/11 + spec.md + P1's tes/score.py + report.py. Internalize: moat, honesty, behavior-preservation, judge-footgun-guard.
2. INVESTIGATE the CC session-end hook question; report what exists; HOLD if it reshapes the trigger design.
3. store.py: SQLite schema (full ThreeAxisResult + caveats + proof-turns + incremental ledger). Round-trip test. HOLD for schema read.
4. watcher.py: scan+stability + incremental ledger; scores token+waste via unchanged pipeline; writes store. Behavior-preservation + incremental + judge-off tests.
5. web/server.py + templates: localhost dashboard (list, detail-with-caveats, trends-with-caveats). localhost-bind + dashboard-caveats tests.
6. cli.py: `tes serve` (watcher+web together); `tes score` now also writes to store. `--background-judge` opt-in + warning.
7. pyproject deps + installability + README. Full suite green.
8. HOLD for consultant read — include a SAMPLE of the dashboard (screenshot-equivalent: the rendered session-detail HTML/text) on a real judge-absent session, to confirm honesty renders in the UI.
