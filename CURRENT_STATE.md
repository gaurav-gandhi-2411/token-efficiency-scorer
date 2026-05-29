# CURRENT_STATE.md — token-efficiency-scorer

Snapshot as of 30 May 2026. Read this BEFORE planning. The repo shows what exists; this doc explains why, what's load-bearing, and what not to touch. This supersedes the prior CURRENT_STATE.md (which predated the architecture pivot).

## Project goal

Production token-efficiency scoring system for coding agents (Claude Code, Cursor, Copilot, Aider, custom). It answers: given what an agent was trying to do, how efficiently did it use tokens — independent of whether it ultimately succeeded. The defensible wedge is a per-task trajectory counterfactual (compare actual run against a domain reference for what an efficient run costs).

Status: VALIDATION COMPLETE, entering IMPLEMENTATION. The original heuristic-primary architecture was tested and retired (see below). We are building a hybrid LLM-judge scorer per accepted report 05.

## The pivot — why the architecture changed (critical context)

We spent four validation phases testing whether simple per-turn heuristics (H1 redundant_read, H2 duplicate_message, H3 backtrack, H4 tool_result_used) could carry the score. Phase A.1 inter-annotator agreement (IAA) against an independent judge model settled it:

- H1 kappa = 0.15 — FAILED
- H2 kappa = 0.825 — PASSED (survives as a deterministic feature)
- H3 kappa = 0.43 — FAILED
- H4 kappa = 0.19 — FAILED (systematic rubric drift; judge over-fires on no-tool-call turns)

Root cause is structural, not fixable by rewording: H1/H3/H4 measure intent and cross-turn credit assignment, which no per-turn rubric specifies tightly enough to remove annotator judgment. Two strong models read the same trace and disagree. We also confirmed model-family bias (same-family labelers agree more than cross-family).

The lesson that drives everything now: **LLM labels are not ground truth.** The new architecture is calibrated against HUMAN ratings, and that human gold set is the single most important asset in the project.

## New architecture (from accepted report 05)

Three layers:

- **Layer 1 — Deterministic (free, reliable):** 7 scalar features (test_outcome, total_tokens, turn_count, h2_duplicate_count, cache_hit_rate, p25_token_ratio, domain_id) plus a deterministic structured trace digest. No LLM. This refines report 05's "summary": we use a reproducible structured digest rather than an LLM-generated prose summary, so Layer 1 stays deterministic and the human + judge consume identical facts.
- **Layer 2 — Reference-based pointwise judge:** local Qwen3-8B via Ollama reads ONE trace digest plus the domain reference standard and rates trajectory efficiency. Reference-based pointwise was chosen over pairwise on the evidence (pointwise flips 9% vs pairwise 35% under perturbation; we have a fixed reference so we don't need pairwise's advantage). Criterion-order permutation fix mitigates position bias.
- **Layer 3 — Calibration harness:** the trust asset. 40-session human gold set (rated by the consultant), Spearman rho between judge and human, target rho >= 0.75, kill criterion rho < 0.55 after 3 prompt iterations.

Score formula (weights PROVISIONAL, to be tuned against the gold set):
```
efficiency        = composite_quality / (p25_token_ratio × difficulty_norm)
composite_quality = 0.50 × outcome_score + 0.35 × judge_score + 0.15 × h2_score
difficulty_norm   = 1 / domain_resolve_rate   (empirical priors, report 03)
```
No-test fallback: outcome_score = 0.5, remaining weight renormalized onto judge + h2. NOTE: testless sessions (common in real Claude Code use) lean almost entirely on the judge — judge reliability matters most exactly where there is no test anchor.

CRITICAL decoupling: the judge and the human gold rating assess efficiency CONDITIONAL on the task, NOT final success. Outcome is captured separately in outcome_score. If the judge rates success, judge_score and outcome_score become collinear and the composite double-counts. Efficiency = progress per token, independent of whether the task was solved.

## Repo structure (key paths only)

