# CURRENT_STATE.md — token-efficiency-scorer

Snapshot as of 2026-06-15 (0.8.0 READY TO PUBLISH — Dashboard Intelligence + Sortable List).
Read this BEFORE planning. This supersedes the 0.7.1 snapshot.

---

## Iteration status: 0.8.0 — Dashboard Intelligence + Sortable Session List (PENDING PUBLISH)

**Built:** wheel at `dist/tracegauge-0.8.0-py3-none-any.whl`. Awaiting PyPI publish.
**Clean-room gate:** PASSED (2026-06-15). Installed from wheel in `conda create
--no-default-packages` env (`tes-cleanroom-080`). All 8 checks passed:
- `tes.__file__` from site-packages (NOT repo)
- `tes.__version__ == "0.8.0"`
- `/patterns` and `/ask` in `url_map` from installed wheel
- `GET /patterns` → 200, floor message + Ask panel rendered from bundled templates
- `POST /ask` empty → 400; `GET /ask` → 405 (not 404)
- `tes --version` → `tes 0.8.0` from installed entry point
- `tes.intelligence` imports OK (numpy/sklearn available as declared deps)
- `CHAT_SYSTEM_PROMPT` contains "I don't predict" guard

**Browser verified (from `python -m tes serve`, repo):**
(a) /patterns renders archetypes + validity + "Descriptive only" caveat
(b) Ask panel: grounded answer to "which task type costs the most?"
(c) Ask panel: "I don't predict" fires in-browser for future-cost question
(d) Session list sort headers re-sort by cost/date/waste/tokens/verdict

**What 0.8.0 delivers (surfacing + sorting only — engine/ML/chat/detectors unchanged):**
- Part B: Sortable session list — clickable headers, `?sort=&dir=` server-side, whitelist-safe
  SQL (no injection surface), Cost + Tokens columns added, honesty elements all survive sorting.
- Part A1: `/patterns` page — archetypes with bar charts, validity (silhouette/N), DOV caveat,
  "descriptive not predictive" framing, anomaly count/pct, small-corpus floor by construction.
- Part A2: Web Ask panel — POST `/ask` → `ask_local()`/`ask_api()` (identical CLI functions,
  same `CHAT_SYSTEM_PROMPT`, same `build_chat_context()` metrics-only). API consent checkbox
  shown in UI; route enforces `api_consent=True` gate. Question capped at 500 chars.
- A3: Judge status chips (Ollama running/not detected/API key set) — read-only, no one-click
  API-judge enable (status + instructions scope, not full in-browser enablement).
- Route-registration test (`test_route_registration.py`): url_map assertion via same path as
  `tes serve` — closes "tests-pass-but-real-server-fails" class (as `test_dep_closure.py`
  closed the numpy class in 0.7.1).

**Tests:** 543 green (+70 new: 23 sort, 17 patterns, 21 ask-guards, 9 route-registration).
Detectors frozen. Import closure green. No new Python or JS deps (vanilla inline script).

**Tag discipline:** tag `v0.8.0` AFTER PyPI publish confirms, not before.

---

## Iteration status: 0.7.1 DONE — Session Intelligence hotfix; LIVE on PyPI

**Published:** https://pypi.org/project/tracegauge/0.7.1/ — `pip install tracegauge` → 0.7.1.
**Git tag:** `v0.7.1` at commit `07b2b86` (pushed to origin AFTER PyPI confirm — tag discipline
correct this time).

**What 0.7.1 fixed (hotfix over 0.7.0):**
- `numpy>=1.24,<3` and `scikit-learn>=1.3,<2` added to `pyproject.toml` core deps. 0.7.0 shipped
  these imported but undeclared — clean installs got `ModuleNotFoundError: No module named 'numpy'`
  on both `tes patterns` and `tes ask`. The gate missed it because conda base had numpy pre-installed.
- `test_dep_closure.py` (1 test): AST-walks ALL of `tes/**/*.py` (27 files), asserts every external
  import is stdlib or in DECLARED_IMPORT_NAMES. Fails immediately if any undeclared import lands
  anywhere in the package — guards project-wide, not just intelligence/.
- `judge.py`: `httpx.HTTPStatusError` caught explicitly before `httpx.HTTPError`; Ollama HTTP 500
  now prints `"Ollama returned HTTP 500 (model error or OOM) — trajectory UNAVAILABLE"` instead of
  a raw exception message.
