# CURRENT_STATE.md — token-efficiency-scorer

Snapshot as of 2026-06-12 (P8 DONE). Read this BEFORE planning. This supersedes
the prior snapshot dated 2026-06-11 (P7 DONE).

---

## Iteration status: P8 DONE — Token Attribution + Judge On-Demand (v0.4.0)

**What P8 delivered:**

**`tes/attribution.py` — Six-bucket token attribution:**
- `compute_attribution(digest, waste_entry, prices)` → `AttributionResult`
- 6 reconciling buckets: B1 RR waste, B2 RFR waste, B3 Context re-send (cache reads),
  B4 Output, B5 Fresh input (not attributable to detected waste), B6 Context growth (cache writes)
- Algebraic invariant: B1+…+B6 == `total_billed_tokens` (tested, held on real data)
- Hard-locked labels: B3 never "bloat", B5 never "productive", B6 never "wasteful"
- Attribution basis = `total_billed_tokens` (all billed incl. cache re-reads); distinct from
  `real_tokens` verdict basis — labeled "over ALL billed tokens" throughout
- Dollar view alongside token view: cache_read billed at 0.1×, so B3 = 95% of tokens but
  49% of cost — the divergence IS the diagnostic insight

**`tes/judge.py` — API judge (opt-in, explicit consent):**
- `ApiJudgeConfig`, `score_trajectory_api()`, `build_api_judge_consent_notice()`
- `consent_given=False` → return None unconditionally, ZERO network calls
- Same v3 rubric as validated local judge; same system prompt, same user template
- Consent screen separates "secrets redacted" from "content-safe" (honest: 300-char snippets
  MAY contain code/file content, other content NOT filtered)
- `JUDGE_SETUP_HINT_FULL` points to both --judge (Ollama) and --api-judge (API key) paths

**`tes/score.py` — API-judge DOV:**
- `build_api_trajectory_dov(api_model)`: carries B3 caveats + extra: "{model} was NOT part
  of B3 cross-model corroboration — treat verdict as indicative, not equivalent to validated
  local judge." API-judge DOV ≠ local-judge DOV (enforced by test)

**`tes/cli.py` — --api-judge flags:**
- `tes score <path> --api-judge [--api-judge-model MODEL] [--api-judge-key KEY]`
- Resolves key from flag or ANTHROPIC_API_KEY env; shows consent notice; requires explicit `y`
- --background-judge help updated to mention both local and API options
- UNAVAILABLE output now points to both --judge and --api-judge

**`tes/web/server.py` + templates — Attribution dashboard:**
- `session_detail`: computes attribution from source JSONL (graceful None if file missing)
- Dollar-ranked table: sorted by cost% DESC; shows both tok% and cost% side-by-side
- One-line takeaway with data-gated actionable hint:
  - context >= 60% of cost → "— a long context drove most of the cost; checkpointing or /compact mid-session reduces re-send."
  - output >= 40% of cost → "— output was a large cost share; shorter responses or fewer regenerations reduce this."
  - neither fires → description only
- real_tokens vs total_billed note beneath table (prevents verdict-vs-attribution confusion)
- session_list: stored-data attribution one-liner (total + waste events, no file I/O)
- Trajectory UNAVAILABLE now points to both on-ramps (Ollama and --api-judge)

**Key P8 diagnostic confirmed on real data:**
- infra-deploy (6.3M real_tokens, $83/session): context 70% of cost (49% re-send + 21% growth),
  output 30%, waste $0.15 (0.2%) — "carrying large contexts, not thrashing"
- ml-eval (7.4M real_tokens, $117/session): context 77% of cost (59% re-send + 18% growth),
  zero waste — same pattern
- token-vs-dollar divergence confirmed: B3 = 95% of tokens but 49% of cost (billed 0.1×);
  output = 1% of tokens but 30% of cost — side-by-side table makes this visible

**New tests (P8):** 48 tests across 6 files — attribution reconciliation (8), attribution rules (12),
API judge opt-in (7), API judge rubric consistency (7), judge caveats (14).
**Test count: 339 green (297 pre-P8 + 42 P8). Detectors frozen. Reports 01-11 immutable.**

**MOAT held:** local scoring stays local; ONLY egress = consented API judge call (user's key,
direct to provider, consent_given gate unconditional). Default install transmits nothing.

---

## P8 BOUNDARY — explicit

**Attribution is measured, not guessed.** B5 ("Fresh input") is a residual — it is NEVER
labeled "productive." The attribution DOV makes this explicit: "whether non-waste tokens were
used WELL is the judge's question, not attribution's."

