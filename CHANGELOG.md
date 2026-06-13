# Changelog

All notable changes to **tracegauge** are documented here. This project follows
[Semantic Versioning](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/)
conventions.

A note on version numbers: the published PyPI artifacts are `0.1.0`, `0.5.0`, and
`0.6.0`. Versions `0.2.0` and `0.4.0` were built and tagged internally but never
published to PyPI. `0.6.0` is the **current release** — the complete `0.5.0`
toolchain with a frictionless front door.

## [0.6.0] — 2026-06-13 — Frictionless UX

Ergonomics only. The scoring engine, numbers, attribution, cost math, detectors,
and honesty surfacing are **byte-for-byte unchanged** from `0.5.0` — this release
only changes how the tool is invoked and how it guides setup. The detectors stay
frozen; reports 01–11 stay immutable.

### Added
- **Bare `tes` launches the dashboard.** Running `tes` with no subcommand now
  starts the localhost dashboard (`tes serve`), the obvious default. `tes --help`
  still shows help; `tes <unknown>` still errors helpfully.
- **`tes score` needs no path.** With no argument it scores your most recent
  session (newest `.jsonl` under `~/.claude/projects` by mtime) and prints which
  one it chose. `tes score --pick` shows a numbered list of recent sessions to
  choose from. An explicit `PATH` still works. Resolution order: explicit PATH >
  `--pick` > newest.
- **Clean `--judge` on-switch.** `tes score --judge` now works — it previously
  failed with `ambiguous option: --judge could match --judge-model,
  --judge-endpoint`. `--judge` means "use the trajectory judge."
- **Judge auto-detect + guide.** `--judge` detects a running local Ollama judge
  and uses it; if none is found but `ANTHROPIC_API_KEY` is set, it *offers* the
  API judge behind the existing per-session consent screen; if neither is
  available it prints the single simplest setup step instead of failing cryptically.
- **First-run orientation line** on `tes serve` / bare `tes` (session count found,
  dashboard URL) and on `tes score` (which session was auto-selected).

### Unchanged (non-negotiable boundaries)
- **API-judge consent stays the egress gate.** Auto-detecting an API key does
  **not** authorize sending data — every byte of egress still requires an explicit
  `y` on the per-session consent screen. `detect_env_api_key()` performs zero
  network activity.
- Judge stays **OFF by default in the background watcher** (GPU/cost footgun guard).
- No scoring / attribution / judge / cost / detector logic changed — same numbers,
  same honesty. `tes/_waste_detectors.py` is byte-frozen; reports 01–11 immutable.

### Tests
- `tests/test_cli_ergonomics.py` — bare-`tes`-serves, no-path-scores-newest,
  `--pick` selection, explicit-path-wins, `--judge`-not-ambiguous, flag conflicts.
- `tests/test_judge_autodetect.py` — Ollama-preferred, API-offered-on-key,
  consent-declined-means-no-egress, consent-accepted-passes-config,
  guide-when-nothing, and an unconditional no-silent-egress guard.

## [0.5.0] — 2026-06-13 — Consolidated current release

The complete, feature-complete release. Bundles the entire validated toolchain
(B1–B5 research arc) and every shipped phase (P1–P9) into one published artifact.

### Added
- **Diagnostic dashboard redesign (P9).** Polished CSS visual system across all
  dashboard views (session list, session detail, baseline status, trends). All
  honesty elements survive the restyle, guarded by a dedicated regression test
  (`test_ui_honesty_survives.py`, 20 assertions): domain-of-validity caveats on
  every axis, UNAVAILABLE rendered as a calm/neutral state (never an error),
  relative "your own lean baseline" framing, baseline-source labels, waste proof
  turns, API-judge egress warning, and no composite/blended score.
- **README Features section** at the top so a new user sees the tool is
  feature-complete regardless of the version number.

### Feature set in this release (cumulative)
- **Self-baseline token scoring (P4)** — scores against your own lean, waste-free
  sessions per task type; bundled reference corpus as fallback.
- **Dollar cost attribution (P8)** — six reconciling buckets (B1–B6); token% and
  cost% side by side; cache-read divergence made visible.
- **Deterministic waste detection (B4/P6)** — frozen observable-invariant
  detectors (repeated-failed-retry, redundant-read) with proof turns and per-event
  wasted cost.
- **Trajectory judge (P1/P8)** — local Ollama judge ($0, GPU) or opt-in API judge
  (explicit per-session consent); UNAVAILABLE is a complete, expected state.
- **Localhost dashboard + watcher (P2)** — `tes serve`, `127.0.0.1`-only,
  auto-scores finished sessions, SQLite store with WAL.
- **Content-free local contribution export (P7)** — `tracegauge export-contribution`;
  redacted local file you inspect and control; nothing transmitted by tracegauge.

### Unchanged / guaranteed
- Detectors frozen (`tes/_waste_detectors.py` byte-verbatim with the validated
  research copy). Research reports 01–11 immutable.
- Local by default — scoring and the dashboard make zero external network calls.
  The only egress is the opt-in API judge (your key, your consent).

## [0.4.0] — built internally, not published

Token attribution (six-bucket) + opt-in API judge (P8). Folded into 0.5.0.

## [0.2.0] — built internally, not published

Content-free contribution export + watcher/dashboard hardening (P2/P7). Folded
into 0.5.0.

## [0.1.0] — 2026-06-08 — First PyPI release

- Installable CLI + SDK: `pip install tracegauge`, `tes score <path>`.
- Three-axis scoring (token economy + trajectory quality + deterministic waste),
  tiered judge, secret redaction on by default, AGPL-3.0.
- Published to PyPI: https://pypi.org/project/tracegauge/0.1.0/

[0.5.0]: https://pypi.org/project/tracegauge/0.5.0/
[0.1.0]: https://pypi.org/project/tracegauge/0.1.0/