- `session_detail.html`: judge model name `qwen3:8b` → `qwen3:30b-a3b` (~18 GB VRAM), matching
  `JudgeConfig.model` default and `report.py` setup hint.
- **Gate lesson applied:** clean-room now uses `conda create --no-default-packages` so only
  `pip install tracegauge` deps are present. Numpy confirmed absent before install, then verified
  auto-installed as a declared dep.

**Post-publish verification (real PyPI, 2026-06-15 — --no-default-packages clean env):**
- `conda create -n tes-verify-071 python=3.12 --no-default-packages -y`
- `numpy` and `sklearn` confirmed absent before install.
- `pip install --no-cache-dir tracegauge==0.7.1` → resolved numpy-2.4.6 (in [1.24,3)) and
  scikit-learn-1.9.0 (in [1.3,2)) from declared deps in the wheel metadata. No resolver conflicts.
  Install completed cleanly (~60MB total including scipy transitive dep).
- `tes --version` → `tes 0.7.1` ✓
- `tes patterns` → **exit 0, k=3, silhouette=0.453, `tracegauge 0.7.1` footer stamp** ✓
  (the headline crash is fixed for real users on a clean install)
- `tes ask "What will my next session cost?"` → "I don't predict future behavior..." ✓

**Tests:** 473 green (472 + 1 dep-closure test covering all of tes/).

---

## Iteration status: 0.7.0 — Session Intelligence (SUPERSEDED by 0.7.1 hotfix)

**Was broken:** `tes patterns` / `tes ask` crashed with `ModuleNotFoundError: No module named 'numpy'`
on clean install. Superseded; do not install 0.7.0.

**Published:** https://pypi.org/project/tracegauge/0.7.0/ — superseded.
**Git tag:** `v0.7.0` at commit `da54116` (pushed before publish — tag discipline error, noted).

**Two composing features on top of the unchanged 0.5.0/0.6.0 engine:**
- **`tes patterns`** — unsupervised KMeans clustering (k=2..8, n_init=30, silhouette k-select,
  10-seed stability). Live: k=3, silhouette=0.453, CV=0.000. Three named archetypes from centroid
  dominant features. Tukey outer fence anomaly detection per cluster (Q3+1.5×IQR on centroid
  distance). Persistent JSON cache (`~/.tes/intelligence_cache.json`) with version+session_count
  stamps; invalidated on version bump or >5 session delta.
- **`tes ask "<question>"`** — metrics-only constrained explainer. Local Ollama first
  (default qwen3:8b); API path behind existing consent gate. 7-rule system prompt: answer only
  from context; "I don't predict" for forecasting questions; "not measured" for absent facts.
  Metrics-only egress — no session content, code, file paths ever sent.

**Non-negotiables held:**
- Engine (`tes/_waste_detectors.py`) byte-frozen — `git diff` empty throughout.
- Reports 01–11 immutable.
- API consent gate unconditional on ALL paths (chat API path included).
- format_intelligence_summary uses unambiguous labels: has_waste: YES/NO (not 0/1), "% of
  billed tokens" on attribution fractions, "% of corpus" on session fractions. Guarded by
  TestContextFormatUnambiguous (5 regression tests). Fix for Q2 regression: `waste=1`
  misread as "1.0% waste rate" by qwen3:8b.

**Tests:** 472 green (377 pre-0.7.0 + 31 ML + 62 chat/cache/honest-path + 2 stamp regression).
- test_cluster_validity.py: 31 — feature extraction, clustering validity, evaluative-terms guard.
- test_anomaly_threshold.py: 12 — Tukey per-cluster, top-3 sorted, no false-positives.
- test_chat_grounding.py: 31 (incl. 5 TestContextFormatUnambiguous regression tests).
- test_chat_no_code_egress.py: 19 — payload inspection, metrics-only egress, egress notice.
- test_small_corpus_honest_path.py: 14 — cache/format/chat all return honest "not enough
  sessions" below floor (30 content sessions); above-floor clustering is valid; 2 stamp
  regression tests guard KeyError 'session_count' fix.