**API judge is indicative, not validated.** The rubric is the same v3 rubric. The MODEL
(Haiku or other API model) was NOT part of the B3 cross-model corroboration. The DOV
names the model and labels the verdict "indicative, not equivalent to the validated local judge."

---

## Iteration status: P7 DONE — Corpus contribution, client-side & send-disabled

**What P7 delivered:**

**`tes/contribution.py` — allow-listed payload builder:**
- `build_contribution_payload()`: builds per-session rows field-by-field from the 14-field
  allow-list only. Never serializes a session object and removes fields — a future store column
  cannot leak by construction. Re-adapts source JSONL (backfill_cost pattern) to populate
  `token_count_input`, `token_count_output`, `cache_creation`, `cache_read`, `model`; nulls
  for inaccessible sources.
- Three value-level closed-set guards (not just key guards): `task_type` → known 5 types or
  "other", `model` → allow-listed 16 keys or "other", `waste_detectors_fired` → only
  `{"REPEATED-FAILED-RETRY", "REDUNDANT-READ"}` — unknown strings silently dropped.
- `contributor_id`: random opaque UUID from `~/.tes/contributor_id.txt`; not identity-derived;
  regeneratable; omittable via `--anonymous`.
- `week_bucket`: ISO year-week from `source_mtime` — no precise timestamp.

**`tracegauge export-contribution` CLI command:**
- Shows consent/preview BEFORE writing: real sample row from the user's store (all
  numbers/categoricals/UUID/ISO-week — zero content), full field list, explicit NEVER-INCLUDED
  list, output path, and "NOTHING is transmitted anywhere — tracegauge has no server."
- Requires explicit `y` confirmation. `--preview` shows preview without writing.
- Writes `~/.tes/contribution-<date>.jsonl` (human-readable JSONL, user inspects and controls).
- `--anonymous` omits contributor_id. `--output` overrides path.

**Safety tests (strongest verification in the project):**
- `test_contribution_content_free.py`: plants real secrets (API keys, file paths, project names,
  exotic model strings, evidence snippets, judge reasoning, session IDs) in every hiding spot;
  serializes the payload to JSON bytes; asserts zero hits for every planted string. Tests VALUES,
  not just keys. Omnibus test covers all in one pass.
- `test_contribution_allowlist.py`: every row has EXACTLY ALLOWED_FIELDS keys; extra key = fail.
- `test_contribution_no_network.py`: patches `socket.socket.connect` to fail-hard; asserts no
  network call is made during `build_contribution_payload`. Any accidental future network import
  breaks this test before shipping.
- `test_contribution_consent.py`: preview shows real row + field list + exclusions; file not written
  without `y`; anonymous path; field keys confirmed post-write.

**README/PRIVACY honest moat update:**
- Tagline: "No server, nothing transmitted — ever. An optional command exports a redacted local
  file you inspect and control."
- Moat section: "local by default" replaces "your session logs never leave your machine"; optional
  export described accurately with PRIVACY.md link.
- `tes serve` moat line: removed "no data leaves the machine" (no longer unconditional); replaced
  with "no external network calls" (still unconditional for serve/score).
- "What this does NOT do": updated to reflect P7 built but server-side not built.
- `PRIVACY.md` (new): complete field table, exclusions, allow-list-by-construction note,
  contributor_id non-derivation, WHY the export exists, transmission section.

**Version fix:** `pyproject.toml` bumped from `0.1.0` → `0.2.0` to match the v0.2.0 git tag
set at P4. `tracegauge_version` in contribution rows now shows `0.2.0` (was reading stale
installed metadata). Package reinstalled.

**Test count: 296 green (244 pre-P7 + 52 P7). Detectors frozen. Reports 01-11 untouched.**

---

## P7 BOUNDARY — explicit, do not cross in future sessions without re-establishing consent

**P7 is client-side only. LOCAL FILE, NOTHING TRANSMITTED.**

The following are NOT built and are a SEPARATE future decision:
- Any server-side aggregation, upload endpoint, or network transmission
- Pooled-baseline computation (how to validate a corpus of contributed rows — requires its own
  B2-level validation arc once data exists)
- Distributing pooled baselines back to users
- The legal surface beyond `PRIVACY.md`: privacy policy, data retention, GDPR/CCPA compliance,
  terms of service — ALL required BEFORE any data ever leaves a machine
- Any consent flow for transmission (the P7 consent flow is for local-file export only)

A future session that builds transmission infrastructure must treat this as a NEW greenfield
decision with explicit user go-ahead — not as a continuation of P7. The contribution file
currently sitting at `~/.tes/contribution-<date>.jsonl` is the user's file; tracegauge never
reads it back, never uploads it, and no daemon watches for it.

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

