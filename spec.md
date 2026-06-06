# Project Spec: token-efficiency-scorer — Packaging: CLI + SDK (Iteration P1)

## Goal

Package the validated three-axis scorer into an installable, self-hosted CLI (with an SDK engine underneath) that a developer runs on their own Claude Code logs and gets a three-axis report. The deployment model is TIERED (option D): the two no-GPU axes (deterministic waste, token economy) run fully locally for everyone, always; the trajectory-quality axis (the Qwen judge) runs IF a local judge model is available and is cleanly marked UNAVAILABLE if not. No hosted endpoint, no data leaving the user's environment — the self-hosted moat (your data never leaves your machine, secrets scrubbed at ingestion) is the product's core differentiator and must be preserved by construction.

This is the first phase where the work becomes a usable product and demoable. It is also the mechanism that addresses the corpus limitation B5 characterized: a tool people run on their own sessions is how diverse real-world CC data is collected (opt-in, redacted) to eventually de-bias the high-waste-infra-outlier calibration.

## The core discipline (read first — packaging's equivalent of the credibility rule)

The credibility phases (B1-B5) established WHAT each axis means and its limits. Packaging must not UNDO that honesty when wrapping it for users. The failure mode: the CLI quietly drops a caveat the reports carefully established — printing "below baseline" without the scope-gate UNAVAILABLE logic, a waste count without proof-turns, or a token band without its domain-of-validity. **The CLI's output must carry the same caveats the reports do.** Specifically:
- Every axis prints its DOMAIN OF VALIDITY inline (token: scope-gated + calibrated to a high-waste infra corpus; trajectory: positive-signal-scoped, not accuracy-validated; waste: observable-invariant only, with proof-turns).
- UNAVAILABLE stays UNAVAILABLE — never silently coerced to a number (token axis below scope gate; trajectory axis when no judge; PATH-B on current CC per the version issue).
- No composite score. Three labeled signals, exactly as the reports established.
- Waste events always carry their proof-turns (the auditability that makes them defensible).
The product is only as honest as its output surface. A packaged tool that strips the caveats is worse than the reports, not a productization of them.

## Current state

See CURRENT_STATE.md. B1-B5 complete; reports 01-11 immutable. The scoring components exist as scripts:
- `scripts/adapters/claudecode_adapter.py` — CC JSONL → digest (FROZEN, battle-tested on 181 + 1,053 sessions).
- `scripts/task_classifier.py` — 5-type classifier.
- `scripts/build_baselines.py` / `data/cc_baselines.json` — token baselines (median + [p25,p75] band, p10 scope gates).
- `scripts/efficiency_score.py` — three-axis scorer (token band + trajectory verdict + waste events).
- `scripts/waste_detectors.py` — REPEATED-FAILED-RETRY + REDUNDANT-READ (FROZEN). NOTE: PATH-B is version-fragile on CC v2.1.38+ (report 11 §4) — the packaging phase MAY apply the documented dual-format fix (`^\d+\t|^\s+\d+→`) since freezing was a B5 constraint, not a permanent one — but if so, treat it as a real detector change with tests, not a silent edit (see decision 5).
- Judge: Qwen3-30B-A3B via Ollama, v3 prompt, locked config. Needs ~18GB VRAM (not laptop-runnable).
- Secret redaction at ingestion (built, 17 patterns + scan gate).

These are advisory scripts run by hand. P1 turns them into an installed tool.

## Scope

### In scope
1. SDK (the engine): a clean Python package (`tes/` or similar) exposing the pipeline programmatically — adapt → classify → score (token, waste) → optional judge → three-axis result object. Refactor the existing scripts into importable modules; preserve behavior exactly (the scoring logic is validated — packaging must not change results).
2. CLI (the surface): `tes score <path-to-cc-logs>` style entry point that runs the SDK and prints a human-readable three-axis report, with each axis's domain-of-validity inline and UNAVAILABLE handled honestly.
3. TIERED judge (option D):
   - Deterministic waste + token axes: always run, fully local, no model.
   - Trajectory axis: detect whether a local judge is available (Ollama running + a configured capable model). If yes, run it. If no, the axis prints UNAVAILABLE with a clear message ("trajectory-quality requires a local judge model; see setup") — NOT an error, NOT a silent skip, NOT a coerced number.
   - Judge config (model, endpoint) is user-configurable; default expects local Ollama + the validated Qwen model. Never sends data off-machine.
