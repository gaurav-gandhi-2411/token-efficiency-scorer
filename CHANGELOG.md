# Changelog

All notable changes to **tracegauge** are documented here. This project follows
[Semantic Versioning](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/)
conventions.

A note on version numbers: the published PyPI artifacts are `0.1.0`, `0.5.0`, `0.6.0`,
`0.7.1`, and `0.8.0`. Versions `0.2.0` and `0.4.0` were built and tagged internally but
never published to PyPI. `0.8.0` is the **current published release**. `0.9.0` and
`0.10.0` (below) are built, tested, and committed, but **deliberately not published**
pending a publish decision — see each entry for why.

## [0.10.0] — Live Monitor, Cost Alarm & Habit Coach (built and tested; publish escalated)

**Design reviewed before code** (research/13_coach_alarm_honesty_design.md): coaching and
alerting are where tools most often over-claim, so the grounding/data-gating/flat-plan
design was written up and approved before `coach.py`/`alarm.py` existed. Two findings
changed scope from the original spec wording — both documented in that file:

- No confirmed compaction-event marker exists in Claude Code's local transcript format
  (checked real session JSONLs on this machine — every "compact" hit was a false positive).
  The spec's flagship habit example ("sessions where you compacted earlier cost less") is
  **deferred** — shipping 3 other fully-grounded habits instead rather than fabricate detection.
- No rate-limit signal exists locally either (same check, same result). The "rate-limit-
  proximity" framing for flat-plan users is replaced with context-size-relative-to-your-
  own-history, which is honestly buildable from data already computed.

**What's built (643/643 tests green, up from 601 in 0.9.0):**
- `tes/live_monitor.py` — scores the ACTIVE (in-progress) CC session incrementally, reusing
  the frozen attribution/cost engine against whatever the file currently contains. Every
  figure is labeled "estimated, in progress" — never presented as final/billed.
