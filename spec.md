# Project Spec: tracegauge — Corpus Contribution, Client-Side & Send-Disabled (Iteration P7)

## Goal

Build the CLIENT side of opt-in corpus contribution: the mechanism that lets a user EXPORT a redacted, content-free, allow-listed digest of their sessions — to a LOCAL FILE they can inspect — so that (in a LATER, separate phase) a shared corpus could be built to give new users a meaningful day-one baseline instead of cold-start limbo.

THIS PHASE BUILDS: the payload definition, the allow-list redaction, the consent/preview flow, and a `tracegauge export-contribution` command that writes the payload to a local file the user opens and verifies.

THIS PHASE DOES NOT BUILD: any server, any network transmission, any backend, any actual data collection. NOTHING leaves the machine in P7. The payload goes to a local file, full stop. Transmission + server + the legal surface (privacy policy, retention, GDPR) are a SEPARATE later decision, made only after the payload is proven trustworthy and lawyer-reviewed.

## The core tension this phase must respect (read first — non-negotiable)

Every prior phase was LOCAL BY CONSTRUCTION. The moat — "your data never leaves your machine" — is enforced, tested, and printed on the PyPI landing page. Corpus contribution, by its nature, is about data EVENTUALLY leaving the machine. So:

1. **The default install stays 100% local. The moat promise stays TRUE for the default.** Contribution is opt-in, explicit, off by default, and in P7 doesn't transmit at all (writes a local file).
2. **The README's moat language must be UPDATED HONESTLY** to: "local by default; you can OPTIONALLY export a redacted, content-free contribution file — here is exactly what it contains — for a future shared-baseline program. Nothing is transmitted without your explicit action, and in this version nothing is transmitted at all." No bait-and-switch. The promise changes from "never leaves" to "never leaves unless YOU explicitly export it, and you can see exactly what that export contains."
3. **What CAN be exported must be PROVABLY content-free** — allow-listed numeric/categorical fields only, NEVER code/prompts/paths/content. Provable by construction (allow-list), not by scrubbing.

## The payload design (locked: option a — per-session rows, strictly allow-listed)

The contribution payload is a set of per-session rows, each containing ONLY these explicitly enumerated fields and NOTHING else:
- `task_type` (categorical: one of the 5 known types + fallback)
- `real_tokens` (int)
- `token_count_input`, `token_count_output`, `cache_creation`, `cache_read` (ints — the four cost classes, AGGREGATED to session level, not per-turn)
- `waste_event_count` (int)
- `waste_detectors_fired` (list of detector NAMES only — e.g. ["REPEATED-FAILED-RETRY"] — NOT the proof turns, NOT the evidence snippets, which contain content)
- `model` (categorical model string, e.g. "claude-sonnet-4-6" — allow-listed against the known model table; unknown → "other")
- `turn_count` (int)
- `week_bucket` (source_mtime rounded to ISO week — NOT a precise timestamp, to avoid timing fingerprints)
- `tracegauge_version`, `schema_version` (provenance)
- `contributor_id` (a RANDOM opaque UUID generated per-install, NOT derived from anything identifying — lets the future corpus dedupe/weight without knowing who the user is; user can regenerate or omit)

EXPLICITLY EXCLUDED (must never appear in the payload): session_id (could correlate), source_path (file paths = content + identity), any evidence/snippet/error text, any prompt or code, proof-turn CONTENT (counts ok, content no), precise timestamps, any free-text field, the judge reasoning, interpretation strings.