**Key bugs caught and fixed during pre-publish gate:**
1. `waste=1` format ambiguity (Q2 regression): binary flag formatted as numeric, model
   misread as "1.0% waste rate". Fixed with has_waste: YES/NO + full format audit.
2. `KeyError: 'session_count'` in `_run_patterns` footer on first run: `save_cache()` stamps
   the disk file but not the in-memory return dict; `return load_cache() or cache_dict` fixed
   both code paths. Caught by B.3 clean-room functional check — MISSED by the 470-test suite.

**Methodology doc:** `research/12_session_intelligence.md` — feature choices, task_type exclusion
reasoning, k-selection sweep, validity results, anomaly rationale, honest framing of archetypes.
The portfolio artifact.

**Post-publish verification (real PyPI, 2026-06-15):**
- `pip install --no-cache-dir --force-reinstall --no-deps tracegauge==0.7.0` from production
  PyPI (no `direct_url.json` confirms index install, not local wheel).
- `tes.__file__` → `C:\Users\gaura\anaconda3\Lib\site-packages\tes\__init__.py` (not repo).
- `tes --version` → `tes 0.7.0` ✓
- `tes patterns` → clean exit (KeyError fix confirmed in published artifact). Cache was stale
  (252 content sessions, up from 235); fresh compute ran end-to-end. k=3, silhouette=0.453,
  CV=0.000, 12 anomalies/252 sessions (4.8%). Footer line with tracegauge 0.7.0 stamp printed.
- `tes ask "What will my next session cost?"` → "I don't predict future behavior — I only
  explain what's already measured..." (honesty guard fires from published wheel) ✓

---

## Iteration status: 0.6.0 DONE — Frictionless UX; LIVE on PyPI

**Published:** https://pypi.org/project/tracegauge/0.6.0/ — superseded by 0.7.0.
**Git tag:** `v0.6.0` at commit `85738f9` (pushed to origin AFTER the PyPI upload + a fresh
real-PyPI install confirmed — tag never points at an unpublished version).

**Ergonomics ONLY — the engine, numbers, attribution, cost math, detectors, and honesty
surfacing are byte-for-byte unchanged from 0.5.0.** This release fixes the front door a real
user tripped on: the natural commands now just work, and the tool finds sessions/judge for you.

**What 0.6.0 delivered (CLI ergonomics in `tes/cli.py` + a detect-only helper in `tes/judge.py`):**
- **Bare `tes` launches the dashboard** (= `tes serve`). `tes --help` still shows help; `tes
  <unknown>` still errors via argparse.
- **`tes score` needs no path** — scores the most recent session (newest `.jsonl` under
  `~/.claude/projects` by mtime) and prints which one. `--pick` shows a numbered recent list;
  explicit `PATH` still works. Resolution order: explicit PATH > `--pick` > newest.
- **Clean `--judge` on-switch** — `tes score --judge` works; the old `ambiguous option:
  --judge could match --judge-model, --judge-endpoint` error is gone.
- **Judge auto-detect + guide** — `--judge` uses a running local Ollama judge; else if
  `ANTHROPIC_API_KEY` is set it *offers* the API judge behind the existing consent screen;
  else prints the single simplest setup step (never cryptic-fails). Token + waste always run.
- **First-run orientation line** on serve/bare-`tes` (session count + dashboard URL) and on
  `tes score` (which session was auto-selected).

**Non-negotiables held (verified by tests):**
- **API-judge consent stays the egress gate.** Auto-detecting a key NEVER sends data;
  `detect_env_api_key()` is network-free; egress still requires explicit `y`. Guarded by
  `test_judge_autodetect.py` (consent-declined → no config reaches the scorer; unconditional
  `score_trajectory_api(consent_given=False)` no-network gate).
- Judge OFF by default in the background watcher (GPU/cost footgun). `tes/_waste_detectors.py`
  byte-frozen (`git diff` empty). Reports 01–11 immutable. Moat intact (local by default; only
  egress = consented API judge).

**Tests:** 377 green (364 from 0.5.0 + 13 new across `test_cli_ergonomics.py` [bare-tes-serves,
no-path-newest, --pick, explicit-path-wins, --judge-not-ambiguous, flag conflicts] and
`test_judge_autodetect.py` [Ollama-preferred, API-offered-on-key, consent-declined-no-egress,
consent-accepted-passes-config, guide-when-nothing, unconditional no-silent-egress]).