- `tes/alarm.py` — fires ONLY on a two-part measured AND gate (same shape as
  `intelligence/anomaly.py`'s per-cluster Tukey fence): (1) the live session's token count
  already exceeds the user's OWN self-baseline p75 for that task_type, AND (2) context
  re-send is the dominant cost driver (so `/compact` is actually relevant). Silent whenever
  the self-baseline for that type is still "building" — no cry-wolf, no arbitrary global
  threshold. `AlarmConfig` is OFF by default (opt-in, `--alarm` on `tes serve`). Flat-plan
  aware: the message ALWAYS shows both $ and token framings; `plan=max` reorders emphasis
  (tokens lead, dollar figure becomes a labeled "API-equivalent" parenthetical) but never
  hides the dollar figure outright.
- `tes/coach.py` — `tes coach` + dashboard Coach panel surface the top fixable habits ranked
  by measured $ impact: H1 (high context re-send ratio costs more, per task_type), H2
  (recurring RR/RFR waste, using already-persisted waste_events), H3 (sessions scored above
  the user's own baseline band cost more). Every habit states its N, its measured basis, and
  a "measured across your own sessions — not a guarantee" caveat. `MIN_N_FOR_HABIT = 5`:
  silent (no entry at all) below the gate, never a fabricated tip. H4 (compaction timing)
  deferred per the design-doc gap above.
- `tes/budget.py` — `tes budget` + dashboard Budget view: rolling-window (default 7 days)
  spend tracking with an honest self-trend projection, always labeled "based on your last N
  days, not a forecast of future work." Silent (returns `None`) when there's nothing in the
  window to project, rather than fabricating a $0 projection.
- `tes/watcher.py` — extended (additively) with `alarm_enabled`/`plan_type` fields; checks
  the live monitor + alarm once per scan cycle when enabled, printing to stderr on fire.
- Dashboard: `/coach`, `/budget`, `/monitor` routes + templates, honest labels throughout;
  all prior routes (`/`, `/session/<id>`, `/trends`, `/baseline-status`, `/patterns`, `/ask`)
  regression-confirmed unchanged.
- `tests/test_alarm_measured.py`, `test_coach_grounded.py`, `test_projection_labeled.py`,
  `test_live_cost_estimated.py`, `test_prior_features_intact.py` (42 new tests) — cover the
  no-cry-wolf proof (silent on a normal/building/non-resend-dominant session), the N-gate
  silence property, flat-plan-safe message construction, and full regression.

**Live proof (this machine, real active session, 2026-07-04):** `tes monitor` against a
genuinely heavy real session correctly fired — "~$7.67 (estimated, in progress) and
~801,271 context tokens (estimated, in progress), 94% of which is re-sent context (measured)
— well above your own typical ml-eval session (p75: 626,012 tokens). Consider `/compact`."
The synthetic no-cry-wolf tests cover the silent-on-normal-session proof (a live real
session below any user's own p75 doesn't currently exist in the corpus to demo directly).

**Non-negotiables held:** `git diff --exit-code tes/_waste_detectors.py` empty throughout;
self-baseline/attribution/cost math untouched (consumed, not altered); import-closure green
(zero new dependencies — coach/alarm/budget/live_monitor use stdlib + existing tes internals
only, per the approved zero-dep default); local-only (live monitor reuses the watcher's file
tail, no new egress); dormant 0.9.0 corpus untouched and still dormant.

**Clean-room verified (2026-07-04, `tes-verify-0100`, `--no-default-packages`):** built the
`0.10.0` wheel, confirmed numpy/tracegauge absent before install, installed from the wheel,
confirmed `tes.__file__` resolves to site-packages (not repo) from a neutral cwd. `tes
--version` → `tes 0.10.0`. All new subcommands (`tes coach --help`, `tes budget --help`,
`tes monitor --help`) present with correct text. All 7 dashboard routes (4 prior + 3 new)
returned 200 from the installed wheel, not just the repo copy.

**NOT done, by choice:** PyPI publish — escalated per the project's standing rule (this
phase's central risk was the coach/alarm honesty design, which was reviewed and approved
BEFORE code; the publish itself is a separate, always-escalated action).

## [0.9.0] — Community Corpus (built and tested; NOT published — corpus stays dormant)

**Decision (2026-07-02):** this release's headline feature — the opt-in community corpus —
is fully built, tested (601/601 passing), and proven (RLS proof, send-time byte-grep proof,
clean-room install proof — see below), but the Supabase corpus itself has deliberately **not
been provisioned**. With zero contributors, a live corpus of one delivers no user value, and
crossing the transmission boundary before there's anyone to receive value from it is
premature. `pyproject.toml` carries `version = "0.9.0"` and this code is committed to the
repository, but **it is not published to PyPI** — publishing a release whose headline
feature is inert adds nothing for users and would make PRIVACY.md describe infrastructure
that doesn't exist yet. The next publish happens when either (a) a corpus is provisioned and
activated (see `CURRENT_STATE.md` and `corpus/setup.md` for the exact activation steps —
Supabase project → schema.sql → Edge Function → three env vars → round-trip proof → publish),
or (b) a different user-facing improvement justifies a release on its own.

Everything below is real, tested code sitting dormant behind an unconfigured destination —
not a description of something currently happening to any user's data.
**Engine, detectors, reports, and self-baseline scoring are byte-for-byte unchanged; this
release is a new opt-in data path plus its presentation, not a change to how anything is
scored.**

### Added

- **`tes corpus contribute`** — builds the same 14-field content-free payload as
  `export-contribution` (0.8.0's P7 builder, reused unchanged), shows the exact real row
  plus full consent screen (fields sent, what's never sent, destination named as a third
  party, use, contributor_id, withdrawal warning), and on explicit `y` would send it to the
  tracegauge community corpus (Supabase, `eu-west-1`) — **once one is provisioned**. Today,
  with no corpus configured, it prints `[NOT SENT] the community corpus is not configured on
  this install` and makes no network call, regardless of consent.
- **Send-time content-free re-verification (`tes/corpus_client.py`)** — the ACTUAL
  serialized POST body (not the in-memory payload) is independently re-checked immediately
  before the network call: every row's keys must be exactly the 14 allowed fields, and every
  value is checked against what's legitimate for its field (known-value sets, format
  patterns, numeric-type checks, a 30-character cap on any other string). A failure raises
  `ContentLeakGuardError`, aborts the send before any network call, and writes a local-only
  `~/.tes/contribution_blocked.log` entry — the guard runs whether or not the payload came
  from the already-tested builder, so a future bug elsewhere cannot silently reach the wire.
- **`tes corpus withdraw`** — deletes every row tied to your `contributor_id` via a
  service-role Edge Function (the only path that can delete anything — the client's `anon`
  role has insert-only RLS access, no select/update/delete). Also deletes the local
  `~/.tes/contributor_id.txt` on confirmed success, so a future contribution starts under a
  fresh, unlinked ID.
- **`tes corpus reset-id`** — generates a new `contributor_id` locally (no network); prior
  rows become unlinked from future contributions.
- **`corpus/schema.sql` + `corpus/edge_functions/withdraw-contributor/`** — the Supabase
  table (columns exactly matching the 14-field allow-list) and RLS policy (anon: insert
  only, no select/update/delete — enforced independently of the client's own content-free
  guarantee), and the withdrawal Edge Function (validates `contributor_id` as UUIDv4 before
  the service-role-authenticated delete). `corpus/setup.md` documents reproducible setup.
- **`tes/community_baseline.py`** — offline batch computation of per-task-type percentile
  statistics from pooled contributed rows (published as a data file, fetched the same way
  the bundled self-baseline ships today), and client-side scoring against it. A task_type
  below a minimum-contributor floor (5 distinct contributors) is reported but marked
  unscoreable rather than shown as a misleadingly precise percentile. Every scored result
  carries a domain-of-validity string: contributor/session counts, self-selection bias, and
  content-free coarseness — there is no path that shows a community percentile without it.
- **`PRIVACY.md`** — replaced the "nothing is transmitted" contribution section with the
  full transmission disclosure: what/where (Supabase, `eu-west-1`, GDPR)/retention (until
  withdrawn)/deletion path/anonymity and its limits (a linkability caveat is disclosed, not
  hidden)/the corrected content-free mechanism description (allow-list + length checks, not
  pattern-matching — described as what the code does, not a stronger claim)/hosting-chain
  disclosure naming Supabase's technical access alongside a link to Supabase's own privacy
  policy.

### Non-negotiables held (verified by tests)

- Default install transmits nothing — `tes corpus contribute`/`withdraw` are the only code
  paths that call `tes.corpus_client`, and both are unconditionally gated on explicit
  consent/confirmation, checked before any network call (`test_transmit_optin.py`).
- Content-free payload re-verified on the actual POST bytes at send time, proven by
  byte-grepping the real payload and by planting a secret in every field type — a string
  field, the `waste_detectors_fired` list, and a numeric-as-string attempt — and confirming
  the guard catches each (`test_send_content_free.py`).
- Withdrawal works and is honest when it can't: missing/malformed `contributor_id.txt`
  refuses rather than guessing (`test_withdrawal.py`).
- RLS proof: `corpus/schema.sql` has exactly one policy (anon insert-only), no
  select/update/delete policy for anon anywhere in the file, and its columns match
  `ALLOWED_FIELDS` exactly (`test_corpus_rls.py`).
- `git diff tes/_waste_detectors.py` empty; self-baseline scoring path untouched by this
  release; import-closure green (no new dependency — `httpx` was already declared).
- Clean-room proof: built the `0.9.0` wheel, installed into a fresh `--no-default-packages`
  conda env, ran from a neutral cwd (outside the repo, so the installed wheel was actually
  exercised, not the local source tree). `tes corpus contribute` with explicit `y` and no
  `TES_CORPUS_*` env vars set → `[NOT SENT] the community corpus is not configured on this
  install` — a fresh install cannot transmit even under forced consent.

### Deliberately not done

- **No Supabase project provisioned; not published to PyPI.** See the decision note at the
  top of this entry. The RLS proof above is static (schema-text assertions against
  `corpus/schema.sql`) — there is no live Supabase project to test against, by choice, not
  by omission. The end-to-end round-trip (contribute → row appears → withdraw → gone) against
  a real project is the last proof before activation, and it happens when a corpus is
  actually provisioned — see `corpus/setup.md` for the exact steps.

## [0.8.0] — 2026-06-15 — Dashboard Intelligence + Sortable Session List

Brings the Session Intelligence features (ML archetypes, natural-language Q&A) from the CLI
into the browser dashboard, and makes the session list sortable. **Surfacing + sorting only —
the engine, ML, chat grounding, cost math, and detectors are byte-for-byte unchanged.**

### Added

#### Part B — Sortable session list

- **Clickable column headers** on the session list: sort by **Cost**, **Date**, **Tokens**,
  **Waste**, or **Token verdict** via server-side `?sort=&dir=` query params. No SPA, no
  browser storage — pure server-rendered Jinja2 and `ORDER BY` on query params.
- **Active-column sort arrow** (↑/↓) rendered in the header that reflects the current sort key
  and direction. Default sort: date descending (most recent first, matching the pre-0.8.0 order).
- **New Cost and Tokens columns** in the session list table, surfacing what was previously only
  visible inside session detail.
- **`list_sessions()` sort params:** `order_by` and `direction` added with a strict 5-key
  whitelist (`date/cost/waste/tokens/verdict` → actual DB column names). Unknown keys fall
  back to `scored_at DESC`. SQL injection surface: zero — the column name never comes from
  user input; only the whitelisted value reaches the query.
- **Honesty elements all survive sorting:** the domain-of-validity caveat, UNAVAILABLE-neutral
  trajectory badge, "not a score" price provenance, baseline-source badge — all verified by
  test assertions on every sort permutation.

#### Part A — Dashboard Intelligence

- **`/patterns` page (nav item):** Session archetypes as visual cards (bar charts for context
  re-send, context growth, output, has-waste; dominant-feature labels; task-type mix). Validity
  header (k, silhouette, session count, computed timestamp). Anomaly count and pct. Domain-of-
  validity caveat. "Descriptive only — not predictive, not quality labels" framing — identical
  to `tes patterns` CLI. Small-corpus floor honored by construction: below 30 content sessions,
  shows "Not enough sessions yet" message and no archetype cards.
- **LLM / judge status chips** in the Patterns page header: green "Ollama running — local
  inference, no egress" / grey "Ollama not detected" / amber "ANTHROPIC_API_KEY set — consent
  required before use". Read-only status indicators only — no one-click API-judge enable.
- **Web Ask panel** on the Patterns page: text input → POST `/ask` → grounded answer rendered
  in the browser. Carries **identical guards to `tes ask`**, enforced by construction:
  - Same `ask_local()` / `ask_api()` functions as the CLI (not a copy — the same code path).
  - Same `CHAT_SYSTEM_PROMPT` object: "I don't predict future behavior" fires in-browser.
  - Same `build_chat_context()`: metrics-only, no raw session content/code/paths structurally.
  - API consent: checkbox shown in UI (only when Ollama absent + API key detected); route
    returns `{needs_consent: true}` error without calling `ask_api` unless `api_consent=True`.
  - Question length capped at 500 chars server-side before any LLM call.
  - Small-corpus floor: below-floor Ask works (corpus stats provided); no invented archetypes.
  - Local-first routing: Ollama answer used; `ask_api` never called when local succeeds.

### Fixed

- **Route-registration gap (closes "tests-pass-but-real-server-fails" class):**
  `test_route_registration.py` imports `create_app` / `start_server` via the same path `tes serve`
  uses, inspects `url_map` directly, and asserts `/patterns` + `/ask` are registered. The
  previous test gap: a fixture-level GET test passes even when the installed artifact lacks the
  route (analogous to the numpy gap caught by `test_dep_closure.py` in 0.7.1).

### Tests

543 green (+70 new across four files):
- `tests/test_session_sort.py` (23): whitelist unit tests, sort-key ordering, Flask route with
  honesty-element assertions on every sort permutation.
- `tests/test_web_patterns.py` (17): floor honored (no archetype grid below 30), validity stats
  shown, DOV + descriptive caveat, judge status chips, Ask panel rendered.
- `tests/test_web_ask_guards.py` (21): G1 metrics-only egress, G2 identical backend (same
  functions as CLI), G3 "I don't predict" pass-through, G4 "not measured" pass-through, G5
  floor no-crash, G6 consent gate (no network without `api_consent=True`), G7 `ask_api` gate
  inherited, G8 question length cap, G9 local-first routing.
- `tests/test_route_registration.py` (9): url_map assertions via same path as `tes serve`,
  GET /patterns → 200, POST /ask empty → 400, GET /ask → 405 not 404.

Detectors frozen. Import closure green (no new Python deps — Ask-panel JS is vanilla inline
script, no framework, no CDN).

---

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
