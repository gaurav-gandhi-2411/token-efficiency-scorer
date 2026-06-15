# Changelog

All notable changes to **tracegauge** are documented here. This project follows
[Semantic Versioning](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/)
conventions.

A note on version numbers: the published PyPI artifacts are `0.1.0`, `0.5.0`, and
`0.6.0`. Versions `0.2.0` and `0.4.0` were built and tagged internally but never
published to PyPI. `0.6.0` is the **current release** — the complete `0.5.0`
toolchain with a frictionless front door.

## [0.7.1] — 2026-06-15 — Hotfix: missing ML dependencies

`tes patterns` and `tes ask` crashed with `ModuleNotFoundError: No module named 'numpy'`
on every clean install of 0.7.0. The Session Intelligence code (tes/intelligence/) imports
`numpy` and `scikit-learn` but those packages were never declared in `pyproject.toml`.
They were present in the development environment, so the pre-publish gate missed the gap.

### Fixed

- **Missing ML dependencies declared:** `numpy>=1.24,<3` and `scikit-learn>=1.3,<2` added
  to `[project.dependencies]` in `pyproject.toml`. A clean `pip install tracegauge` now
  installs everything `tes patterns` and `tes ask` need. No pandas is imported — only
  numpy and scikit-learn were missing.
- **Judge HTTP 500 graceful handling:** Ollama returning HTTP 500 (model OOM or inference
  failure) now prints `Judge unavailable: Ollama returned HTTP 500 (model error or OOM) —
  trajectory UNAVAILABLE` instead of a raw exception message. `httpx.HTTPStatusError` is
  now caught explicitly before the broader `httpx.HTTPError` catch.
- **Dashboard judge model name corrected:** `session_detail.html` suggested `qwen3:8b` for
  the trajectory judge setup — wrong model. Fixed to `qwen3:30b-a3b` (~18 GB VRAM), matching
  `judge.py`'s `JudgeConfig` default and `report.py`'s setup hint.

### Tests

- `tests/test_dep_closure.py` (1 test): walks `tes/intelligence/*.py` via AST, extracts
  all absolute top-level imports, and asserts every external import is either stdlib or
  in `DECLARED_IMPORT_NAMES`. Fails immediately if a new undeclared import slips in —
  guards against the 0.7.0 clean-install regression.

### Gate lesson

The 0.7.0 clean-room gate ran in conda base (numpy pre-installed). The gate must use a
truly isolated environment with only declared deps. Starting with 0.7.1, the gate uses
`conda create --no-default-packages` so only `pip install tracegauge` deps are present.

---

## [0.7.0] — 2026-06-15 — Session Intelligence

Two composing features that do work the deterministic engine cannot: unsupervised
ML finding recurring patterns across the whole corpus, and a conversational layer
to ask about sessions in plain language. The engine, attribution math, detectors,
and reports are **byte-for-byte unchanged** — this adds analytics on top of what's
already measured.

### Added

#### ML Foundation (`tes/intelligence/`)

- **Validated KMeans clustering** over the session corpus (k=2..8, n_init=30).
  k selected by silhouette score; stability checked across 10 seeds (CV < 0.15
  required). Silhouette thresholds: ≥ 0.20 meaningful, 0.10–0.20 weak, < 0.10
  no structure. Live corpus: k=3, silhouette=0.466, stability CV=0.000.
- **Feature engineering** — 8 features: 4 attribution percentages
  (context_resend_pct, context_growth_pct, output_pct, waste_pct), 3 log-scale
  size features (log_real_tokens, log_turn_count, log_cost), 1 binary (has_waste).
  `task_type` deliberately **excluded** from the feature vector: including it
  produced k=7 where 5/7 clusters re-discovered known folder labels (silhouette
  0.37); excluding it gives k=3, silhouette 0.466 with genuinely cross-type
  behavioral archetypes — structure not already present in the folder names.
- **Named archetypes** from centroid dominant features, with evaluative terms
  forbidden. Live archetypes: `medium high context re-send sessions` (64.7%),
  `small active context-building sessions` (24.3%),
  `medium with detected waste sessions` (11.1%).
- **Per-cluster Tukey outer fence anomaly detection** (Q3 + 1.5×IQR on centroid
  distance). Top-3 deviating features reported per anomaly. Live: 10 anomalies /
  235 sessions (4.3%).
- **Persistent ML cache** at `~/.tes/intelligence_cache.json` — stamped with
  `tracegauge_version + session_count + computed_at`. Invalidated on version
  bump or when session count changes by > 5. Minimum corpus floor: 30 content
  sessions; below it, an honest "not enough sessions for stable patterns yet"
  message is returned instead of clustering noise.
- **`tes patterns`** — CLI command showing archetypes, cluster validity metrics,
  and anomaly summary for the whole corpus.

#### Conversational Layer

- **`tes ask "<question>"`** — plain-language Q&A about sessions. Local Ollama
  first (auto-picks best available model); falls back to offering the Anthropic
  API behind the existing consent gate. Default local model: `qwen3:8b`.
- **Metrics-only egress** — the chat context contains ONLY computed numbers
  (corpus stats, cluster centroids, anomaly counts). Raw session content, code,
  tool inputs/outputs, and file paths are never sent to any model.
- **Constrained system prompt** (7 rules): answer only from context; "I don't
  have that measured" for absent facts; "I don't predict future behavior" for
  forecasting questions; cite the metric source for every number; do not
  dramatize archetypes into personalities.
- **Unambiguous context labels** — all binary flags use YES/NO (never 0/1);
  all attribution percentages name the denominator ("of billed tokens"); all
  session fractions name the reference ("of corpus"). The Q2 regression: `waste=1`
  misread as "1.0% waste rate" is guarded by `TestContextFormatUnambiguous`.
- **Honest small-corpus path** — `tes ask` and `tes patterns` both gate on the
  minimum corpus floor; a new user sees "not enough sessions yet" rather than
  archetypes that don't statistically exist.

### Tests (470 total, up from 377)

- `tests/test_cluster_validity.py` (31) — feature extraction, clustering validity,
  no-stable-clusters path, evaluative terms not in archetype names.
- `tests/test_anomaly_threshold.py` (12) — Tukey threshold is per-cluster, every
  anomaly exceeds threshold, top-3 deviating features valid and sorted.
- `tests/test_chat_grounding.py` (31) — system prompt constraints, context
  structure, no-code egress, API consent gate; **+ 5 context-format regression
  tests** (`TestContextFormatUnambiguous`).
- `tests/test_chat_no_code_egress.py` (19) — user message builder, API payload
  inspection, egress notice accuracy.
- `tests/test_small_corpus_honest_path.py` (12) — cache layer, format layer, and
  chat layer all return honest "not enough sessions" messages below the clustering
  floor; above the floor, valid clustering is produced.

### Methodology

`research/12_session_intelligence.md` — feature choices, task_type-exclusion
reasoning, cluster-validity results, anomaly-threshold rationale. The portfolio
artifact documenting what the ML actually found and why the method is sound.

### Unchanged (non-negotiable)

- `tes/_waste_detectors.py` is **byte-frozen** (confirmed: `git diff` empty).
- Reports 01–11 immutable. Attribution engine, cost math, judge path, consent
  gate — all unchanged.
- API consent gate is **unconditional** on all paths, including the new chat API
  path. `consent_given=False` → `None` + zero network calls.

---

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