**Post-publish verification (real PyPI, 2026-06-13):**
- Fresh throwaway venv, `pip install --no-cache-dir tracegauge==0.6.0` from production PyPI
  (one CDN-propagation retry). `tes`/`tracegauge --version` → `tes 0.6.0`; `tes.__file__` in
  site-packages.
- Frictionless UX confirmed from the PUBLISHED artifact (the whole point of this version):
  bare `tes` → dashboard on `127.0.0.1:4747` scanning ~/.claude/projects (1,014 files found);
  `tes score` (no path) → resolved to newest session with orientation line + full three-axis
  report; `tes score --judge` parses without the ambiguity error.

---

## Iteration status: P9 DONE — Dashboard UI redesign; 0.5.0 is the prior consolidated release

**Published:** https://pypi.org/project/tracegauge/0.5.0/ — superseded by 0.6.0 then 0.7.0.
**Git tag:** `v0.5.0` at commit `c079e5d` (pushed to origin AFTER the PyPI upload confirmed —
tag never points at an unpublished version).

**0.5.0 is the SINGLE consolidated complete version.** It contains ALL features built across
the whole arc: B1-B5 validated detectors + P1-P9 (self-baseline token scoring, dollar cost
attribution, deterministic waste detection, trajectory judge [local or opt-in API], the
diagnostic dashboard). 0.2.0 and 0.4.0 were built-but-never-published; that version drift is
now closed. PUBLISH-IMMEDIATELY is the standing rule: a phase is not done until LIVE on PyPI
and a fresh `pip install` confirms it.

**What P9 delivered:**
- Polished CSS visual system (`base.html`) + redesigned dashboard views: `session_list`,
  `session_detail`, `baseline_status`, `trends`.
- `tests/test_ui_honesty_survives.py` — 20-assertion regression guard. Every honesty element
  must survive any future restyle: the 3 domain-of-validity caveats, UNAVAILABLE rendered as
  a calm/neutral badge (never an error class), relative "your own lean baseline" framing,
  baseline-source labels, waste proof turns, API-judge egress warning, and no composite/blended score.

**Release mechanics (0.5.0):**
- `pyproject.toml` version → 0.5.0; `tests/test_packaging.py` version assertion → 0.5.0.
- README: **Features** section added at the top (tool is feature-complete regardless of the
  version number). New `CHANGELOG.md` marks 0.5.0 as the consolidated current release and
  honestly records 0.2.0/0.4.0 as built-but-never-published (folded into 0.5.0).
- Commits: `2906b6f` (P9 templates + honesty test), `c079e5d` (0.5.0 consolidation).

**Post-publish verification (real PyPI, 2026-06-13):**
- Fresh throwaway venv, `pip install --no-cache-dir tracegauge==0.5.0` from production PyPI.
- `tracegauge --version` / `tes --version` → `tes 0.5.0`; `tes.__file__` in venv site-packages;
  bundled `cc_baselines.json` present.
- `tes serve` from the PyPI install: dashboard up on 127.0.0.1 (localhost-only bind), watcher
  auto-scored 100 sessions, secret redaction fired in-flight, all dashboard routes 200.
- Session-detail render from the PyPI-installed templates: all 20 honesty elements intact
  (token/trajectory/waste DOV, UNAVAILABLE=neutral, source-aware baseline framing, attribution
  table, no composite score). The P3 packaging-bug guard (templates ship in the wheel) holds.

**Test count: 364 green. Detectors frozen (`git diff tes/_waste_detectors.py` empty). Reports
01-11 immutable. Moat intact (local by default; only egress = opt-in API judge).**

---

## P8 BOUNDARY — explicit

**Attribution is measured, not guessed.** B5 ("Fresh input") is a residual — it is NEVER
labeled "productive." The attribution DOV makes this explicit: "whether non-waste tokens were
used WELL is the judge's question, not attribution's."

**API judge is indicative, not validated.** The rubric is the same v3 rubric. The MODEL
(Haiku or other API model) was NOT part of the B3 cross-model corroboration. The DOV
names the model and labels the verdict "indicative, not equivalent to the validated local judge."

---

## Phase 4 (Trends) — PARKED, do not build

