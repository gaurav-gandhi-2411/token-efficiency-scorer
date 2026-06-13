# Changelog

All notable changes to **tracegauge** are documented here. This project follows
[Semantic Versioning](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/)
conventions.

A note on version numbers: the published PyPI artifacts are `0.1.0` and `0.5.0`.
Versions `0.2.0` and `0.4.0` were built and tagged internally but never published
to PyPI. `0.5.0` is the **single consolidated current release** that contains every
feature built across all phases — install it and you have the complete tool.

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
