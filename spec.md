# Project Spec: token-efficiency-scorer — Phase A.1 finish + A.2 rebuild

## Goal

Complete the in-flight Phase A.1 validation work (IAA, F1 remeasurement, report 04), then execute Phase A.2 (rebuild heuristics that fail their verdicts) in the same iteration. Heuristics that pass are locked. Heuristics that fail are either redesigned (semantic backtrack via BGE embeddings is the likely candidate) or formally deprecated to a v2 backlog. Production scorer code is NOT in scope; this is still validation.

## Current state

See `CURRENT_STATE.md` in full. Key points reiterated here for the orchestrator:

- A long-running annotation job MAY still be in progress at handoff. Verify state before any action; do not restart unilaterally.
- Model allocation is LOCKED: GPT-OSS 120B (Groq) bulk, Sonnet 4.6 (Anthropic) IAA only, local BGE-large for embeddings.
- Research reports 01, 02, 03 are IMMUTABLE. Report 04 is stubbed and this iteration fills it in. Report 05 is new for A.2.
- Path A is committed. Do not pivot to Path B.
- Wedge = per-task trajectory counterfactual; do not substitute.
- Cumulative API cost cap for the whole project is $5. Check `data/cost-log.jsonl` before any API call.

## Scope

### In scope (this iteration)

- Verify (and if needed, gracefully complete) the in-flight A.1 annotation job
- Run IAA mode: Sonnet 4.6 on the 25-session fixed-seed sample
- Compute Cohen's kappa per label (H1, H2, H3, H4)
- Run `10_remeasure_heuristics.py` against the GPT-OSS labels
- Fill stubbed sections of `research/04-phaseA1-remeasure.md`
- Escalate per-heuristic verdicts to user before treating as final
- After user confirms verdicts: execute A.2 work
  - DEAD heuristics: implement redesign OR deprecate to v2 backlog per user direction
  - SALVAGEABLE heuristics: apply proposed fix from report 04
  - PRODUCTION_READY heuristics: lock and document
- Most likely A.2 task: semantic H3 backtrack via local BGE-large embeddings + similarity threshold tuning, validated against the existing corpus
- Write `research/05-phaseA2-rebuild.md` documenting redesigns and their validation F1

### Out of scope (do not build)

- Corpus expansion (real Claude Code, Aider non-Python, multi-language) — Phase A.3, explicitly deferred
- Production scorer code
- Path B pivot (LLM-judge-primary)
- New SWE-bench downloads or dataset additions
- Modifying research reports 01, 02, 03
- New benchmarks or evaluation frameworks
- Embedding model downloads beyond BGE-large-en-v1.5
- API providers beyond Anthropic and Groq
- Tests for production code (none exists yet)
- Final score composition (separate iteration)

## Tech stack

- Python (match existing `pyproject.toml`)
- `python-dotenv` for `.env` loading
- `groq` Python SDK or OpenAI-compatible client pointed at Groq
- `anthropic` Python SDK
- `sentence-transformers` + `BAAI/bge-large-en-v1.5` (NEW — escalate before download, model is ~1.3GB)
- `numpy`, `pandas`, `scipy` (likely already present)
- No new packages without escalation

## Architecture (new or modified files only)

```
scripts/
├── 01_annotate_corpus.py        # EXISTING — run only, do not restructure
├── 10_remeasure_heuristics.py   # EXISTING — run only, do not restructure
├── 11_semantic_backtrack.py     # NEW (only if H3 needs rebuild)
└── 12_validate_phaseA2.py       # NEW — F1 validation for A.2 redesigns

research/
├── 04-phaseA1-remeasure.md      # MODIFY — fill stubs only, no restructure
└── 05-phaseA2-rebuild.md        # NEW

data/
├── validation-corpus/           # DO NOT regenerate
├── cost-log.jsonl               # APPEND ONLY
└── embeddings-cache/            # NEW (if semantic H3 runs)
```

## Verification commands

```yaml
- name: cost-check
  cmd: python -c "import json; tot=sum(json.loads(l).get('cost_estimate_usd',0) for l in open('data/cost-log.jsonl')); print(f'cumulative ${tot:.2f}'); assert tot<5, 'budget exceeded'"
  required: true
- name: corpus-integrity
  cmd: python -c "import json,glob; [json.loads(open(f).read()) for f in glob.glob('data/validation-corpus/**/*.json',recursive=True)]; print('corpus parses OK')"
  required: true
- name: report-04-no-tbd
  cmd: python -c "t=open('research/04-phaseA1-remeasure.md').read(); assert 'TBD' not in t and 'TODO' not in t, 'unfilled stubs'"
  required: true
- name: lint
  cmd: ruff check scripts/
  required: false
- name: types
  cmd: mypy scripts/
  required: false
```