**Trending approach when eventually built:**
- Trend raw `real_tokens` or raw `session_cost_usd` over time.
- Show the current self-baseline as a static reference line.
- Do NOT trend "delta from baseline" — the baseline moves as sessions accumulate.

**Next step:** Product decision in progress on what is most valuable given waste came back
small and trends are not yet supportable. Standing by.

---

## Iteration status: P6 DONE — Waste backfill + per-event cost annotation

**What P6 delivered:**

**Correctness fix — waste backfill:**
- `backfill_waste()` in `tes/store.py`: re-runs frozen detectors on all accessible sessions,
  embeds `wasted_cost_usd` per event into the `waste_events` JSON blob. Hash-independent
  (fixes the stale-zeros bug: sessions scored before waste detection was wired show 0 in store).
- `tes backfill-waste` CLI subcommand with `--db-path` option.
- Expected reconcile against inventory: 43 events / 22 sessions / ~$1.89 total wasted cost.

**Cost annotation definition (wasted_cost_usd per event):**
- `proof_turns[2:]` only — the redundant turns. `proof_turns[0:2]` is the first
  (legitimate) call+result pair; it's real work, not waste.
- RR-A events have only 2 proof turns → redundant = [] → `wasted_cost_usd = 0.0`.
- `annotate_waste_costs()` in `tes/waste.py`: mutates event dicts in-place, returns same list.
- Embedded in the existing `waste_events` JSON blob — no new DB column, no migration.

**PATH-A behavioral note (not a regression):**
- `REDUNDANT-READ PATH-A` fires zero events on the live session population (2026-06-10).
- ROOT CAUSE: CC no longer emits "File unchanged since last read" in `content_snippet`
  on current versions. The PATH-A check (`snip.startswith("File unchanged since last read")`)
  is a plain string match — it was never broken by regex changes.
- THIS IS NOT the v2.1.38 PATH-B break (that was `^\d+\t` failing on arrow-format output,
  fixed in P1 with dual-format `r"^\d+\t|^\s+\d+→"`).
- Waste coverage on current CC is effectively PATH-B only (redundant content reads) + RFR.
- Detector is frozen — do NOT add a fallback or patch PATH-A until CC re-emits the hint.

**Surface changes (per-event cost visible, not loud):**
- `tes/report.py` `format_human()`: waste event lines now show `~$0.042` inline when nonzero.
- `tes/web/templates/session_detail.html`: "Wasted Cost" column in waste events table.
- `tes/web/server.py` `/baseline-status` route: queries `waste_by_type` from store.
- `tes/web/templates/baseline_status.html`: "Waste Concentration" section — one paragraph,
  shows per-type distribution and zero-waste in feature-build. Shows "run backfill-waste" note
  when store still has all-zeros.

**Tests:** 10 new unit tests in `tests/test_waste_costs.py` covering `annotate_waste_costs`
(RFR 2-repeat, RFR 3-repeat, RR-B, RR-A zero-cost, empty list, in-place mutation, missing
turn in map, multi-event, all-zero, empty turns list).

**Test count: 194 green (184 P4 + 10 P6). Reports 01-11 untouched. Detectors frozen.**

---

## Iteration status: P4 DONE — Self-Baselining active, v0.2.0 tagged

**Git tag:** `v0.2.0`
**What changed:** Token verdicts now compare against user's own lean waste-free sessions
(self-baseline), not the infra-heavy B2 reference corpus.

**P4 results:**
- Self-baselines ACTIVE on all 5 task types (debug-fix, feature-build, infra-deploy,
  ml-eval, research-recon). Lean subsets: 9–20 sessions per type.
- Content-session coverage: 187/210 sessions with actual work scored (89.0%).
  Pre-self-baseline (B2 corpus): 174/210 (82.9%). +13 sessions from lower scope floors.
- Empty/stub sessions: 509 of 719 total — legitimately OOS (no work product),
  excluded from the coverage denominator, not treated as scoring failures.
- Watcher wired: `run_watcher` calls `load_or_compute()` each scan cycle; new sessions
  route through self-baseline from this commit forward.
- Dashboard shows honest split: content-session headline (89%) + stubs as a separate fact.
  Historical B2 anchor hardcoded for before/after comparison.
- 184 tests green. Frozen: `tes/_waste_detectors.py` unchanged.

**Scope floor corrections (P4):**
- feature-build: B2 p10=166 → self floor=20 (zero-token stubs excluded from p10)
- infra-deploy: B2 p10=63 → self floor=37
- ml-eval: B2 p10=127 → self floor=96
- debug-fix, research-recon: unchanged (59, 44)

---

## Iteration status: P3 DONE — tracegauge 0.1.0 published to PyPI