4. Input handling: point the CLI at a CC logs directory (e.g. `~/.claude/projects/.../*.jsonl`); discover + adapt sessions through the frozen adapter. Handle the v2.1.38 format (see decision 5).
5. Secret redaction ON by default at ingestion (the moat's privacy half) — never optional-off without an explicit flag + warning.
6. Output: per-session and/or aggregate three-axis report; a machine-readable mode (JSON) for the SDK/automation path.
7. Packaging hygiene: installable (`pip install` from repo / pyproject.toml), dependency manifest, a README that states the tool's domains of validity (carries the reports' honesty to the user-facing docs).

### Out of scope (this phase)
- Hosted judge endpoint / any data-leaves-machine path (contradicts the moat; explicitly deferred).
- Swapping to a smaller judge model (that's a re-validation phase, not packaging).
- The opt-in corpus-contribution mechanism (design it conceptually in the README/roadmap, but BUILDING the upload/collection pipeline is a later phase — P1 just must not preclude it).
- A GUI / dashboard / web UI.
- Re-validating or re-calibrating any axis (the scores are locked; packaging preserves them).
- Modifying reports 01-11 (immutable).
- Cross-agent support (non-CC) — CC-only, per B5.

## Tech stack
- Python package, pyproject.toml, console-entry-point CLI (argparse/click).
- Ollama Python client for the optional judge (local only).
- Reuse existing deps; no new heavy deps without escalation.
- pytest: the critical test is BEHAVIOR-PRESERVATION (see verification) — the packaged pipeline must produce identical scores to the validated scripts on the existing pool.

## Architecture (target structure — orchestrator may refine)
```
tes/                              # the SDK package
├── __init__.py
├── adapt.py                      # wraps frozen claudecode_adapter
├── classify.py                   # task classifier
├── baselines.py                  # token baseline + scope gate
├── waste.py                      # wraps frozen waste_detectors
├── judge.py                      # OPTIONAL judge: detect-availability + run-or-UNAVAILABLE
├── score.py                      # orchestrates the three axes -> result object
├── report.py                     # human-readable + JSON output, caveats inline
└── data/cc_baselines.json        # bundled baseline artifact

cli.py / tes/__main__.py          # console entry point: `tes score <path>`
pyproject.toml                    # installable, entry point, deps
tests/
├── test_behavior_preservation.py # CRITICAL: packaged == validated scripts on pool
├── test_judge_tiering.py         # judge-available -> runs; judge-absent -> UNAVAILABLE (not error)
├── test_caveats_present.py       # output carries domain-of-validity per axis
└── test_redaction_default_on.py  # secrets scrubbed by default
README.md                         # states domains of validity (reports' honesty -> docs)
```
Existing `scripts/` can remain (or be thinned to thin wrappers around `tes/`) — but the frozen files (waste_detectors.py, claudecode_adapter.py) must not change behavior; if refactored into `tes/`, behavior-preservation tests prove equivalence.

## Key design decisions (resolve early, escalate)
1. SDK/CLI BOUNDARY: the SDK returns a structured three-axis result object (each axis: value + status + domain-of-validity); the CLI formats it. Keep all honesty (UNAVAILABLE, caveats) in the result OBJECT, so both CLI and SDK consumers get it — not bolted on in CLI formatting only.
2. JUDGE-AVAILABILITY DETECTION: how the CLI decides a judge is usable (Ollama reachable + configured model present + a quick health probe). Define the check; default to the validated Qwen config; fail to UNAVAILABLE gracefully, never crash.
3. JUDGE PERFORMANCE EXPECTATION: Qwen-30B is slow even on GPU and unusable on laptop — so for most users the trajectory axis will be UNAVAILABLE in practice. The CLI must make this a clean, expected state with a helpful message, not a degraded-feeling failure. Decide the messaging.
4. OUTPUT FORMAT: human-readable default (the three labeled sections with caveats, like efficiency_score.py's current output) + `--json` for machine consumption. Decide the exact human layout; it must show all three axes' domains of validity.
5. PATH-B FIX (the one allowed behavior change): report 11 documented PATH-B silently broken on CC v2.1.38+. Packaging is the right time to apply the dual-format fix (`^\d+\t|^\s+\d+→`) so the shipped tool works on CURRENT CC. BUT: this is a real detector change — it must come with (a) unit tests for both formats, (b) a re-run on the existing pool confirming pre-v2.1.38 PATH-B counts are unchanged (the tab format still matches), (c) a note that PATH-B now covers both. Escalate before applying; if applied, waste_detectors.py is no longer "frozen" and CURRENT_STATE must reflect the version. If you'd rather keep it frozen and ship PATH-B-as-UNAVAILABLE-on-new-CC, that's also defensible — decide with the consultant.
6. CORPUS-CONTRIBUTION HOOK (design-only): the README/roadmap should describe how opt-in redacted-signal contribution would work (since it's the path to fixing the B5 corpus limitation), but P1 does NOT build the upload pipeline. Just don't architect in a way that precludes it.

## Verification commands
```yaml
- name: behavior-preservation
  cmd: python -m pytest tests/test_behavior_preservation.py -v   # packaged pipeline == validated scripts on pool sessions
  required: true
- name: judge-tiering
  cmd: python -m pytest tests/test_judge_tiering.py -v           # absent judge -> UNAVAILABLE, not error
  required: true
- name: caveats-present
  cmd: python -m pytest tests/test_caveats_present.py -v         # each axis prints its domain of validity
  required: true
- name: redaction-default-on
  cmd: python -m pytest tests/test_redaction_default_on.py -v
  required: true
- name: installable
  cmd: pip install -e . && tes score --help
  required: true
- name: no-network-in-local-path
  cmd: grep -rL 'requests.post\|http' tes/score.py tes/waste.py tes/baselines.py   # local axes make no network calls
  required: true
```

## Escalation rules
- BEFORE the PATH-B fix (decision 5): confirm the approach (apply-with-tests vs keep-frozen-and-UNAVAILABLE) with the consultant.
- BEFORE refactoring frozen files into `tes/`: the behavior-preservation test must exist FIRST and pass, proving the refactor changed no scores.
- If ANY axis's score changes during refactor: STOP — packaging must preserve validated behavior; a score change is a regression, not a packaging step.
- BEFORE adding any data-leaves-machine path: not in scope — escalate, don't build.
- BEFORE new heavy dependencies.

## Hard rules
- THE MOAT: no axis sends user data off-machine. The local axes make zero network calls. The judge is local-only. Verification enforces no-network in the local path.
- OUTPUT HONESTY: every axis carries its domain of validity; UNAVAILABLE is never coerced to a number; no composite score; waste events keep proof-turns. (The packaging discipline.)
- BEHAVIOR PRESERVATION: packaged scores == validated scores on the pool. Packaging changes the interface, never the numbers.
- Secret redaction ON by default; off only with explicit flag + warning.
- Reports 01-11 immutable. Scores/baselines/judge config locked (the only allowed behavior change is the escalated PATH-B fix, decision 5).
- No human labels. .env in-process only; cost log append-only.

## Budget
- Soft: 2-4 CC sessions (refactor + CLI + tests is more code than a research phase).
- Anthropic API: unchanged. GCP: only if the PATH-B re-run or a judge smoke-test needs the GPU — escalate; mostly local/$0.

## Success criteria (verify ALL before done)
- SDK package importable; pipeline exposed as a clean API returning a three-axis result object (each axis: value + status + domain-of-validity).
- CLI installable (`pip install -e .`), `tes score <path>` runs end-to-end on real CC logs.
- Behavior-preservation test passes: packaged pipeline reproduces the validated scores on pool sessions exactly.
- Tiered judge works: present -> trajectory axis runs; absent -> UNAVAILABLE with a clear helpful message, no crash, no coerced number.
- Output carries every axis's domain of validity; UNAVAILABLE preserved; no composite; waste proof-turns present. (caveats-present test passes.)
- Secret redaction on by default (test passes).
- No-network verification passes for local axes; moat intact.
- PATH-B decision (5) resolved + implemented per consultant call; if fixed, tested both formats + pool re-run confirms no change to pre-v2.1.38 counts + CURRENT_STATE reflects the un-freeze.
- README states the tool's domains of validity (honesty carried to docs); corpus-contribution path described as roadmap (not built).
- Reports 01-11 untouched. Git clean.

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md + reports 08/10/11 + spec.md. Internalize: tiered judge, moat-by-construction, output-honesty discipline, behavior-preservation.
2. Write the behavior-preservation test FIRST (capture current script outputs on a sample of pool sessions as golden). This is the safety net for the refactor.
3. Refactor scripts into the `tes/` SDK package, axis by axis; behavior-preservation test stays green throughout. HOLD after refactor for consultant read (confirm scores unchanged).
4. Build the tiered judge module (detect-availability + run-or-UNAVAILABLE). Test both branches.
5. Resolve + implement the PATH-B decision (5) — escalate first.
6. Build the CLI surface + output formatter (three labeled axes, caveats inline, --json).
7. pyproject.toml / installability / README with domains-of-validity + corpus-contribution roadmap note.
8. Full test suite green (behavior-preservation, tiering, caveats, redaction, installable, no-network). HOLD for consultant read before calling P1 done.
