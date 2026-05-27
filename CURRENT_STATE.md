# CURRENT_STATE.md — token-efficiency-scorer

Snapshot as of 28 May 2026. Read this BEFORE planning. The repo shows what exists; this doc explains why, what's load-bearing, and what not to touch.

## Project goal

Production token-efficiency scoring system for coding agents (Claude Code, Cursor, Copilot, Aider, custom). Working score formula: `efficiency = outcome_quality / (tokens_used × difficulty_norm)`. The defensible wedge is a per-task inference-time trajectory counterfactual — estimating what an optimal agent run SHOULD have cost in tokens, given the task. No published method does this; that is our novel contribution.

Status: VALIDATION phase, not production. Current work is Phase A.1 (heuristic remeasurement against LLM labels) → Phase A.2 (rebuild heuristics that fail their verdicts). Production scorer code comes later.

## Repo structure (key paths only)

```
token-efficiency-scorer/
├── research/
│   ├── 01-sota-scan.md            # LITERATURE — IMMUTABLE
│   ├── 02-trajectory-waste.md     # v1 ARCHITECTURE — IMMUTABLE
│   ├── 03-validation-corpus.md    # PHASE 2 RESULTS — IMMUTABLE
│   └── 04-phaseA1-remeasure.md    # STUBBED — FILL THIS ITERATION
├── scripts/
│   ├── 01_annotate_corpus.py      # Bulk annotation pipeline (in-flight)
│   ├── 10_remeasure_heuristics.py # F1 remeasurement
│   └── (older 00–04 corpus build scripts)
├── data/
│   ├── validation-corpus/         # LABELED CORPUS — DO NOT REGENERATE
│   └── cost-log.jsonl             # APPEND ONLY
├── .env                           # API keys (Anthropic + Groq) — gitignored
├── pyproject.toml
└── README.md
```

## Load-bearing files and why

- `research/01–03-*.md` — Accepted research reports documenting locked decisions (Path A chosen, LLMLingua-2 demoted, structural proxies dead, etc.). Treat as immutable. If a finding is wrong, write a new superseding report rather than edit.
- `research/04-phaseA1-remeasure.md` — Currently stubbed. This iteration fills TBD fields with real F1 / IAA / verdicts. Do not restructure; fill stubs only.
- `scripts/01_annotate_corpus.py` — Calls GPT-OSS 120B (Groq) for bulk labels, Sonnet 4.6 (Anthropic) for IAA. Has built-in pre-flight cost check, prompt caching, rate-limit throttle, $5 hard cap. The 20s inter-request throttle was conservative for free-tier Groq TPM limits — if changed, test on 5 sessions first.
- `scripts/10_remeasure_heuristics.py` — F1/precision/recall/confusion per heuristic. Outputs verdict tags (PRODUCTION_READY / SALVAGEABLE / DEAD). The 0.7/0.5 thresholds are PLACEHOLDERS, not final. Surface to user before treating verdicts as committed.
- `data/validation-corpus/` — 200 sessions across 3 HF datasets, structural ground truth + GPT-OSS labels (in-flight or done as of handoff). Regenerating breaks reproducibility and burns budget. Never overwrite.
- `data/cost-log.jsonl` — Append-only cost tracking. Cumulative project spend cap is $5. Read before any new API call.

## Conventions (non-obvious — orchestrator might violate)

1. **Model allocation is locked.** Bulk: `openai/gpt-oss-120b` via Groq. Eval/IAA only: `claude-sonnet-4-6` via Anthropic. Embeddings (when needed): local `BAAI/bge-large-en-v1.5` via sentence-transformers. Do not substitute. Do not add OpenAI or Gemini.
2. **API keys live in `.env`.** Load via `python-dotenv` inside the Python process. Never export `ANTHROPIC_API_KEY` as a shell env var — that's a different concern from `.env` loading and conflicts with Claude Code's own Max-plan auth.
3. **Session-level annotation, not per-turn.** One API call covers the full agent trajectory. ~5× cheaper. Cross-turn context required for H1/H3 anyway.
4. **Prompt caching on both providers.** Groq: 50% off cached prefix. Anthropic: ~90% off with `cache_control: {"type": "ephemeral"}`. The rubric is the cacheable prefix.
5. **Pre-flight 5 sessions before any bulk run.** Halt if projected cost exceeds budget.
6. **Cost log is append-only.** Never rewrite past entries.
7. **Research reports are versioned by phase.** Never edited in place after acceptance; new findings get a new numbered report.

