# PLAN — token-efficiency-scorer

## Project Goal

Build a production token-efficiency scoring system for coding agents (Claude Code, Cursor, Copilot, Aider, custom agents).

**Score formula:**
```
efficiency = outcome_quality / (tokens_used × difficulty_norm)
waste_delta = actual_tokens - counterfactual_baseline_tokens
```

**Units of analysis:** turn, session, task (all three).

---

## Phase 1 — Research (current)

- [x] Project setup: folder structure, git, GitHub repo
- [ ] SOTA scan: outcome quality measurement (Q1)
- [ ] SOTA scan: counterfactual baseline estimation (Q2)
- [ ] Recommended stack selection
- [ ] Commit research report to `/research/01-sota-scan.md`

## Phase 2 — Design

- [ ] Score formulation ADR
- [ ] Data schema design (agent trace format)
- [ ] Benchmark dataset selection
- [ ] Counterfactual baseline architecture

## Phase 3 — Implementation

- [ ] Agent trace ingestion pipeline
- [ ] Outcome quality scorer (execution + LLM-judge hybrid)
- [ ] Counterfactual baseline estimator
- [ ] Efficiency score computation
- [ ] Eval harness

## Phase 4 — Deployment

- [ ] FastAPI service
- [ ] Cloud Run deploy
- [ ] Prometheus metrics / observability

---

## Key Decisions Pending

- Which benchmark dataset to use as the primary eval corpus
- Whether counterfactual uses DSPy compilation, prompt compression, or both
- LLM judge: which model, which rubric, calibration methodology
