# Project Spec: token-efficiency-scorer — Generalization Validation (Iteration B5)

## Goal

Test whether the frozen B4 deterministic waste detectors — and, if the data adapts well enough, the judge's B3-corroborated positive signal — GENERALIZE beyond the single 181-session CC pool, by running them READ-ONLY against decontaminated public coding-agent trajectory datasets. This directly attacks report 10 §9's stated limitation ("the boundary is derived from one 181-session CC pool; generalization unvalidated") and B2/B3's single-developer corpus limitation.

**This is VALIDATION, not improvement.** The detectors are deterministic rules and the judge is locked; nothing is trained or tuned. The deliverable is KNOWLEDGE — do the detectors fire cleanly on other developers'/agents' traces (→ stronger generalization claim) or messily (→ they're CC-specific, also a real finding)? Either outcome is a result to report honestly. The detectors and judge config DO NOT CHANGE based on what the public data shows.

**The discipline (non-negotiable):** B5 must NOT tune detectors to fit public data. Adding exclusions or loosening rules to make public-data numbers look better is the exact "move the opinion into the regex" failure the B4 credibility rule forbids. Frozen detectors in, honest measurement out. If they don't generalize, report that.

## Current state

See CURRENT_STATE.md. B1-B4 complete:
- Judge = Qwen3-30B-A3B, v3 prompt, LOCKED. Positive signal cross-model corroborated (84-96% gate overlap w/ Gemma, B3). Negative/waste signal model-dependent (B3) → moved to deterministic layer (B4).
- B4 shipped two deterministic detectors over the CC digest:
  - REPEATED-FAILED-RETRY: same shell tool, identical error snippet ≥2x consecutive, no intervening state change (Write/Edit/NotebookEdit, Bash state-mutation, or user turn). One principled exclusion (transient/CI-polling errors).
  - REDUNDANT-READ: PATH A (CC tool's own "File unchanged since last read" verdict) + PATH B (identical line-numbered content, gap ≤5, no Write/Edit/user between).
  - DEAD-END + EMPTY-TURN: documented exploratory, NOT shipped.
- Detectors read the CC JSONL digest: per-turn role, tool_names, content_snippet (300-char), token_count_input/output, cache_read.
- Reports 01-10 immutable. No human labels. No inference in the waste layer. GPU path exists but B5 is largely local/$0 (judge run on public data, if reached, needs GPU).

## The core risk this phase must surface FIRST (feasibility gate)

The detectors and judge read the CC digest schema. Public trajectory datasets (SWE-rebench, AgentRx, SWE-smith, tau-bench, etc.) are in THEIR OWN formats. B5's viability hinges on whether public data can be adapted to the digest LOSSLESSLY ENOUGH for the detectors to run VALIDLY. Specifically the detectors depend on:
- Distinguishable tool RESULTS (the error output that REPEATED-FAILED-RETRY matches on)
- Tool TYPE per turn (Bash/PowerShell vs Read vs Write/Edit)
- Turn ORDERING and the ability to detect state-changing ops between turns
- Read RESULTS / file content (REDUNDANT-READ PATH B)
- The CC-specific "File unchanged since last read" signal (PATH A — almost certainly CC-only; expect PATH A to be unavailable on public data)

If a public format lacks tool results, or can't distinguish edit-from-read, the detectors CANNOT run validly on it (or run degraded). Step 1 is a feasibility investigation; if adaptation is too lossy, B5 narrows or stops — better to learn that in a short survey than after building a translator.

## Scope

### In scope
1. FEASIBILITY SURVEY (gate): identify accessible, DECONTAMINATED public coding-agent trajectory datasets. For each: format, whether it captures tool results / tool types / turn order / file content, and whether it maps to the digest schema cleanly enough for each detector to run validly. Report adaptability per dataset per detector. HOLD.
2. ADAPTER: if ≥1 dataset is viable, write a translator from its format to the digest schema. Document exactly what maps, what's lost, and which detectors can run validly on it. Lossy fields → mark the dependent detector UNAVAILABLE on that dataset, don't fake it.
3. DETECTOR GENERALIZATION RUN (read-only): run the FROZEN detectors over the adapted public traces. Report per-detector fire rates + evidence samples. Compare to CC-pool fire rates. Do the detectors fire on the same KINDS of events? Any spurious fires that reveal a CC-specific assumption baked into a rule?
4. JUDGE GENERALIZATION (only if the data adapts well enough for the judge's digest_text input): run the locked Qwen judge on a sample of public sessions; does the positive signal behave sensibly on non-CC, non-single-developer traces? This is exploratory and data-permitting.
5. DECONTAMINATION CHECK: confirm the public sources are decontaminated (SWE-rebench is built for this). Validating a Qwen-based judge against trajectories Qwen's base model trained on would be circular. Document the decontamination basis for any dataset used for the JUDGE run especially.
6. research/11-generalization.md: honest report — what generalized, what didn't, what's CC-specific, and the updated generalization claim for report 10's §9 limitation (as a NEW report; 10 stays immutable, 11 supersedes its §9 generalization note).

### Out of scope
- Tuning/modifying detectors to fit public data (the whole discipline).
- Retraining or reconfiguring the judge.
- Human labels.
- Using public data as a TOKEN BASELINE (B2 proved cross-scaffold token accounting is incommensurable — public non-CC-caching traces CANNOT enter the token-economy baseline; this phase does NOT touch the token axis).
- Packaging (next phase).
- Modifying reports 01-10.

## Tech stack
- Python, repo conventions. Adapter + detector runs are local/$0.
- Judge generalization run (if reached) needs GPU (same Qwen/Ollama/L4 path as B1-B3) — escalate before provisioning.
- Public datasets: prefer HuggingFace / official repo releases; verify license permits this use.
- pytest for the adapter (golden-file tests: known public trace → expected digest).

## Architecture (new files only)
```
scripts/
├── public_data_survey.py       # NEW - catalog accessible datasets + format probe
├── public_trace_adapter.py     # NEW - public format -> CC digest schema, lossy-field flags
└── generalization_run.py        # NEW - frozen detectors over adapted traces + compare

tests/
└── test_public_adapter.py      # NEW - golden-file adapter tests

data/
├── public_traces_adapted.jsonl # NEW - adapted public traces (decontaminated sources only)
├── public_waste_signals.jsonl  # NEW - detector outputs on public data + evidence
└── generalization_compare.json # NEW - public vs CC-pool fire-rate comparison

research/
└── 11-generalization.md        # NEW (supersedes report 10 §9 generalization note)
```

## Key design decisions (resolve early, escalate)
1. DATASET SELECTION: which public datasets, decontaminated, license-clear, format-adaptable. Candidates to assess (verify current availability/access — do not assume): SWE-rebench (built decontaminated), AgentRx failure-trajectory corpus, SWE-smith generated trajectories, tau-bench, SWE-chat ("coding agent interactions from real users in the wild"). Report which are actually accessible + adaptable. HOLD before committing to one.
2. ADAPTER LOSSINESS THRESHOLD: how much field-loss makes a detector INVALID vs degraded-but-usable on a dataset. Define per detector. PATH A redundant-read is almost certainly CC-only (the "File unchanged" signal) → expect UNAVAILABLE on public data; PATH B may port if file content is captured. Be explicit about what each detector needs and whether the dataset provides it.
3. "GENERALIZES" CRITERION: define what success looks like BEFORE the run, to avoid post-hoc rationalization. E.g.: detector fires on structurally-equivalent events in public traces (same error + no change), at a non-trivial rate, WITHOUT spurious fires that expose a CC-specific assumption. State the criterion, then measure against it.
4. JUDGE RUN GO/NO-GO: only if the adapter produces digest_text good enough that the judge is reading a fair representation of the public session (not a degraded artifact). If adaptation is lossy, the judge run is NOT valid — skip it and say so.

## Verification commands
```yaml
- name: adapter-golden-tests
  cmd: python -m pytest tests/test_public_adapter.py -v
  required: true
- name: detectors-unchanged
  cmd: git diff --exit-code scripts/waste_detectors.py && echo "detectors frozen - unchanged since B4"
  required: true
- name: evidence-integrity-public
  cmd: python -c "import json; [exit('no evidence') for l in open('data/public_waste_signals.jsonl') if (r:=json.loads(l)).get('waste_events') and not all('turns' in e for e in r['waste_events'])]; print('all evidenced')"
  required: true
- name: decontamination-documented
  cmd: test -f data/public_traces_adapted.jsonl && grep -q 'decontamination' research/11-generalization.md && echo "decontamination basis documented"
  required: true
```

## Escalation rules
- After the feasibility survey: report dataset adaptability per detector, HOLD before building the adapter. If nothing adapts cleanly, B5 narrows to "we surveyed, here's why public data can't validate these detectors" — itself a documented finding.
- Before the JUDGE generalization run (needs GPU): confirm the adapter produces a fair digest_text representation + decontamination basis, report cost, HOLD before provisioning.
- If a detector fires SPURIOUSLY on public data (exposing a CC-specific assumption): do NOT patch the detector. Report it as a generalization finding. The detector stays frozen.
- Before modifying any report 01-10 (immutable — 11 is new).

## Hard rules
- DETECTORS FROZEN: waste_detectors.py is unchanged from B4 (verification command enforces). B5 measures, it does not modify.
- Read-only validation: public data is input to frozen rules, never a reason to change the rules.
- Decontaminated sources only, especially for the judge run (circularity risk).
- Token axis untouched: public non-CC traces CANNOT enter the token baseline (B2 incommensurability). B5 does not touch B2.
- Lossy adaptation → detector UNAVAILABLE on that dataset, never faked.
- No human labels. No inference in the detector layer (judge run is separate + escalated). Reports 01-10 immutable.
- License check on every public dataset before use.

## Budget
- Soft: 1-3 CC sessions. Survey + adapter + detector runs local/$0.
- Anthropic API: unchanged (~$2.59).
- GCP: only if the judge generalization run is reached (escalate first); estimate ~$1-2 same as B3 if so.

## Success criteria (verify ALL before done)
- Feasibility survey complete: accessible decontaminated datasets cataloged, adaptability per detector reported.
- If viable: adapter built, golden-tested, lossy fields documented; detectors run read-only on adapted public traces.
- Per-detector public fire rates + evidence + comparison to CC-pool rates reported against the pre-stated "generalizes" criterion.
- Any spurious fires reported as findings (detector NOT patched).
- Judge generalization run done OR explicitly skipped-with-reason (adaptation too lossy / decontamination unmet).
- Decontamination basis documented for all sources used.
- research/11-generalization.md: honest verdict — generalizes / CC-specific / mixed, per detector; updated generalization claim superseding report 10 §9.
- waste_detectors.py byte-identical to B4 (verification passes). Reports 01-10 untouched. Git clean. License compliance noted.

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md + reports 09/10 + spec.md. Internalize: validation not improvement, frozen detectors, feasibility gate first.
2. FEASIBILITY SURVEY: catalog accessible decontaminated public trajectory datasets; probe each format for tool-results/tool-types/turn-order/file-content; report adaptability per detector. HOLD.
3. On approval: build the adapter for the most viable dataset; golden-file tests; document lossy fields + which detectors can validly run. HOLD.
4. Run frozen detectors read-only on adapted traces; report fire rates + evidence + CC-pool comparison vs the pre-stated criterion. HOLD.
5. Judge generalization go/no-go (decision 4); if go, escalate for GPU, run, report. If no-go, document why.
6. Write research/11-generalization.md with the honest per-detector verdict + updated generalization claim. HOLD for consultant read before calling B5 done.
