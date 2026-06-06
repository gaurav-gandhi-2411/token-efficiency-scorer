# token-efficiency-scorer

A self-hosted three-axis efficiency report for [Claude Code](https://claude.ai/code) sessions.
Run it on your own CC logs. Nothing leaves your machine.

**Status:** P1 — installable CLI + SDK (2026-06-07)

---

## Quick start

```bash
git clone https://github.com/gaurav-gandhi-2411/token-efficiency-scorer
cd token-efficiency-scorer
pip install -e .

# Score a single CC session log
tes score ~/.claude/projects/<project-id>/<session-id>.jsonl

# Score all sessions in a project directory
tes score ~/.claude/projects/<project-id>/

# Machine-readable output (full result object, includes all caveats)
tes score <path> --json
```

Secrets are redacted at ingestion (17 patterns). No data leaves your machine — the local
data moat is the product, not an option.

---

## What it scores

Each session gets a **three-axis report**. No composite score — three labeled signals, each
with its own domain of validity.

### Token economy

Is the session's token count above, within, or below the p25–p75 band for sessions of the
same task type? Scope-gated: sessions below the per-type p10 turn floor print UNAVAILABLE
rather than a misleading band verdict.

**Domain of validity:** Calibrated to a high-waste infra/ML-ops corpus (1 developer,
75 quality-gated sessions; B2 report). Baseline reflects high-intensity infra work — ordinary
coding sessions may read below-band without being inefficient. Interpret with the trajectory
verdict. UNAVAILABLE when below the scope gate or task type has no baseline.

### Trajectory quality

A local Qwen3-30B judge scores the session's trajectory on purposefulness (MUCH_BETTER /
BETTER / ABOUT_SAME / WORSE / MUCH_WORSE). Requires a local GPU (~18GB VRAM). Without the
judge, this axis prints UNAVAILABLE — that is the expected, complete state for most users.
Token and waste axes still run fully.

**Domain of validity:** Positive signal (MUCH_BETTER/BETTER) is cross-model corroborated
(84–96%; B3 report). Negative signal (WORSE/MUCH_WORSE) is model-dependent; do not treat as
fact. No human accuracy calibration. UNAVAILABLE when no local judge is configured.

To enable:
```bash
# Install Ollama: https://ollama.ai
ollama pull qwen3:30b-a3b   # ~18GB
```

### Deterministic waste

Two observable-invariant detectors:

- **REPEATED-FAILED-RETRY** — same shell command + same error + no state change between
  retries. Proof turns attached to every event.
