# NEXT_PHASE.md — Candidate Next Builds

**Status:** Options list, NOT a committed plan. Direction chosen next session.

Last updated: 2026-06-02 (B1 close). Read CURRENT_STATE.md first.

---

## Where B1 left us

The prototype is instrument-validated but not accuracy-validated. The remaining hard gate is
human ground truth. Everything below is only meaningful after — or explicitly structured to
enable — that gate.

The open limitations that constrain every option below:
- No human gold set (no accuracy claims, no weight tuning)
- Corpus is 100% Python/SWE-bench, offline scaffolds only
- No live log ingestion adapters
- Score weights untuned
- Local judge only (no production serving path)

---

## Option A — Human Gold Collection (closes the accuracy gate)

**What it is:** Collect the 40-session human gold set deferred from B1. Rate sessions using the
existing `scripts/rating_interface.py`. Compute judge-vs-human rho with the four calibration
cuts. Evaluate the kill criterion (rho >= 0.55) and target (rho >= 0.75) as originally designed
in report 05.

**Why first:** Until this exists, every other investment is building on an unvalidated instrument.
A human-vs-judge rho < 0.55 would require a different judge or a different rubric before any
other work is worth doing.

**Scope:** Human rating session (~2-4 hours), calibration run (local, $0), report 07. Low cost,
high leverage. Unlocks: weight tuning, accuracy claims, production promotion decision.

**Key decision needed:** Stratification of the 40 sessions — specifically whether to cap
swegym empty-loop cluster representation (currently 54% of MUCH_WORSE; should be <= 10% of
the gold set for a fair test).

---

## Option B — Tool Packaging (makes the scorer usable by others)

**What it is:** Wrap the three-layer pipeline into a callable API or CLI with a defined input
schema. Define the log ingestion contract (what a session record must contain). Produce a
`score_session()` function usable without knowing the internals.

**Why:** The scorer exists but is only usable by someone who knows the repo well. Packaging
is required before any external user (recruiter demo, collaborator, product evaluation) can
run it. Does not require the human gold gate; can proceed in parallel.

**Scope:** `app/` or `src/api/` layer, FastAPI endpoint or CLI entry point, input schema
validation (Pydantic), one end-to-end example that scores a real session from raw log to
efficiency_score. Does not include live log adapters (that is Option C).

---

## Option C — Live Log Ingestion Adapters

**What it is:** Build adapters that convert real agent logs from Claude Code, Aider, Cursor, or
OpenHands into the SessionDigest format that Layer 1 and Layer 2 expect. Today the pipeline
only works on pre-built digests in layer1_outputs.jsonl.

**Why:** Without this, the scorer cannot be applied to any real agent run. It is a demo tool,
not a product. This is the gap between "scores SWE-bench corpus" and "scores actual coding
agent sessions."

**Scope:** One adapter per agent format. Claude Code is the highest priority (the target user
persona). Requires understanding the Claude Code transcript format and mapping it to
TurnDigest fields. May surface gaps in the digest schema.

**Dependency:** Option B (packaging) should probably precede or accompany this, so adapters
have a clean interface to target.

---

## Option D — Distribution Model Decision

**What it is:** Decide how the scorer reaches users. Candidates: (a) open-source library +
self-hosted Ollama judge; (b) hosted API with a lightweight distilled judge model; (c) private
deployment for enterprise buyers; (d) HF Spaces demo for recruiter visibility.

**Why now:** The scoring architecture was explicitly designed for self-hostability (Ollama,
Apache 2.0 judge, $0 per session). The production path outlined in report 05 is a distilled
24B judge (~$50-200 to train). Both paths are viable; the choice shapes what to build next.

**Not a build — a decision.** This is a strategy choice that should precede Options B and C,
since the distribution model determines the interface and deployment target.

---

## Option E — Data Broadening + Hardening

**What it is:** Expand the corpus beyond 100% Python/SWE-bench to include: real Claude Code
traces, Aider non-Python sessions, multi-language tasks, interactive sessions. Re-run
calibration on the broadened corpus to test generalization.

**Why:** The current corpus limitation is documented explicitly in every report. All calibration
numbers are on a narrow slice. A buyer evaluating the scorer on their real workload (mixed
language, real-time, non-SWE-bench) is extrapolating from an unvalidated region.

**Dependency:** Option C (ingestion adapters) is a prerequisite. Option A (human gold) should
be done first so the narrowness caveat is quantified before broadening.

**Scope:** Significant — corpus curation, potential schema changes, full re-calibration. Not a
one-session effort.

---

## Suggested sequencing (not a committed plan)

If forced to order: **A → D → B+C → E**

- A first because it is the only thing that validates the core claim. Low cost, high leverage.
- D before B+C because the distribution target shapes what the interface needs to be.
- B and C together once the interface is defined.
- E last because it requires the ingestion infrastructure and a validated baseline to compare against.

The user chooses. This file is a menu.
