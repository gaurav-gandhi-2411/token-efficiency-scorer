# Project Spec: tracegauge — Frictionless UX (Iteration 0.6.0)

## Goal

Eliminate the setup friction a real user hit: having to know magic flags, hunt for session-file paths, and manually configure the judge. The principle: **the user does almost nothing; the tool does the work.** The tool already knows where sessions live (the watcher scans ~/.claude/projects) — it should never make the user type a path or decode a cryptic argparse error.

This is a UX/CLI-ergonomics phase. It changes how the user INVOKES the tool and how defaults behave. It does NOT change any scoring, attribution, judge logic, cost math, detectors, or honesty surfacing — those are frozen and correct. Same engine, frictionless front door.

## The friction being removed (real user report)

A user on a fresh 0.5.0 install hit ALL of these in one session:
- `tes score --judge` → "ambiguous option: --judge could match --judge-model, --judge-endpoint" (there's no plain --judge flag; the obvious command errors).
- `tes score` with no path → had to go dig through ~/.claude/projects to find a session file to pass.
- To use the judge → would have to install Ollama, know the model name, pull 18GB, OR find an API key and pass it as a flag — with no guidance.

The fix principle, in priority order:
1. The OBVIOUS command should WORK (no cryptic errors on natural input).
2. NO PATHS for the common case (the tool knows where sessions are).
3. AUTO-DETECT what's available (Ollama running? API key in env?) and USE it; where genuine setup is unavoidable (the judge needs a model the tool can't install), DETECT-AND-GUIDE with the single simplest next step — never fail with a manual-reading error.

## What "minimal user work" means concretely (the deliverables)

1. **Bare `tes` does the obvious thing.** `tes` with no subcommand → launches `tes serve` (the dashboard — the most useful default). Today it likely shows help; it should DO the useful thing. (`tes --help` still shows help; `tes <unknown>` still errors helpfully.)

2. **`tes score` needs NO path.** With no path argument, it auto-scores the MOST RECENT session (locked decision: option a). The tool finds the newest .jsonl under ~/.claude/projects by mtime, scores it, prints the report. A `--pick` flag (option b) shows a short numbered list of recent sessions (e.g. last 10, with task type / time / a one-line hint) to choose from. An explicit PATH still works (power users / scripting). Resolution order: explicit path > --pick (interactive list) > newest session (default).

3. **The `--judge` flag WORKS.** Add a plain `--judge` as an explicit on-switch (turns the local judge on / makes intent explicit), resolving the current ambiguity with --judge-model/--judge-endpoint. `--judge` = "use the local judge" (the obvious meaning). The ambiguity error must be gone — the natural command succeeds.