**PyPI:** https://pypi.org/project/tracegauge/0.1.0/
**Git tag:** `v0.1.0` → commit `c5858820` ("docs: CURRENT_STATE P3 in-progress — detector byte-verbatim, adapter verified, wheel clean-room passed")
**Install:** `pip install tracegauge`

**What P3 delivered:**
- `pyproject.toml` rewritten: name=tracegauge, version=0.1.0, AGPL-3.0-only (PEP 639 SPDX),
  `requires-python = ">=3.10"`, both `tes` and `tracegauge` console-script entry points,
  `package-data` for `cc_baselines.json`, pinned runtime deps (flask>=3.0,<4, httpx>=0.27,<1).
- `LICENSE` file: full AGPL-3.0 text (34,523 bytes), verbatim from gnu.org.
- `__version__` single-sourced via `importlib.metadata.version("tracegauge")` in `tes/__init__.py`.
  `tes --version` / `tracegauge --version` both emit `tes 0.1.0`.
- `README.md` rewritten as honesty-forward PyPI landing page: quickstart first, Scope & Limitations
  second (corpus caveat 1.4% vs 6.6%, no-human-accuracy, tiered judge, moat), three-axis docs,
  "What this does NOT do" section, AGPL link, B5 validation arc.
  All research links are absolute GitHub URLs (clickable on PyPI).
- `tests/test_packaging.py`: 5 packaging integrity tests.
- Total tests: 163 passing (158 P1+P2 + 5 packaging). Reports 01-11 immutable.

**Packaging architecture (frozen-detector guarantee):**
- `tes/_waste_detectors.py`: **BYTE-VERBATIM COPY** of frozen `scripts/waste_detectors.py`
  (diff-proven: one docstring module-path line differs, zero functional differences).
- `tes/adapt.py` + `tes/_digest.py`: option-B-unavoidable re-home (original used repo-relative
  `sys.path.insert`). Output-verified on pool (RFR 12/181, RR 20/181) + B5 SWE-chat CC
  (1,053 sessions from 172 developers: RFR 15, PATH-A 4, PATH-B 51).

**Post-publish fresh-venv verification (real PyPI, 2026-06-08):**
- `pip install tracegauge` from production PyPI → 0.1.0, all deps resolved (flask, httpx + transitive)
- `tes.__file__` → site-packages (not repo) ✓
- `BUNDLED_BASELINES_PATH` → site-packages/tes/data/cc_baselines.json, exists=True ✓
- `tes --version` → `tes 0.1.0` ✓
- `tracegauge --version` → `tes 0.1.0` ✓
- `tes serve --help` → full help text ✓
- `tes score <gold-rate-tracker session, 7.4M tokens>` → TOKEN above_p75 (ml-eval), TRAJECTORY
  UNAVAILABLE (no judge configured, correct), WASTE 0 events, all three domain-of-validity
  caveats present; secret redaction fired (groq_key + wandb_api_key redacted in-flight) ✓

**Known minor items for a future 0.1.1 (not blockers):**
- Research links are absolute GitHub URLs — will be live on PyPI ✓ (already done in P3)
- `tracegauge --version` shows `tes 0.1.0` (prog name from argv[0]); cosmetic, not a bug.

---

## Iteration status: P2 DONE — tes serve (watcher + localhost dashboard)

P2 turns the P1 manual CLI into an always-available local service. `tes serve` launches a
background watcher that auto-scores finished CC sessions (token + waste) and a Flask
MLflow-style dashboard on `http://127.0.0.1:PORT/` where scores accumulate.

**What P2 delivered:**
- `tes/store.py`: SQLite persistence (`~/.tes/tes.db`). Single `sessions` table = scored
  result + incremental ledger + judge-staleness signal (`judge_source_hash`). Schema version
  via `PRAGMA user_version=1`. WAL mode (`PRAGMA journal_mode=WAL`) for concurrent watcher
  write + dashboard read. Judge-preservation merge: a background re-score never overwrites
  a manual judge result; stale judge flagged when session grew after judging.
- `tes/watcher.py`: Scheduled-scan + file-stability trigger (sole P2 mechanism). Incremental
  via hash-ledger (unchanged sessions skipped). Judge OFF by default (`background_judge=False`
  in `WatcherConfig`). Per-session failure isolation: one bad file never aborts the scan.
  GIL yield between scorings (`time.sleep(0)`) for dashboard responsiveness during initial scans.
- `tes/web/`: Flask + Jinja2 localhost dashboard. Binds `127.0.0.1` only. Three views:
  session list, per-session detail (all three domain-of-validity caveats verbatim, TrajectoryRenderState
  dispatch: UNAVAILABLE / CURRENT / STALE-with-note), trends (no composite score). Threaded Flask
  server for concurrent request handling.