`ruff` and `mypy` are best-effort. If not configured, skip and report — do not silently install.

## Subagent usage rules

- Use `executor` for any pass that writes or edits files
- Use `verifier` for running verification commands and the long-running annotation/remeasurement scripts (verifier captures exit + tail and reports)
- The orchestrator does NOT write code — always delegates
- DO NOT spawn a fresh annotation job from a subagent — first verify whether one is already running; escalate if uncertain

## Escalation rules (orchestrator must ask before doing)

- BEFORE restarting the bulk annotation job (it may be running from the previous session)
- BEFORE assigning final PRODUCTION_READY / SALVAGEABLE / DEAD verdict to any heuristic — surface F1 + IAA numbers and ask
- If IAA Cohen's kappa < 0.6 on ANY label — labels are suspect; ask before proceeding to verdicts
- BEFORE downloading the BGE-large embedding model (~1.3GB)
- BEFORE installing any dependency not listed in "Tech stack"
- If cumulative API cost in `cost-log.jsonl` exceeds $4 (80% of $5 cap)
- BEFORE modifying research reports 01, 02, or 03 — never expected; escalate if you think it's needed
- If verifier reports any existing script newly failing
- If a single executor pass would touch more than 4 files
- If verification fails 3 times in a row on the same check
- BEFORE pivoting to Path B (LLM-judge-primary) — never expected this iteration
- BEFORE expanding the corpus (A.3 is deferred)
- BEFORE adding embedding or API providers beyond what's in "Tech stack"

## Hard rules

- DO NOT modify `research/01-sota-scan.md`
- DO NOT modify `research/02-trajectory-waste.md`
- DO NOT modify `research/03-validation-corpus.md`
- DO NOT regenerate, overwrite, or delete anything in `data/validation-corpus/`
- DO NOT rewrite past entries in `data/cost-log.jsonl` (append only)
- DO NOT export `ANTHROPIC_API_KEY` to the shell environment; use `python-dotenv` inside the script process
- DO NOT restart the bulk annotation job without escalating
- DO NOT change the locked model allocation (GPT-OSS / Sonnet / BGE)
- DO NOT write production scorer code in this iteration
- DO NOT commit `.env` or any secret

## Budget

- Soft target: 1–2 Claude Code sessions
- Hard cap: stop and escalate after 20 executor invocations
- API cost hard cap: $5 cumulative across entire project (check `cost-log.jsonl`); escalate at $4
- Orchestrator runs `/cost` at midpoint and reports

## Success criteria (orchestrator verifies ALL before declaring done)

- `research/04-phaseA1-remeasure.md` has no TBD/TODO markers
- `research/04-phaseA1-remeasure.md` contains: F1 table per heuristic, IAA Cohen's kappa per label, confusion matrices, 5 failure-mode examples per heuristic, per-heuristic verdict
- Per-heuristic verdicts were escalated to user and confirmed before being treated as committed
- A.2 redesigns (if any) have validation F1 reported in `research/05-phaseA2-rebuild.md`
- `data/cost-log.jsonl` shows cumulative cost < $5
- Git history is clean: conventional commits, one concept per commit
- No file in `research/01-*`, `research/02-*`, `research/03-*` was modified
- `data/validation-corpus/` was not regenerated
- Annotation job was not duplicated or restarted unnecessarily

## Build order (recommended; orchestrator may adjust)

1. Read `CURRENT_STATE.md` end to end
2. Verify the in-flight annotation job state (`ps`, log file tail). Escalate if ambiguous; do not restart unilaterally
3. If bulk annotation complete: invoke verifier to confirm output integrity (200 sessions labeled, schema valid, cost log updated)
4. Run IAA mode (`scripts/01_annotate_corpus.py --mode iaa-only`)
5. Run F1 remeasurement (`scripts/10_remeasure_heuristics.py`)
6. Fill stubs in `research/04-phaseA1-remeasure.md` (text edits only)
7. Commit A.1 results. Escalate verdicts to user
8. After user confirms verdicts: plan A.2 work (likely semantic H3 via BGE if H3 = DEAD)
9. Implement A.2 redesigns via executor
10. Validate redesigns against existing corpus
11. Write `research/05-phaseA2-rebuild.md`
12. Commit A.2 results
13. Run full verification suite; declare done
