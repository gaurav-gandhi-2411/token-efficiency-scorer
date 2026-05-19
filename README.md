# token-efficiency-scorer

Scores coding-agent runs (Claude Code, Cursor, Copilot, Aider, custom agents) on token efficiency: `efficiency = outcome_quality / (tokens_used × difficulty_norm)`, measured against a counterfactual baseline of what an optimally-compiled run should have cost.

**Status:** research phase

---

## Overview

Given an agent trace (prompt/response turns with token counts), the scorer returns:

- **outcome_quality** — execution-based + LLM-judge hybrid score
- **tokens_used** — actual input + output tokens across the session
- **difficulty_norm** — task difficulty normalization (benchmark-derived)
- **counterfactual_baseline** — estimated optimal-prompt token cost for the same task
- **waste_delta** — `actual - baseline` token overspend

Supports turn-level, session-level, and task-level scoring.

---

## Project Structure

```
research/          SOTA scan and literature notes
data/              benchmark datasets, agent traces, eval fixtures
docs/adr/          architecture decision records
evals/             eval harnesses (separate from unit tests)
evals/fixtures/    per-case JSONL eval fixtures
scripts/           data prep, scoring pipelines
notebooks/         exploratory analysis
tests/             pytest unit tests
reports/           eval results, charts
```

---

## Reproduction

```bash
# clone
git clone https://github.com/gaurav-gandhi-2411/token-efficiency-scorer
cd token-efficiency-scorer

# install (uv)
uv sync
```

---

## Results

*Research phase — no results yet. Baseline metrics will be reported here alongside naive comparisons per project conventions.*