- **REDUNDANT-READ** — same file content read twice with no edit between reads (PATH-A:
  CC's own "File unchanged" verdict; PATH-B: content-match, gap ≤ 5 turns). Dual-format
  as of P1 (tab format pre-v2.1.38 and arrow format v2.1.38+).

**Domain of validity:** Observable-invariant waste only. Judgment-of-progress waste (was
this cycle productive?) is not covered — requires human labeling. PATH-B depends on CC's
Read output format; may under-report on future CC versions if the format changes again.

---

## Setup

Requirements: Python 3.11+, conda or venv recommended.

```bash
pip install -e .
tes score --help
```

Optional (trajectory axis):

```bash
# Ollama + Qwen3-30B (~18GB VRAM)
ollama pull qwen3:30b-a3b
# Then run without --no-judge (default)
tes score <path>
```

---

## Results (B1–B5 credibility arc)

The scoring components were validated through a five-phase credibility arc before packaging.
Key findings:

**Token baselines (B2):** 75 quality-gated CC sessions, 5 task types. Scope gates set at
per-type p10 turn floor. Baseline encodes "efficient under expert prompting, high-intensity
infra/ML-ops work." See `research/08-baselines.md`.

**Trajectory judge (B3):** Cross-model corroboration check. Positive verdicts corroborated
at 84% strict / 96% top-2. Negative verdicts are model-dependent — do not treat as ground
truth. No human gold labels. See `research/09-cross-model.md`.

**Deterministic waste (B4):** REPEATED-FAILED-RETRY fired on 12/181 pool sessions (6.6%).
REDUNDANT-READ fired on 20/181 (11.0%). Observable-invariant only; judgment-of-progress
boundary documented. See `research/10-deterministic-waste.md`.

**Generalization (B5):** RFR and PATH-A validated across 172 independent developers (1,053
SWE-chat CC sessions). Rate gap: 6.6% pool vs 1.4% SWE-chat — pool is a high-waste
infra/ML-ops outlier, not a representative developer. Cross-agent (non-CC) generalization
is inconclusive (OpenCode/Codex parquet lacks tool_result rows). PATH-B was silently broken
on CC v2.1.38+ (arrow format); fixed in P1 (`^\d+\t|^\s+\d+→`), confirmed 51/1,053 fires.
See `research/11-generalization.md`.

**Corpus limitation (the honest asterisk):** The token baseline is calibrated to a
single high-intensity developer on infra/ML-ops work. The 1.4% generalization rate from
B5 is the better estimate for ordinary software development. A tool people run on their
own sessions is how diverse real-world data would eventually de-bias the calibration —
see Roadmap below.

---

## Architecture

```
tes/                      SDK package
├── adapt.py              CC JSONL → digest (frozen claudecode_adapter)
├── classify.py           5-type task classifier (keyword, deterministic)
├── baselines.py          Token baselines + scope gate
├── waste.py              Waste detectors (RFR + RR)
├── judge.py              Tiered judge: detect-availability → run or UNAVAILABLE
├── score.py              Three-axis scorer → ThreeAxisResult
├── report.py             Human-readable + JSON formatter (caveats inline)
└── data/cc_baselines.json  Bundled per-type token baselines

scripts/                  Validated research scripts (source of truth for scoring logic)
├── waste_detectors.py    Un-frozen in P1 (PATH-B dual-format fix)
├── efficiency_score.py   Three-axis implementation
├── layer2_judge.py       Qwen3 judge (GPU required, $0/call)
└── adapters/claudecode_adapter.py  CC JSONL parser (frozen)

tests/
├── test_behavior_preservation.py   Packaged == validated scripts on pool (20 sessions)
├── test_judge_tiering.py           Absent judge → UNAVAILABLE, not error
├── test_caveats_present.py         Each axis prints domain-of-validity
├── test_redaction_default_on.py    Secrets scrubbed at ingestion
└── test_waste_detectors.py         Detector unit tests (both formats)
```

The `tes/` SDK calls `scripts/` unchanged — behavior-preservation test proves packaging
changes no scores (golden fixture, 20 sessions × 15 fields).

---

## SDK usage

```python
from tes import load_baselines, score_session, JudgeConfig
from tes.adapt import adapt_session
from tes.baselines import BUNDLED_BASELINES_PATH
from tes.judge import score_trajectory
from tes.waste import detect_repeated_failed_retry, detect_redundant_read

baselines = load_baselines(BUNDLED_BASELINES_PATH)
record = adapt_session("path/to/session.jsonl")   # secrets redacted at ingestion

# Waste detection
session_id = record["session_id"]
turns = record["digest"]["turns"]
rfr = detect_repeated_failed_retry(session_id, turns)
rr = detect_redundant_read(session_id, turns)
waste_entry = {
    "session_id": session_id,
    "waste_events": [
        {"detector": e.detector, "turns": e.turns, "repeat_count": e.repeat_count,
         "evidence": e.evidence}
        for e in rfr + rr
    ],
}

# Optional trajectory judge (returns None when no judge available)
judge_entry = score_trajectory(record)

result = score_session(record, baselines, judge_entry=judge_entry, waste_entry=waste_entry)
print(result.band_verdict)          # "within_band" | "above_p75" | "below_p25" | "unavailable"
print(result.judge_verdict)         # "BETTER" | None (UNAVAILABLE)
print(result.waste_event_count)     # int
print(result.token_domain_of_validity)   # caveat string, always populated
```

---

## Roadmap

**Corpus de-biasing (design-only in P1):** The B5 finding is that 1.4% is the generalizable
waste rate; 6.6% is the infra-outlier pool rate. The path to a less biased baseline is
voluntary opt-in signal contribution: users run the tool locally, see their scores, and
optionally contribute redacted session digests (no source code, no content — digest only)
to a shared calibration corpus. P1 does not build the upload pipeline. A contribution
mechanism would need: consent flow, server-side redaction verification, public corpus
provenance tracking. Explicitly deferred to a later phase.

**Judge model variety:** Qwen3-30B is the validated judge. A smaller validated judge
(8B or laptop-runnable quantized) would make the trajectory axis accessible without a
GPU. That requires a new B3-equivalent cross-model corroboration run — it's a
re-validation phase, not a swap. Explicitly deferred.

**Cross-agent support:** Non-CC agents (OpenCode, Codex, Aider) would need adapted
ingestion (the CC adapter is CC-specific) and a re-generalization run once tool_result
rows are available. PATH-B format fragility suggests that format-specific adapters are
maintenance surface — they need versioned format tests.

---

## Verification

```bash
# All 140 tests
python -m pytest tests/ -v

# Behavior preservation (packaged == validated scripts on pool)
python -m pytest tests/test_behavior_preservation.py -v

# Judge tiering (absent → UNAVAILABLE, not error)
python -m pytest tests/test_judge_tiering.py -v

# Caveats present in output
python -m pytest tests/test_caveats_present.py -v

# Redaction default on
python -m pytest tests/test_redaction_default_on.py -v

# No network in local axes (moat verification)
python -c "
import pathlib
for f in ['tes/score.py', 'tes/waste.py', 'tes/baselines.py']:
    src = pathlib.Path(f).read_text()
    if 'http' in src or 'requests' in src:
        print(f'FAIL: {f}')
    else:
        print(f'OK: {f}')
"
```