The allow-list is the SAFETY MECHANISM: the export builds each row by EXPLICITLY copying ONLY the enumerated fields from a known source — it must NOT serialize a session object and "remove" sensitive fields (that's the unsafe pattern — a new field added later would leak by default). Build the payload field-by-field from the allow-list; anything not on the list cannot appear by construction.

## Scope

### In scope
1. **Payload builder** (`tes/contribution.py`): from the store, build per-session allow-listed rows per the locked field list. Field-by-field construction from the allow-list (NOT object-minus-fields). Returns a payload object + a manifest (what fields are included, the schema version, the row count).
2. **`tracegauge export-contribution` CLI command**: writes the payload to a local file (e.g. `~/.tes/contribution-<date>.jsonl` or a user-specified path). Off by default — only runs when explicitly invoked. Prints a CONSENT/PREVIEW summary BEFORE writing: "This will write N rows containing ONLY these fields: [list]. NO code, prompts, paths, timestamps, or session content. NOTHING is transmitted — this writes a local file you can inspect. Continue? [y/N]" Requires explicit confirmation.
3. **The preview/inspect affordance**: the command (or a `--preview` flag) shows a SAMPLE row (real data, so the user sees exactly what their contribution looks like) and the full field list, before any file is written. The user can open the resulting file and read it — it's human-readable JSONL.
4. **Content-free verification test**: a test that takes a payload built from sessions with KNOWN sensitive content (secrets, file paths, prompts in the source) and asserts NONE of it appears in the payload — proving the allow-list works. Plus a test that asserts the payload contains ONLY the enumerated fields (any extra key = test failure).
5. **README + docs update**: honest moat language (local by default; optional inspectable export; nothing transmitted in this version), and a CONTRIBUTING/PRIVACY note describing exactly what the payload contains and excludes.
6. **Schema versioning** on the payload (so a future corpus can handle version evolution).

### Out of scope (explicitly, for a LATER separate decision)
- ANY network transmission / upload / server connection. NOTHING leaves the machine in P7.
- The corpus SERVER (receive/validate/aggregate/redistribute) — a separate backend project with its own privacy/legal/security surface.
- The legal surface: privacy policy, data retention, GDPR/CCPA, terms — required BEFORE any transmission, not in P7.
- The pooled-baseline computation / validation (how to build a trustworthy day-one baseline from pooled rows — that needs the corpus to exist + B2-level validation; later).
- Actually distributing pooled baselines back to users.
- Changing detectors, judge, self-baseline math, cost model.
- Modifying reports 01-11.

### Hard rules
- NOTHING IS TRANSMITTED in P7. The export writes a LOCAL FILE only. No network code, no server URL, no upload. (A test asserts no network egress in the contribution path — same discipline as the moat tests.)
- ALLOW-LIST BY CONSTRUCTION: the payload is built by copying ONLY enumerated fields. Never serialize-then-remove. A test asserts the payload has ONLY the allow-listed keys.
- CONTENT-FREE PROVEN: a test with known-sensitive source asserts zero leakage.
- OPT-IN, EXPLICIT, OFF BY DEFAULT: export only on explicit command + confirmation. Default install transmits/exports nothing.
- MOAT LANGUAGE UPDATED HONESTLY: README reflects "local by default; optional inspectable export; nothing transmitted in this version." No bait-and-switch.
- Detectors frozen, reports 01-11 immutable, no human labels.

## Tech stack
- Python, reuse `tes/`. Payload built from the SQLite store (the allow-listed fields are already columns/derivable).
- Output: human-readable JSONL the user can open.
- pytest: content-free proof, allow-list-only proof, no-network proof, consent-gate behavior, schema version present.

## Architecture (new/changed)
```
tes/
├── contribution.py     # NEW: allow-listed payload builder + manifest; field-by-field from allow-list
├── cli.py              # CHANGED: add `tracegauge export-contribution` (consent/preview, writes local file)
└── (no server, no network module — by design)

tests/
├── test_contribution_content_free.py  # NEW: known-sensitive source -> zero leakage in payload
├── test_contribution_allowlist.py     # NEW: payload has ONLY enumerated keys; extra key = fail
├── test_contribution_no_network.py     # NEW: no network egress in the contribution path
└── test_contribution_consent.py        # NEW: export requires explicit confirmation; off by default

README.md / PRIVACY.md  # CHANGED/NEW: honest moat language + exact payload contents/exclusions
```

## Key design decisions (resolve early, escalate)
1. **contributor_id**: random per-install UUID (for future dedupe/weighting) vs omit entirely. Recommend: random opaque UUID, stored in ~/.tes/, REGENERATABLE, with an `--anonymous` flag to omit it. It carries no identity (random), only lets a future corpus avoid double-counting one user. Confirm it's not derivable from anything identifying (not hostname, not username, not path).
2. **week_bucket granularity**: ISO week vs month. Recommend ISO week (enough for future cohort analysis, coarse enough to avoid precise-timing fingerprints). Confirm no precise timestamp leaks.
3. **Preview UX**: show one real sample row + full field list + the explicit exclusions, then confirm. Decide the exact copy — it must make the user CONFIDENT they know what's in it.
4. **Model field for unknown/empty**: map to "other" (don't leak an unrecognized raw string that could theoretically be a custom/identifying model name). Allow-list against the known model table.
5. **Where the file lands**: default ~/.tes/contribution-<date>.jsonl + a --output path. The user owns the file; tracegauge never auto-sends it.

## Verification commands
```yaml
- name: content-free
  cmd: python -m pytest tests/test_contribution_content_free.py -v   # known secrets/paths/prompts -> zero leakage
  required: true
- name: allowlist-only
  cmd: python -m pytest tests/test_contribution_allowlist.py -v       # ONLY enumerated keys present
  required: true
- name: no-network
  cmd: python -m pytest tests/test_contribution_no_network.py -v      # no egress in contribution path
  required: true
- name: consent-gate
  cmd: python -m pytest tests/test_contribution_consent.py -v         # explicit confirm required, off by default
  required: true
- name: detectors-frozen
  cmd: git diff --exit-code tes/_waste_detectors.py && echo frozen
  required: true
- name: full-suite
  cmd: python -m pytest -q
  required: true
```

## Escalation rules
- If building the payload tempts ANY network code, a server URL, or a transmission path: STOP — that is explicitly out of scope for P7, escalate.
- If the allow-list approach would miss a field the future corpus "needs": note it for the later corpus-design phase; do NOT add content-bearing fields to make the corpus richer — content-free is the hard constraint.
- BEFORE updating the README moat language: show the consultant the exact new wording — the honesty of the public promise is load-bearing.
- Detectors/judge/self-baseline/cost frozen.

## Budget
- Soft: 2-3 CC sessions. Local/$0. No GCP, no API, NO server, no network.

## Success criteria (verify ALL before done)
- `tracegauge export-contribution` builds a per-session allow-listed payload, writes a local human-readable JSONL file, after an explicit consent/preview the user confirms.
- Content-free test passes: known secrets/paths/prompts/snippets in source -> ZERO leakage in payload.
- Allow-list test passes: payload contains ONLY the enumerated fields; any extra key fails.
- No-network test passes: nothing in the contribution path transmits; it writes a local file only.
- Consent-gate test passes: export is off by default, requires explicit confirmation, shows the preview + exclusions first.
- contributor_id is a random opaque per-install UUID (not derivable from identity), regeneratable, omittable via --anonymous.
- README/PRIVACY updated with honest moat language (local by default; optional inspectable export; nothing transmitted this version) + exact payload contents/exclusions — consultant-reviewed wording.
- Payload has schema_version. Detectors frozen. Full suite green. Reports 01-11 untouched. Git clean.
- NO server, NO network, NO transmission exists in the codebase.

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md + spec.md + tes/store.py + tes/cost.py + the existing moat/redaction tests. Internalize: nothing-transmitted-in-P7, allow-list-by-construction, opt-in-explicit, honest-moat-update.
2. Build contribution.py: the allow-listed payload builder (field-by-field from the enumerated list, NOT object-minus-fields) + manifest. HOLD for consultant read of the EXACT field list + the build approach.
3. Content-free + allow-list-only + no-network tests (these are the safety gates — write them strong). HOLD for consultant read of the test that proves zero leakage.
4. CLI `export-contribution`: consent/preview (sample row + field list + exclusions), explicit confirm, write local file. Consent-gate test.
5. README/PRIVACY honest moat update. HOLD for consultant review of the exact public wording.
6. Full suite green; confirm NO network/server anywhere. Show the consultant: a real exported sample row + the rendered consent/preview + the new README moat paragraph. HOLD before P7 done.