**Decision (2026-06-10):** Trends deferred. Data probe showed the current store cannot support
honest trends, and building a trend feature now would draw confident lines through noise.

**Why deferred:**
- **29-day sprint, not sustained history.** All 719 sessions span 2026-05-09 to 2026-06-07.
  44% of sessions are in the final week (W22). A 4-week line implies history that doesn't exist.
- **Circular-baseline trap confirmed with data.** 4 of 5 task types only crossed the
  `min_lean_n=8` self-baseline activation gate in the second half of that 4-week window.
  debug-fix — the only type with an active baseline in both halves — shifted +28% median as
  the lean subset grew from 10 → 20 sessions. "Trending toward baseline" would measure the
  baseline moving toward the user, not user improvement.
- **feature-build is a stub factory.** 518 of 540 feature-build sessions are real_tokens=0
  stubs. Only 22 content sessions exist; only 2 qualifying weeks meet the ≥5/week threshold.
  The "540 sessions" number is misleading.
- **New-user problem.** A new user's first month would get an even worse version of the
  same lie — a trend line through 3–5 sessions per type.

**Cold-start gate (for when trends ARE built):**
- ≥ 3 calendar-week windows with ≥ 5 content sessions of that type, AND
- Self-baseline active (`source='self'`, lean_n ≥ 8) for that type.
- Until both: show "Building your [type] trend — N more sessions or more time needed."
- At a typical pace this gate is met after ~3–4 weeks of active use per type.

---

## What NOT to touch

- **research/01-11-*.md** — All immutable. Reports 01-10 inherited from B1–B4; report 11 is the
  B5 generalization validation final report. Do not edit any of these.
- **data/corpus_pool/** and **data/pool_judge_scores.jsonl** — Do not re-score or modify.
- **data/cc_baselines.json** — Locked for launch-1. Rebuild only for launch-2 with new data.
- **data/cost-log.jsonl** — Append-only. $5 cumulative Anthropic cap; currently ~$2.59.
- **tes/_waste_detectors.py** — Byte-frozen. Do NOT modify without a new research arc.

---

## Repo structure (key paths, 0.7.0 state)

```
token-efficiency-scorer/
├── tes/                        SDK package
│   ├── intelligence/           NEW 0.7.0: ML clustering + chat explainer
│   │   ├── features.py         8-feature vector per content session
│   │   ├── cluster.py          KMeans k=2..8, silhouette k-select, 10-seed stability
│   │   ├── anomaly.py          Tukey outer fence (Q3+1.5×IQR) per cluster
│   │   ├── cache.py            Persistent JSON cache (~/.tes/intelligence_cache.json)
│   │   └── chat.py             Constrained explainer; metrics-only egress; consent gate
│   ├── _waste_detectors.py     BYTE-FROZEN
│   ├── cli.py                  tes score/serve/patterns/ask subcommands
│   ├── store.py                SQLite persistence: sessions table + WAL
│   ├── web/                    Flask localhost dashboard (127.0.0.1 only)
│   └── data/                   cc_baselines.json (locked) + templates
├── research/
│   ├── 01-11-*.md              IMMUTABLE (B1–B5 reports)
│   └── 12_session_intelligence.md  NEW 0.7.0: methodology + validity results
├── tests/                      472 green
│   ├── test_cluster_validity.py       31 tests
│   ├── test_anomaly_threshold.py      12 tests
│   ├── test_chat_grounding.py         31 tests (incl. 5 format-regression)
│   ├── test_chat_no_code_egress.py    19 tests
│   └── test_small_corpus_honest_path.py  14 tests (incl. 2 stamp-regression)
├── CHANGELOG.md                Full 0.7.0 entry
├── pyproject.toml              version = "0.7.0"
└── CURRENT_STATE.md            This file
```

---

## GCP infrastructure status

All B2/B3 GPU VMs deleted after scoring. No persistent infrastructure from any phase.
Estimated B2/B3 GCP spend: ~$3.53 USD. GCP credits pool, not Anthropic cap.

---

## Judge configuration (locked — do not change)

- Model: qwen3:30b-a3b via Ollama ($0/session, GPU required)
- Prompt: v3 — trajectory purposefulness only, /no_think prefix
- Parameters: temp=0, seed=42, num_predict=6144, JSON schema
- DO NOT substitute Claude or any paid API as judge without escalation.