4. **Judge auto-detect + guide (the irreducible-setup part, handled gracefully):**
   - On a judge-requesting run (or even by default when scoring), DETECT: is Ollama running locally with a usable model? If yes → use it, no flags needed.
   - DETECT: is an API key present in the environment (e.g. ANTHROPIC_API_KEY)? If yes AND the user opts into the API judge → use it (with the existing per-session consent — consent is NOT removed; it's a moat/honesty non-negotiable).
   - If NEITHER is available and the user wanted a judge → DO NOT error cryptically. Print the single simplest path: "No local judge detected. Fastest option: [the one best step]. Token + waste axes ran fully without it." Detect-and-guide, never fail-with-manual.
   - The judge stays OFF by default in the background watcher (the GPU/cost footgun guard is unchanged). This is about making the ON path frictionless when the user wants it, not auto-enabling it.

5. **First-run friendliness.** On the very first `tes serve` / `tes score`, if the store is empty or it's clearly a first run, a one-line friendly orientation ("scanning ~/.claude/projects … found N sessions … dashboard at http://127.0.0.1:4747"). Not a wizard — just clarity so the user isn't staring at a blank thing wondering if it worked.

## The honesty / non-negotiable boundary (autonomy does NOT override)

Making it frictionless must NOT erode the disciplines:
- **API-judge consent stays.** Auto-detecting an API key does NOT mean auto-sending data. The per-session consent ("sends session data including snippets that may contain your code") is REQUIRED before any egress. Frictionless ≠ silent data egress. This is the one place "minimal user work" must NOT cut a corner.
- **Judge OFF by default in background watcher** — unchanged (GPU/cost footgun).
- **No scoring/attribution/judge/cost/detector changes** — same numbers, same honesty surfacing. Detectors frozen (git diff empty).
- **Moat intact** — defaults stay local; the only egress is the still-consented API judge.
- Reports 01-11 immutable. No human labels.

## Current state
tracegauge 0.5.0 LIVE on PyPI, feature-complete (B1-B5 + P1-P9). The engine is correct and the dashboard is polished. The gap is purely the FRONT DOOR — invocation ergonomics and judge setup friction. CURRENT_STATE reflects 0.5.0 LIVE. Detectors frozen, reports immutable.

## Scope
### In scope
1. CLI ergonomics: bare `tes` → serve; `tes score` no-path → newest session; `--pick` interactive list; explicit path still works.
2. Fix the `--judge` ambiguity: add a clean `--judge` on-switch.
3. Judge auto-detect (Ollama running? API key in env?) + detect-and-guide messaging (never cryptic-fail); consent preserved for API.
4. First-run orientation line.
5. Help text + README updated so the minimal-effort usage is the documented path ("just run `tes`").
6. Tests: no-path-scores-newest, --pick lists, --judge no longer ambiguous + turns judge on, auto-detect picks Ollama/API correctly, API consent still required (no silent egress), bare-tes-serves.

### Out of scope
- Any scoring/attribution/judge/cost/detector logic change (ergonomics only).
- Auto-installing Ollama or any model (can't, won't — detect-and-guide only).
- Removing or weakening the API-judge consent (non-negotiable).
- Auto-enabling the judge in the background watcher (footgun guard stays).
- New data egress paths. Reports 01-11. Trends (still parked).

## Tech stack
- Python, reuse tes/. CLI is argparse in tes/cli.py — restructure invocation/defaults, add subcommand-less default + --pick + --judge. Session discovery reuses the watcher's existing ~/.claude/projects scan (newest-by-mtime).
- Judge detect: a quick Ollama health probe (already exists in tes/judge.py) + an env-var check for the API key.
- pytest for the new ergonomics + the consent-preserved guard.

## Architecture (changed)
```
tes/cli.py        # default-subcommand (bare tes -> serve), tes score no-path -> newest,
                  # --pick, clean --judge, judge auto-detect + guide messaging, first-run line
tes/judge.py      # reuse the Ollama probe; add env-API-key detect (NO auto-send — detect only)
tes/watcher.py    # reuse session-discovery (newest-by-mtime) — or factor a shared helper
tests/
├── test_cli_ergonomics.py      # bare tes, no-path-newest, --pick, --judge-not-ambiguous
├── test_judge_autodetect.py    # detects Ollama/API; guides when neither; consent still required
└── (existing tests unchanged + green)
```

## Verification commands
```yaml
- name: cli-ergonomics
  cmd: python -m pytest tests/test_cli_ergonomics.py -v
  required: true
- name: judge-autodetect-consent-preserved
  cmd: python -m pytest tests/test_judge_autodetect.py -v   # auto-detect works AND API consent still gates egress
  required: true
- name: detectors-frozen
  cmd: git diff --exit-code tes/_waste_detectors.py && echo frozen
  required: true
- name: no-engine-change
  cmd: python -m pytest -q   # full suite; scoring/attribution/judge/cost tests unchanged + green
  required: true
```

## Escalation rules (autonomous mode — escalate ONLY these)
- PUBLISHING 0.6.0 to PyPI (irreversible; user's token) — the expected publish escalation.
- If any ergonomics change would WEAKEN the API-judge consent or create silent egress — STOP, escalate (it's the one corner that must not be cut).
- Touching frozen detectors / immutable reports / scoring math — out of scope, escalate.
- Otherwise: DECIDE AND ACT. Implementation, messaging copy, --pick UX, detect logic, first-run wording — all your call; report, don't ask.

## Hard rules
- ERGONOMICS ONLY: same engine, same numbers, same honesty. Detectors frozen.
- API-JUDGE CONSENT PRESERVED: frictionless must not become silent egress. Auto-detect a key ≠ auto-send.
- Judge OFF by default in background; the obvious manual ON path just works.
- The OBVIOUS command WORKS (no cryptic argparse errors on natural input).
- Moat intact; reports immutable; publish-immediately (0.6.0 ships when done).

## Success criteria (verify ALL)
- `tes` (bare) launches the dashboard.
- `tes score` (no path) scores the newest session; `tes score --pick` lists recent to choose; explicit path still works.
- `tes score --judge` works (no ambiguity error), turns the local judge on.
- Judge auto-detects Ollama/API-key and uses what's available; when neither, guides with the single simplest step (no cryptic fail). API egress still requires per-session consent (tested — no silent send).
- First-run shows a friendly orientation line.
- README/help document the minimal path ("just run `tes`").
- Same numbers (engine untouched); detectors frozen; full suite green; reports 01-11 untouched.
- 0.6.0 built, clean-roomed (works from installed wheel), and PUBLISHED (publish-immediately) — verified by fresh pip install.

## Build order (orchestrator decides details autonomously)
1. Read CURRENT_STATE.md + tes/cli.py + tes/judge.py + tes/watcher.py. Confirm context + the ergonomics-only / consent-preserved boundary in 4-6 lines.
2. Implement the CLI ergonomics (bare tes, no-path-newest, --pick, clean --judge) + judge auto-detect + guide + first-run line. Decide messaging/UX autonomously.
3. Tests (ergonomics + consent-preserved-no-silent-egress). Full suite green, detectors frozen.
4. README/help update to the minimal path.
5. Build + twine check + CLEAN-ROOM (works from installed wheel: bare tes serves, tes score scores newest, --judge works, consent preserved).
6. Bump 0.5.0 -> 0.6.0, CHANGELOG. ESCALATE with the prepared publish command + clean-room result for the user to upload (publish-immediately). After upload, confirm fresh pip install.