```
token-efficiency-scorer/
├── research/
│   ├── 01-sota-scan.md            # IMMUTABLE
│   ├── 02-trajectory-waste.md     # IMMUTABLE
│   ├── 03-validation-corpus.md    # IMMUTABLE (domain priors, p25 baselines live here)
│   ├── 04-phaseA1-remeasure.md    # IMMUTABLE (IAA results, heuristic verdicts)
│   ├── 05-architecture-pivot.md   # IMMUTABLE (the accepted new design)
│   └── cleanup-backlog.md         # post-iteration tech-debt list
├── scripts/
│   ├── 01_annotate_corpus.py      # historical — annotation pipeline
│   ├── 10_remeasure_heuristics.py # historical — F1/IAA
│   └── (older corpus build scripts)
├── data/
│   ├── validation-corpus/
│   │   ├── annotations/gpt_oss/   # 191 LLM-labeled sessions (mixed labeler — see below)
│   │   └── skipped.jsonl          # 9 partial-coverage skips
│   └── cost-log.jsonl             # APPEND ONLY
├── .env                           # API keys — gitignored
├── pyproject.toml
└── README.md
```

## Load-bearing files and why

- `research/01–05-*.md` — Accepted, immutable. Report 03 holds the domain resolve-rates and p25 token baselines that feed difficulty_norm and the judge reference. Report 05 is the design contract for the build. New findings get a NEW report, never an edit.
- `data/validation-corpus/annotations/gpt_oss/` — 191 sessions: 35 labeled by GPT-OSS 120B (with _reason fields), 156 by Claude Haiku 4.5 (compact, no _reason). The directory name is misleading (a cleanup-backlog item); the `labeler_model` field inside each JSON is the source of truth. These are a SECONDARY signal and eval set now — NOT the calibration ground truth.
- `data/cost-log.jsonl` — Append-only. Cumulative project spend is ~$2.59 of a $5 cap.

## Conventions (non-obvious — orchestrator might violate)

1. **Judge model is local Qwen3-8B via Ollama (`qwen3:8b`, Q4_K_M).** $0 inference, Apache 2.0 (commercially usable — matters for selling), different family from the Claude agents we measure (avoids self-enhancement bias), self-hostable (enterprise privacy story). Do NOT substitute a Claude model or a paid API as the judge without escalation.
2. **Human gold ratings are sacred.** Never synthesize them, never let an LLM fill them in, never impute missing ones. If the human hasn't rated a session, it is not in the gold set. Full stop.
3. **API keys in `.env`, loaded via python-dotenv.** Never export ANTHROPIC_API_KEY to the shell (conflicts with Claude Code Max-plan auth). Never commit `.env`.
4. **Cost log append-only.** $5 cumulative cap, escalate at $4.
5. **Research reports versioned by phase, never edited after acceptance.**
6. **Judge and human rate efficiency CONDITIONAL on task, not success.** See the decoupling note above. Easy to get wrong; silently corrupts calibration.

## Known issues / accepted limitations

- **Corpus is 100% Python, 100% SWE-bench-shaped, 100% offline scaffolds.** Real Claude Code, Aider non-Python, multi-language — deferred. Do not expand corpus in this iteration.
- **Mixed-labeler corpus** (35 GPT-OSS + 156 Haiku) with family bias documented in report 04. Fine as a secondary/eval signal; not ground truth.
- **Groq Developer-tier entitlement is bugged** (support ticket filed externally). Not blocking — the judge is local. If Groq resolves, a Groq-hosted larger judge becomes a cheap option worth revisiting.
- **Score weights are provisional.** Tuned against the human gold set in this iteration.
- **cleanup-backlog.md** holds deferred tech debt (rename gpt_oss/ → annotations/, backport labeler_model to the 35 GPT-OSS files, etc.). Do not action during the build unless a step depends on it.

## Tests / lint / types — current state

- No production test suite yet. Scripts have been research-grade. The implementation iteration is the first that should add real unit tests for the scoring pipeline.
- `pyproject.toml` exists; ruff/mypy config status UNVERIFIED — check before assuming, escalate rather than install silently.

## Open questions to flag (don't guess)

- Will Qwen3-8B clear the calibration bar (rho >= 0.55 floor)? Report 05 rated this LOW confidence. If it fails, the diagnostic is: run a small frontier-judge batch to disambiguate "weak local model" from "broken architecture" — but only on escalation (costs money).
- Does the deterministic digest preserve enough efficiency signal for the human to rate reliably? Validate digest fidelity on a few examples BEFORE the human rates 40.
- Final weight values — provisional until tuned on the gold set.