## Key decisions and the WHY (don't relitigate without escalating)

- **Vertical: software engineering.** Coding has objective outcomes (tests, PRs, benchmarks) and existing infra (SWE-bench, Aider polyglot). Other verticals = subjective quality. Locked.
- **Path A over Path B.** Path A = rebuild heuristics on better data. Path B = pivot to LLM-judge-primary. We picked A because cheaper at inference, more interpretable. Path B remains a fallback only if A.2 fails. Do not pivot without explicit user approval.
- **The wedge: per-task inference-time trajectory counterfactual.** No published method does this. LLMLingua-2 was considered as a substitute (prompt compression as waste proxy) and rejected — it measures prompt verbosity, not session waste. SWE-bench p25-percentile-by-task-class is the v1 baseline; works in-distribution only.
- **Difficulty normalization is domain-level empirical.** Per-instance structural proxies (patch lines, turn count, description length) all failed at AUC ≈ 0.50 in report 03. Domain explains 7× resolve-rate variance. So we normalize by task class, not static code metrics. Known limit: OOD Claude Code traces fall through the classifier (0% coverage on CC-Bench).
- **PRMs deferred to v2.** v1 uses LLM-as-judge step scoring. Math-Shepherd-style auto-PRMs need an outcome signal we may not have for free-form Claude Code sessions.

## Known issues / accepted limitations

- **H3 regex backtrack detector fires 0/218 turns on real Claude Code traces.** Vocabulary over-fit to OpenHands. Semantic replacement via BGE embeddings is the likely A.2 task.
- **H1 original κ=0.066 against humans.** Definition mismatch — fires on any exact repeat, humans require prior failure. H1-revised candidate already in `10_remeasure_heuristics.py`. Validation pending in A.1.
- **H2 phase-2 F1=1.000 was a circular artifact** — ground truth and heuristic shared the same algorithm. Real F1 against LLM labels is what report 04 will show.
- **Corpus is 100% Python, 100% SWE-bench-shaped, 100% offline scaffolds.** Real Claude Code, Aider non-Python, multi-language — all deferred to Phase A.3. Do not expand corpus in this iteration.
- **Verdict thresholds (F1 ≥ 0.7 = PRODUCTION_READY) are arbitrary.** Real threshold depends on heuristic's weight in the final score, which isn't composed yet. Surface verdicts to user; never treat as auto-committed.
- **Annotation job may still be running as of handoff.** Bulk run started ~01:01 with 20s inter-request throttle, ~80 min estimate. Check process state and log tail before any action; do not restart.

## Tests / lint / types — current state

- No formal test suite yet. Scripts are research-grade. Unit tests arrive when production scorer code is written (post-validation).
- `pyproject.toml` exists but lint/type config is UNVERIFIED. Run `ruff --version` and `mypy --version` before assuming they work. If absent, escalate rather than install silently.
- Verification currently = scripts execute end-to-end without crash + outputs match expected schema. Not pytest-based.

## Open questions to flag (don't guess)

- Is the bulk annotation job still running? If failed/partial, how to recover without burning budget?
- After A.1 verdicts, which heuristics are DEAD vs SALVAGEABLE — product call, not technical.
- If H1-revised passes and H3 needs rebuild, ship A.2 partial or wait for all heuristics?
- F1 target for "good enough" semantic H3 — should be set by user based on intended weight in final score.
