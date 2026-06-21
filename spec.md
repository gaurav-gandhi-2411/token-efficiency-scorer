# Project Spec: tracegauge — Community Corpus (Iteration 0.9.0)

## Goal

Turn tracegauge's single-developer baseline into a real CROSS-DEVELOPER baseline by letting developers OPT IN to contribute content-free session aggregates to a shared corpus, and get back community percentile baselines ("your context efficiency is in the 60th percentile for infra-deploy across N developers"). This is the multi-developer niche move (buyer class b: community-benchmarking individuals), built at $0 on Supabase free tier, on top of the ALREADY-BUILT content-free export (P7).

It simultaneously: (1) fixes the product's deepest limitation (single-developer calibration), (2) unlocks the research (multi-developer data), (3) builds the strategic moat (coding-session corpus the production-observability incumbents structurally don't have).

## THE CENTRAL RISK — this is the first time data leaves the machine

Every prior version was local-by-default, moat-by-construction, zero egress. THIS version transmits — for the first time. That is the most consequential change in the project's history. A privacy mistake here is not a patchable bug; it is a breach of the trust the product is built on. So:

**Transmission is the escalation-heavy, test-heavy core of this phase, not a detail.** Hard requirements, non-negotiable:

1. **OPT-IN, never default.** Default install transmits NOTHING (unchanged from today). Contribution requires an explicit, informed opt-in action by the user. No silent enrollment, no opt-out-buried-in-settings. The user must DO something deliberate.
2. **CONTENT-FREE, proven at the moment of transmission.** Reuse the P7 content-free export (allow-listed field-by-field construction, byte-grep tests proving no planted secret survives). Before ANY network send, the EXACT payload is shown to the user (the real row, P7's preview) AND re-verified content-free by the byte-grep guard AT SEND TIME — not just at export time. Transmission of anything not in the allow-list is impossible by construction + tested.
3. **CONSENT shows what's sent AND where it goes.** The consent screen states: the exact content-free fields, that it's transmitted to the tracegauge community corpus (Supabase), what it's used for (pooled baselines), that NO code/prompts/paths/content are included, and how to withdraw. Mirror P7's consent honesty, extended to "this now actually transmits to <destination>."
4. **WITHDRAWAL / DELETE.** The user can delete their contributed data (a delete path keyed on their random contributor_id). A privacy policy that promises deletion must deliver it.
5. **NO IDENTITY.** contributor_id is the existing random per-install UUID (not derived from anything identifying). No email, no account, no auth in Phase 1 (that's Phase 2 / team product). Anonymous content-free aggregates only.
6. **PRIVACY POLICY** written plainly: what's collected (the 14 content-free fields), where stored (Supabase region), retention, how to delete, that it's anonymous + content-free. This is the "lawyer eventually" item; for content-free anonymous numeric aggregates at dev-stage a clear honest plain-language policy is the reasonable starting point — but it must be HONEST and COMPLETE about exactly what the data is.

If any design path could transmit content, transmit without consent, enroll by default, or make withdrawal impossible: STOP, escalate. This is the one boundary that does not bend.

## $0 architecture (Supabase free tier)
- **Client (tracegauge, local):** computes the content-free aggregate (P7 export, already built), shows the user the exact payload + consent, and ONLY on explicit opt-in POSTs it to the corpus endpoint. The contribution is a one-shot content-free row per session (or a batch), keyed on the random contributor_id.
- **Store (Supabase free tier):** a single table of content-free rows. Aggregates are tiny (numbers + categoricals + week-bucket + UUID), so the free tier (500MB) holds an enormous number of rows — never near the limit at this scale. RLS configured so a contributor can only write their own rows + delete their own rows (keyed on contributor_id); reads for baseline computation are aggregate-only.
- **Baseline computation:** periodically (or on demand), compute cross-developer percentile baselines per task_type from the pooled content-free rows, and publish them as a DATA FILE the client fetches (exactly like cc_baselines.json ships today). The client then scores the user against the COMMUNITY baseline as an option alongside their self-baseline.
- **$0 confirmed:** Supabase free tier (Postgres, 500MB, generous row limits) + content-free tiny rows + batch baseline computation = no per-user server, no egress bills, no scaling cost at dev/early scale. When it grows enough to matter, THAT's the invest-later moment.

## What the user GETS (the value)
- **Community percentile baseline:** "your context-resend efficiency for infra-deploy is in the Nth percentile across M contributing developers" — alongside (not replacing) the self-baseline. Honest framing: it's a community comparison, with its own domain-of-validity (who contributed, N, the self-selection caveat).
- The self-baseline stays the primary, honest, no-network default. The community baseline is an OPT-IN enhancement.

## Current state
tracegauge 0.8.0 LIVE — local-by-default, content-free export built (P7, send-disabled), self-baseline, attribution, Session Intelligence, dashboard. The corpus TRANSMISSION was always the deferred, walled-off, "needs consent + lawyer" boundary. This phase crosses it — carefully — for content-free anonymous aggregates only.

## Scope
### In scope
1. **Contribution transmission (opt-in):** extend the P7 export to actually POST (on explicit consent) to the Supabase corpus endpoint. The send-time content-free re-verification. The exact-payload preview + consent + destination. Withdrawal/delete path.
2. **Supabase corpus:** the content-free table, RLS (write/delete own rows, aggregate reads), the schema = the P7 14 fields. Setup documented + reproducible.
3. **Baseline computation + redistribution:** compute community percentile baselines per task_type from the pooled rows; publish as a fetchable data file; client scores against community baseline as an OPT-IN comparison alongside self-baseline.
4. **Privacy policy + consent UX:** the plain-language PRIVACY policy (what/where/retention/delete/anonymous/content-free); the consent screen (fields + destination + use + withdrawal).
5. **Honest framing of the community baseline:** its DOV (N contributors, self-selection bias, content-free-so-coarse), shown wherever the community percentile appears. Never overstated.
6. Tests: content-free-at-send-time (byte-grep the actual POST payload), opt-in-required (no send without explicit consent), withdrawal-works, RLS-enforces-own-rows-only, community-baseline-carries-DOV, default-install-still-transmits-nothing.

### Out of scope (Phase 2 / later / invest-later)
- Team accounts, auth, per-developer identity, team dashboards (buyer a — the invest-later product).
- Any NON-content-free transmission (no code, prompts, paths, content — ever).
- General token observability (routing/caching/production attribution — the THIRD horizon).
- Live/real-time backend (batch baseline computation is fine + free).
- Reports 01-11, detectors, the scoring engine — unchanged.

### Hard rules
- TRANSMISSION IS OPT-IN + CONTENT-FREE + CONSENTED + WITHDRAWABLE + ANONYMOUS. The default install transmits nothing (tested). Content-free re-verified at SEND time (tested on the actual payload). No identity. These do not bend.
- Self-baseline + local scoring UNCHANGED — community baseline is an additive opt-in, never replaces the honest local default.
- Engine/detectors/reports frozen (presentation + a new opt-in data path; no scoring-math change). git diff _waste_detectors.py empty.
- Community baseline carries its DOV (N, self-selection, content-free-coarseness) wherever shown.
- $0: Supabase free tier, content-free tiny rows, batch computation. No design that incurs cost at dev scale.

## Tech stack
- Client: reuse tes/contribution.py (P7 content-free builder), add an opt-in POST (httpx, already a dep) with send-time content-free re-verification. Consent/preview reuse P7's preview.
- Store: Supabase (Postgres + RLS). Content-free table. Setup script/doc (reproducible, like the cc_baselines provenance).
- Baseline computation: a script that reads pooled rows, computes per-task percentiles, emits the community baseline data file. Runs free (locally or a free scheduled job).
- pytest: the transmission guards (content-free-at-send, opt-in, withdrawal, RLS, DOV, default-silent).

## Architecture
```
tes/
├── contribution.py     # P7 content-free builder (REUSE) + opt-in POST + send-time re-verify
├── corpus_client.py    # NEW: the consented POST, the withdrawal/delete, fetch community baseline
├── community_baseline.py # NEW: score against community percentile (opt-in, alongside self)
├── cli.py / web/       # opt-in contribution flow + consent + the community-percentile display (DOV-carried)
corpus/                 # NEW: Supabase schema + RLS + the baseline-computation script + setup doc
PRIVACY.md              # UPDATED: the transmission section — what/where/retention/delete/anonymous
tests/
├── test_send_content_free.py    # byte-grep the ACTUAL POST payload — no planted secret survives
├── test_transmit_optin.py       # no send without explicit consent; default install transmits nothing
├── test_withdrawal.py           # delete path removes the contributor's rows
├── test_corpus_rls.py           # contributor writes/deletes only own rows; reads aggregate-only
└── test_community_baseline_dov.py # community percentile always carries its DOV
```

## Verification commands
```yaml
- name: send-content-free
  cmd: python -m pytest tests/test_send_content_free.py -v   # the ACTUAL payload is content-free (byte-grep)
  required: true
- name: transmit-optin
  cmd: python -m pytest tests/test_transmit_optin.py -v       # opt-in required; default transmits nothing
  required: true
- name: withdrawal
  cmd: python -m pytest tests/test_withdrawal.py -v
  required: true
- name: detectors-frozen
  cmd: git diff --exit-code tes/_waste_detectors.py && echo frozen
  required: true
- name: import-closure
  cmd: python -m pytest tests/test_all_tes_imports_are_declared.py -v   # any new dep (supabase client?) declared
  required: true
- name: full-suite
  cmd: python -m pytest -q
  required: true
```

## Escalation rules (autonomous mode — escalate ONLY these)
- PUBLISHING 0.9.0 to PyPI (irreversible; user's token).
- ANYTHING touching the transmission boundary: the consent wording, the content-free-at-send guarantee, the default-silent guarantee, the withdrawal path, the privacy policy text — escalate ALL of these for consultant review BEFORE they ship. This is the one boundary where autonomy is suspended: the transmission design + consent + privacy text get human review, every time.
- If any path could transmit content / transmit without consent / enroll by default / make withdrawal impossible — STOP, escalate.
- A new dependency (the Supabase client lib?) — declare + import-closure; escalate the choice.
- Touching frozen detectors/engine/reports — out of scope, escalate.
- Otherwise (the baseline math, the percentile display UX, the Supabase table mechanics): decide and act, report.

## Budget
- $0. Supabase free tier. Content-free tiny rows. Batch (not live) baseline computation. No paid infra. If any step would incur cost, STOP and escalate (the $0 constraint is firm at this stage).

## Success criteria (verify ALL)
- Default install transmits NOTHING (tested). Contribution is explicit opt-in only.
- The transmitted payload is content-free, RE-VERIFIED at send time on the actual payload (byte-grep, tested). Consent shows the exact fields + destination + use + withdrawal. The user sees their real row before sending.
- Withdrawal/delete works (the contributor's rows removed, tested). contributor_id anonymous (random UUID, no identity).
- Supabase corpus: content-free table, RLS (own-rows write/delete, aggregate reads), $0 free tier, documented + reproducible.
- Community percentile baseline computed + redistributed as a data file; client scores against it as an OPT-IN comparison ALONGSIDE the unchanged self-baseline; the community baseline carries its DOV (N, self-selection, coarseness) wherever shown.
- PRIVACY.md honestly + completely describes the transmission (what/where/retention/delete/anonymous/content-free).
- Self-baseline + local default UNCHANGED; engine/detectors/reports frozen; import-closure green (new deps declared); full suite green.
- 0.9.0 built, clean-roomed (--no-default-packages: opt-in contribution + community baseline work from the installed wheel; default still transmits nothing), PUBLISHED, fresh-install confirmed.

## Build order (orchestrator decides reversible details; transmission boundary is escalation-gated)
1. Read CURRENT_STATE.md + spec.md + tes/contribution.py (the P7 content-free builder + its tests) + the P7 PRIVACY section. Confirm context + the transmission-is-the-central-risk boundary in 5-7 lines.
2. DESIGN the transmission: the consent screen wording, the send-time content-free re-verification mechanism, the withdrawal path, the PRIVACY policy text, and the contributor_id anonymity. HOLD — escalate ALL of this for consultant review BEFORE building. (Autonomy suspended on the transmission boundary — the consent + privacy + content-free-at-send design gets reviewed before code.)
3. Build the Supabase corpus (schema + RLS + setup doc) + corpus_client.py (consented POST + withdrawal) + the send-content-free / opt-in / withdrawal / RLS tests. HOLD — show consultant the RLS proof + the byte-grep-the-actual-payload test result + a real (test-account) round-trip (contribute → appears as content-free row → withdraw → gone).
4. Build community_baseline.py (compute percentiles, redistribute, score-against-as-opt-in) + the DOV-carried display. Tests.
5. PRIVACY.md update + consent UX. HOLD — consultant reviews the final privacy + consent text (the public promise about transmission).
6. Full suite + import-closure + detectors frozen + clean-room (--no-default-packages: opt-in contribution + community baseline from the wheel; default transmits nothing). Bump 0.8.0 -> 0.9.0, CHANGELOG. ESCALATE the publish.