- `tes/cli.py`: `tes serve` subcommand (watcher + web together, all flags). `tes score` now
  also writes to the store (manual + auto scores share one dashboard).
- SessionEnd hook: deferred. The hook exists in CC (`settings.json`) but modifying the user's
  config silently is against the moat posture. Future `tes install-hook` command = explicit
  opt-in only.
- **Behavior preservation held**: P1 pipeline unchanged; same session → same ThreeAxisResult
  whether scored manually or by the watcher.
- **Moat intact**: localhost-only bind (verified by test), no data off-machine, no telemetry,
  redaction on by default.
- **End-to-end smoke**: 39+ CC sessions auto-scored in one `tes serve` run, 30/30 dashboard
  pages loaded concurrently, zero `database is locked` errors (WAL confirmed live).
- 158 tests green (P1's 140 + 18 P2: store ×6, watcher ×11, dashboard ×5). Installable.
  Reports 01–11 untouched.

**What P2 explicitly does NOT do (deferred):**
- SessionEnd hook auto-install (opt-in only via future `tes install-hook`)
- Corpus-upload pipeline (design-only; store schema is export-compatible)
- Hosted judge / data-leaves-machine
- Cross-agent (non-CC) support

---

## Iteration status: P1 DONE — installable CLI + SDK

P1 packages the validated B1–B5 scoring components into an installable self-hosted tool.
`pip install -e . && tes score <path>` runs end-to-end on real CC logs. The self-hosted moat
(no data leaves the machine, secrets redacted at ingestion) is intact by construction.

**What P1 delivered:**
- `tes/` SDK package: adapt → waste-detect → score (token + waste, always) → optional judge → ThreeAxisResult
- `tes score <path>` CLI: three-axis human report with domain-of-validity per axis + `--json` mode
- Tiered judge (option D): deterministic axes always run; trajectory axis runs if local Ollama +
  Qwen3-30B available, else prints UNAVAILABLE as a complete state (not an error)
- Output honesty: all three domain-of-validity caveats print inline; UNAVAILABLE never coerced;
  no composite score; waste events carry proof turns
- Secret redaction ON by default at ingestion (17 patterns; off only with explicit flag)
- 140 tests green: behavior-preservation (20 golden sessions × 15 fields), judge-tiering (8),
  caveats-present (8), redaction-default-on (5), waste-detectors (99 total including arrow-format)
- No-network verified: tes/score.py, tes/waste.py, tes/baselines.py make zero network calls
- Installable: `pip install -e .` → `tes score --help` works; entry point wired in pyproject.toml

**PATH-B fix (waste_detectors.py un-frozen):**
`_LINE_NUMBERED_RE = re.compile(r"^\d+\t|^\s+\d+→")` — dual-format for pre-v2.1.38 (tab)
and v2.1.38+ (arrow). Pool re-run: 18/18 tab-format counts byte-identical to B4. SWE-chat CC:
51/1,053 sessions fire (4.84%). Updated in CURRENT_STATE during P1; waste_detectors.py is no
longer frozen.

**Reports 01–11: confirmed immutable.** No research files modified in P1.

---

## Iteration status: B5 DONE — credibility arc B1–B5 complete

B3–B5 are complete. B4 (deterministic waste detection) closed with report 10: two detectors
shipped (REPEATED-FAILED-RETRY, REDUNDANT-READ), two documented as exploratory (DEAD-END,
EMPTY-TURN). B5 (generalization validation) closed with report 11: detectors tested read-only
against SWE-chat (1,053 CC sessions, 172 developers). RFR + PATH-A generalize; single-developer
limitation PARTIALLY RETIRED. Pool characterized as high-waste infra/ML-ops outlier (qualifies
B2/B4 absolute rates). PATH-B silently broken on CC v2.1.38 (maintenance issue logged, fix
documented not applied). Credibility arc B1–B5 complete.

Read report 11 (research/11-generalization.md) for the complete B5 findings and the
superseding scope statement on detector generalization and corpus characterization.

---

## B3 summary (closed)

**Finding:** LLM waste judgments are model-dependent. Gemma 3 27B reversed 94.4% (17/18) of
Qwen's WORSE/MUCH_WORSE verdicts on the same digests. Positive verdicts (MUCH_BETTER) are
corroborated at 84% strict / 96% top-2. The B2 baselines rest on a corroborated foundation.
Report 09 is final and immutable.

**GCP:** VM deleted. No persistent infrastructure from B3.

---

## B4 summary (done)

**What shipped:**
- `REPEATED-FAILED-RETRY` detector — 12/181 sessions (6.6%), 0/18 overlap with Qwen WORSE.
  Fires on exact-match shell retry loops with no state change. 25 unit tests.
- `REDUNDANT-READ` detector (PATH A + PATH B) — 20/181 sessions (11.0%), 3/18 overlap with
  Qwen WORSE. PATH A: CC tool's own "File unchanged" verdict. PATH B: content-match, gap≤5.
  19 unit tests. (Combined test suite: 46/46 pass.)
- `data/pool_waste_signals.jsonl` — per-session detector output for all 181 sessions.
- `scripts/efficiency_score.py` — three-axis scorer: token-economy + trajectory-quality +
  deterministic waste. No composite score; each axis carries its stated domain of validity.
- `research/10-deterministic-waste.md` — FINAL report: detectors, fire rates, Qwen
  cross-check, exploratory findings (DEAD-END, EMPTY-TURN), and the central boundary finding.

**What is exploratory (NOT shipped):**
- `DEAD-END/LOOP` — fails at implementation (3 domain-specific header exclusions; concept
  requires evaluating whether loop was productive, not just whether headers repeated).
- `EMPTY-TURN` — fails at definition (empty ai turns in CC JSONL are ambiguous between
  genuine no-ops and extended-thinking turns; prototype cases ARE the B3 dispute).

**Central B4 finding (the boundary):**
Observable-invariant waste (same command + no state change; same content + no edit) is
deterministically detectable. Judgment-of-progress waste (was this cycle productive, was
this turn purposeful) is not — it requires evaluator judgment that reintroduces the
model-dependency problem B4 was built to solve. Future human labeling targets the second
category.

---

## B5 summary (done)

**What B5 validated:**
- RFR fires across 172 independent developers (SWE-chat CC, 1,053 sessions). Single-developer
  asterisk PARTIALLY RETIRED for RFR and PATH-A.
- Rate gap (6.6% pool vs 1.4% SWE-chat CC): pool is a high-waste infra/ML-ops outlier. All 12
  pool RFR events are GCP/SSH/pytest-env sessions. 1.4% is the generalizable real-world rate for
  ordinary software development.
- Corpus characterized as high-intensity ML-ops outlier: B2 token baselines and B4 waste rates
  are calibrated to this context, not a representative developer. This qualifies (does not
  invalidate) prior baselines — domain limitation, not a measurement error.

**PATH-B maintenance issue (fix applied in P1):**
- PATH-B was silently broken on CC v2.1.38+: Read output changed from `\d+\t` to `   \d+→` (arrow
  format). Regex `^\d+\t` failed to match.
- Fix applied in P1: `_LINE_NUMBERED_RE = re.compile(r"^\d+\t|^\s+\d+→")`. Pool re-run confirmed
  tab-format counts unchanged (18 sessions). SWE-chat CC re-run: 51 PATH-B fires on 1,053 sessions
  (4.84%).

**Non-CC generalization:**
- INCONCLUSIVE. conversations.parquet lacks tool_result rows for OpenCode (623 sessions) and
  Codex (213 sessions). Cannot test RFR without tool_result. Gemini CLI (11 sessions, 0/11) is
  too small to claim. Cross-agent validation remains open.

**Frozen file verification:**
- `waste_detectors.py`: UN-FROZEN as of P1. PATH-B `_LINE_NUMBERED_RE` updated to dual-format:
  `r"^\d+\t|^\s+\d+→"`. Pool re-run confirmed: 18 PATH-B sessions unchanged (tab format,
  byte-identical to B4). SWE-chat CC re-run: 51 PATH-B sessions fire on 1,053 sessions (4.84%).
- `claudecode_adapter.py`: byte-identical to B4 (unchanged)

---

## Iteration status: B2 CLOSED

The B2 iteration is complete. Quality-gated CC-native token baselines are built, validated,
and committed. The two-axis efficiency product (token economy + trajectory quality) is now
scoped with explicit domain boundaries.

Do NOT extend this iteration. Read NEXT_PHASE.md for candidate next directions.

---

## What B2 delivered

**Token measure (locked):**
`real_tokens = sum_ai_turns(token_count_input - cache_read + token_count_output)`
Excludes cache_read re-accumulation (which inflated total_tokens by 87–94% on CC sessions).
CC-caching-native only — non-caching agents (Kimi etc.) cannot use this baseline.

**Quality gate:** MUCH_BETTER only (strict). 75 local Claude CC sessions as the reference
corpus. Armand0e/Kimi excluded — no-caching token accounting is incommensurable.

**Task taxonomy (5 types, keyword classifier):**
ml-eval, debug-fix, infra-deploy, research-recon, feature-build (fallback).
Handles 10 `<local-command-caveat>` sessions. One documented session override.
Classifier: deterministic keyword matching, no LLM, consistency PASS.

**Per-type baselines (cc_baselines.json):**

| Type | n | p25 | median | p75 |
|---|---|---|---|---|
| infra-deploy | 20 | 386K | 698K | 1,003K |
| debug-fix | 19 | 353K | 524K | 654K |
| ml-eval | 12 | 458K | 646K | 1,034K |
| research-recon | 12 | 362K | 718K | 1,339K |
| feature-build | 12 | 424K | 711K | 803K |

**Scope gate (p10 turns per type):** ml-eval=127, debug-fix=59, infra-deploy=63,
research-recon=44, feature-build=166. Sessions below the floor → UNAVAILABLE (token axis);
trajectory verdict only. Rationale: p10 anchors to reference mass, not a single outlier.

**Circularity:** Spearman r=−0.0801, p=0.3418 (n=143). Token baseline and judge score
are independent axes.

**Two-axis output (efficiency_score.py):**
- Token economy: scope status + band verdict (above_p75 / within_band / below_p25 / unavailable)
- Trajectory quality: judge verdict + score + reasoning (populated when judge entry provided)
- No composite score — each axis labeled with its own domain of validity.

---

## Validated findings

**Two-axis orthogonality confirmed.**
- `6852df92` (MUCH_WORSE, within_band): trajectory scope violation, normal token cost. Judge
  catches what the token axis misses.
- `78bd2719` (WORSE, above_p75): 470 turns of repeated WSL2 failures. Both axes agree.
- `b9c6cbd4` (BETTER, above_p75): larger task done well. Token excess = task scope, not waste.
  Judge verdict needed to distinguish.

**Cache inflation was real.** Removing cache_read re-accumulation reduced per-session counts
by 87–94%. The ml-eval bimodality (CV=0.88 inflated → CV=0.51 corrected) dissolved completely.

**Circularity is not a concern.** Baseline tokens and judge scores are orthogonal (r=−0.08, n.s.).

**Armand0e gate rate (6% vs 69%) is earned, not bias.** Investigation confirmed identical
behavioral grounding (same waste categories, turn-specific citations, same bar applied to both
populations). The gap reflects population-level quality difference from expert prompting.

---

## Known limitations (all carry forward — do not paper over)

**Single-developer, expert-prompted corpus.** 97% of the baseline is one developer's
sessions, predominantly structured orchestrator workflows with explicit ROLE/STEP/CONSTRAINT
prompting. The baseline encodes "efficient under expert prompting." Cross-customer and
cross-prompting-style generalization is unvalidated.

**Token axis scope boundary.** The token verdict fires for 50% of the held-out sessions;
17/34 are UNAVAILABLE (below p10 scope gate). Coverage improves with more reference data
across scope ranges, not with wider quality bands. MUCH_BETTER+BETTER widening was
evaluated and rejected — it raises coverage from 26.5% to 44.1% but dilutes the quality
floor. See report 08.

**feature-build: zero held-out validation.** All 3 held-out feature-build sessions were
below the scope gate (12–35 turns vs gate of 166). The baseline exists but is unvalidated
on held-out data.

**No human gold.** Judge validated at rho=0.79 vs Sonnet reference LLM (instrument
coherence only, not accuracy). No "calibrated to human experts" claims permitted.

**CC-caching-native tokens.** Non-caching agents need their own baseline (launch-2 /
per-customer accrual).

**Score weights still provisional.** The B1 composite formula weights (0.50/0.35/0.15) are
untuned. The composite score is NOT run on CC sessions in B2 — token economy + trajectory
verdict are the two-axis product for launch-1.

---

## Repo structure (key paths, P2 state)

```
token-efficiency-scorer/
├── tes/                        SDK package (P1)
│   ├── __init__.py             Exports: ThreeAxisResult, score_session, load_baselines, JudgeConfig, domain-of-validity constants
│   ├── adapt.py                Thin wrapper → claudecode_adapter.adapt_session (frozen)
│   ├── classify.py             Thin wrapper → task_classifier.classify_session
│   ├── baselines.py            Thin wrapper → compute_real_tokens, load_baselines; BUNDLED_BASELINES_PATH
│   ├── waste.py                Thin wrapper → WasteEvent, detect_rfr, detect_rr
│   ├── judge.py                JudgeConfig + is_judge_available() + score_trajectory() (tiered; None=UNAVAILABLE)
│   ├── score.py                ThreeAxisResult dataclass + score_session() wrapper
│   ├── report.py               format_human() + format_json() (caveats inline, three labeled sections)
│   ├── store.py                SQLite persistence: sessions table + incremental ledger + WAL
│   ├── watcher.py              Scan loop: file-stability + incremental + judge-off guard
│   ├── web/
│   │   ├── server.py           Flask localhost-only dashboard (127.0.0.1 ONLY)
│   │   └── templates/          session_list, session_detail (caveats+TrajectoryRenderState), trends
│   ├── cli.py                  argparse CLI: tes score PATH + tes serve [opts]; tes score writes to store
│   ├── __main__.py             python -m tes entry point
│   └── data/cc_baselines.json  Bundled baseline artifact (locked)
├── research/
│   ├── 01-07-*.md              IMMUTABLE (B1 reports)
│   ├── 08-baselines.md         B2 final report — IMMUTABLE
│   ├── 09-cross-model.md       B3 final report — IMMUTABLE
│   ├── 10-deterministic-waste.md  B4 final report — IMMUTABLE
│   └── 11-generalization.md       B5 final report — IMMUTABLE
├── scripts/
│   ├── waste_detectors.py      Un-frozen P1: dual-format PATH-B (RFR + RR shipped)
│   ├── efficiency_score.py     Three-axis session scorer (token + judge + waste)
│   ├── task_classifier.py      5-type keyword classifier + selftest
│   ├── adapters/
│   │   └── claudecode_adapter.py  CC JSONL → digest schema (frozen)
│   ├── layer2_judge.py         Qwen3 judge (locked, GPU-required)
│   └── ...                     (other research scripts unchanged)
├── tests/
│   ├── test_behavior_preservation.py  20 golden-session regression tests (FROZEN fixture)
│   ├── test_judge_tiering.py          8 mock-based tiered-judge tests
│   ├── test_caveats_present.py        8 domain-of-validity output tests
│   ├── test_redaction_default_on.py   5 secret-redaction tests
│   ├── test_waste_detectors.py        99 detector unit tests (tab + arrow formats)
│   ├── test_store.py                  SQLite round-trip, ledger, merge semantics, WAL concurrent access
│   ├── test_watcher_behavior_preservation.py  Watcher-scored == manually-scored (same session)
│   ├── test_watcher_incremental.py    Unchanged not re-scored; failure isolation
│   ├── test_judge_off_in_background.py  Judge never called unless --background-judge
│   ├── test_localhost_bind.py         Dashboard binds 127.0.0.1, not 0.0.0.0
│   ├── test_dashboard_caveats.py      Domain-of-validity present in all dashboard views
│   └── fixtures/golden_scores.json    20-session golden fixture (FROZEN — do not modify)
├── data/
│   ├── cc_baselines.json       Per-type baselines + scope gates (locked)
│   ├── pool_judge_scores.jsonl 143 sessions scored (qwen3:30b-a3b)
│   ├── pool_waste_signals.jsonl  181 sessions × detector output (B4)
│   ├── corpus_pool/
│   │   └── pool_adapted.jsonl  181 adapted CC sessions
│   └── cost-log.jsonl          Append-only, ~$2.59 Anthropic cumulative
├── pyproject.toml              [project.scripts] tes = "tes.cli:main"; httpx dep; tes/ packages.find
├── README.md                   Domains of validity + corpus limitation + roadmap
└── CURRENT_STATE.md            This file
```

---

## What NOT to touch

- **research/01-11-*.md** — All immutable. Reports 01-10 inherited from B1–B4; report 11 is the
  B5 generalization validation final report. Do not edit any of these.
- **data/corpus_pool/** and **data/pool_judge_scores.jsonl** — Do not re-score or modify.
- **data/cc_baselines.json** — Locked for launch-1. Rebuild only for launch-2 with new data.
- **data/cost-log.jsonl** — Append-only. $5 cumulative Anthropic cap; currently ~$2.59.

---

## GCP infrastructure status

All B2 GPU VMs deleted after scoring:
- B2 pool scoring VM (asia-east1-a, g2-standard-8 SPOT): DELETED
- B2 step3 rescore VM: DELETED
- CC validation VM (report 07): DELETED (previously recorded)
- No persistent disks, snapshots, static IPs, or storage buckets remain

Estimated B2 GCP spend: ~$3.53 USD (pool scoring ~$2.80, rescore ~$0.36,
CC validation ~$0.37). GCP credits pool, not Anthropic cap.

---

## Judge configuration (locked — do not change)

- Model: qwen3:30b-a3b via Ollama ($0/session, GPU required)
- Prompt: v3 — trajectory purposefulness only, /no_think prefix
- Parameters: temp=0, seed=42, num_predict=6144, JSON schema
- GPU path: GCP g2-standard-8 SPOT, asia-east1-a
- DO NOT substitute Claude or any paid API as judge without escalation.
