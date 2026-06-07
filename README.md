# tracegauge

Three-axis efficiency scoring for Claude Code sessions — token economy, trajectory quality, deterministic waste. Runs entirely on your machine. Nothing leaves.

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://pypi.org/project/tracegauge/)
[![PyPI](https://img.shields.io/pypi/v/tracegauge.svg)](https://pypi.org/project/tracegauge/)

---

## Quick start

```bash
pip install tracegauge

# Background watcher + localhost dashboard (http://127.0.0.1:4747/)
tes serve

# Score a single session
tes score ~/.claude/projects/<project-id>/<session-id>.jsonl

# Score all sessions in a project directory
tes score ~/.claude/projects/<project-id>/

# Machine-readable output
tes score <path> --json

# Version
tes --version
```

`tes serve` starts two things: a background scan loop that auto-scores finished Claude Code sessions (token economy + deterministic waste, judge OFF by default), and a web dashboard on `http://127.0.0.1:4747/` where scores accumulate.

---

## Scope & Limitations

Read this before installing. These are not caveats to hide — they're the honest picture of what the tool measures and where the calibration comes from.

**Corpus caveat (token baselines).** The token economy baselines are derived from one developer's 75 quality-gated Claude Code sessions, skewed toward high-intensity infrastructure and ML-ops work (GCP, Cloud Run, training pipelines). B5 generalization validation across 172 independent developers (1,053 SWE-chat CC sessions) found the generalizable repeated-failed-retry rate is ~1.4% — versus 6.6% in the calibration pool, which is a high-waste infra outlier. A developer doing ordinary coding work may score below-band on the token axis without being inefficient; the baseline encodes "efficient under expert prompting on heavy infra work," not a universal reference.

**No human accuracy validation.** The trajectory judge (Qwen3-30B) is coherence-validated against a reference LLM (Spearman ρ ≈ 0.79), not calibrated to human expert labels. Positive verdicts (MUCH_BETTER/BETTER) are cross-model corroborated at 84–96%. Negative verdicts (WORSE/MUCH_WORSE) are model-dependent — treat them as a signal to review, not a ground truth.

**Tiered judge.** Token economy and deterministic waste run locally with no GPU and no network — these axes are always available. The trajectory quality axis requires a local Ollama judge (~18 GB VRAM for Qwen3-30B). Without it, trajectory prints UNAVAILABLE, which is the expected complete state for most users, not an error.

**What waste detection covers.** The two waste detectors catch observable-invariant patterns only: exact-match retry loops with no state change, and redundant file reads where the content was unchanged. Judgment-of-progress waste (was this cycle productive? was this approach the right one?) is not covered — that requires human labeling and is out of scope.

**The moat is the product.** All scoring is local. Your session logs never leave your machine. No telemetry, no phone-home, no external network calls (except the optional local Ollama endpoint). The localhost bind is enforced by construction, not configuration.

---

## The three axes

No composite score. Three independent labeled signals, each with its own domain of validity.

### Token economy

Compares the session's real token count (AI turns only; cache-read inflation removed) against the p25–p75 band for the same task type (ml-eval, debug-fix, infra-deploy, research-recon, feature-build). Verdicts: `above_p75`, `within_band`, `below_p25`, `unavailable`.

`unavailable` when the session is below the per-type p10 turn floor (scope gate) — the session is too short relative to the reference mass to produce a meaningful comparison. Not an error.

**Domain of validity:** calibrated to a high-waste infra/ML-ops corpus (one developer, 75 sessions). Interpret alongside the trajectory verdict.

### Trajectory quality

A local Qwen3-30B judge scores the session's trajectory on purposefulness: `MUCH_BETTER` / `BETTER` / `ABOUT_SAME` / `WORSE` / `MUCH_WORSE`.

Requires a local GPU (~18 GB VRAM). Without the judge, this axis is `UNAVAILABLE` — token and waste axes still run fully.

**Domain of validity:** positive signal cross-model corroborated (B3 report); negative signal is model-dependent. No human gold labels.

To enable:
```bash
# Install Ollama: https://ollama.ai
ollama pull qwen3:30b-a3b   # ~18 GB
tes score <path>             # judge auto-detected
```

### Deterministic waste

Two observable-invariant detectors with proof turns attached to every event:

- **REPEATED-FAILED-RETRY** — same shell command + same error output + no state change between retries. Validated across 172 developers (SWE-chat CC). ~1.4% of ordinary CC sessions; ~6.6% of high-intensity infra sessions.
- **REDUNDANT-READ** — same file content read twice with no edit between reads (PATH-A: CC's own "File unchanged" verdict; PATH-B: content-match, gap ≤ 5 turns). Dual-format regex handles both pre- and post-v2.1.38 CC output.

**Domain of validity:** observable-invariant only. Fires conservatively — misses judgment-of-progress waste by design.

---

## `tes serve` — always-available local service

```bash
tes serve [--port PORT] [--scan-interval SECONDS] [--stability-window SECONDS] \
          [--cc-path PATH] [--db-path PATH] [--background-judge]
```

- **Watcher**: scans `~/.claude/projects` every 2 minutes (configurable), scores any session file stable for 5+ minutes (token + waste; judge OFF by default).
- **Dashboard**: `http://127.0.0.1:4747/` — session list, per-session three-axis detail with domain-of-validity notes inline, trend views.
- **Store**: SQLite at `~/.tes/tes.db` (WAL mode; watcher writes and dashboard reads concurrently without locks).
- **Manual scores share the dashboard**: `tes score <path>` results also write to the store.

Moat properties: binds `127.0.0.1` only (never exposed to external interfaces), no data leaves the machine, redaction on by default at ingestion.

To enable the trajectory judge in the background watcher:
```bash
tes serve --background-judge
# WARNING: runs qwen3:30b-a3b (~18 GB VRAM) on your GPU for every new session continuously.
```

---

## What this does NOT do

- No composite efficiency score. The three axes are independent by design — a single number would hide the axis-specific domain limitations.
- No "catches all inefficiency." The waste detectors fire on observable-invariant patterns only.
- No accuracy guarantee on the trajectory axis. It's an LLM judge, coherence-validated, not human-calibrated.
- No data contribution / cloud scoring. The tool is local-only. A voluntary corpus contribution mechanism is on the roadmap (opt-in, redacted digests only) but not built.
- No cross-agent support yet. The CC adapter is Claude Code–specific; OpenCode/Codex/Aider would need their own adapters and re-validation.

---

## SDK usage

```python
from tes import load_baselines, score_session, JudgeConfig
from tes.adapt import adapt_session
from tes.baselines import BUNDLED_BASELINES_PATH
from tes.waste import detect_repeated_failed_retry, detect_redundant_read, build_waste_entry

baselines = load_baselines(BUNDLED_BASELINES_PATH)
record = adapt_session("path/to/session.jsonl")   # secrets redacted at ingestion

session_id = record["session_id"]
turns = record["digest"]["turns"]
waste_entry = build_waste_entry(session_id, turns)

# Optional: trajectory judge (returns None → UNAVAILABLE when no local judge)
from tes.judge import score_trajectory
judge_entry = score_trajectory(record)

result = score_session(record, baselines, judge_entry=judge_entry, waste_entry=waste_entry)
print(result.band_verdict)        # "within_band" | "above_p75" | "below_p25" | "unavailable"
print(result.judge_verdict)       # "BETTER" | None
print(result.waste_event_count)   # int
print(result.token_domain_of_validity)   # caveat string, always populated
```

---

## Validation

The scoring components were validated through a five-phase credibility arc (B1–B5) before packaging. Key results:

- **Token baselines (B2):** 75 quality-gated CC sessions, 5 task types, scope gates at per-type p10 turn floor. See `research/08-baselines.md`.
- **Trajectory judge (B3):** Cross-model corroboration. Positive verdicts: 84% strict / 96% top-2. Negative verdicts model-dependent. No human gold. See `research/09-cross-model.md`.
- **Deterministic waste (B4):** RFR fired 12/181 pool sessions (6.6%). RR fired 20/181 (11.0%). Observable-invariant boundary documented. See `research/10-deterministic-waste.md`.
- **Generalization (B5):** RFR and PATH-A validated across 172 developers (1,053 SWE-chat CC sessions). Rate gap (6.6% pool vs 1.4% SWE-chat) explained by corpus characterization — pool is a high-waste infra outlier. Cross-agent generalization inconclusive (parquet lacks tool_result rows for OpenCode/Codex). See `research/11-generalization.md`.

---

## License

[AGPL-3.0](LICENSE) — free to use and self-host; any modified version distributed as a network service must publish its source under the same license.

---

## Roadmap

- **Corpus de-biasing:** voluntary opt-in digest contribution (no source code, redacted) to build a broader calibration baseline. Not built yet.
- **Smaller judge:** a laptop-runnable quantized model for the trajectory axis (requires a new B3-equivalent corroboration run, not a swap).
- **Cross-agent support:** adapters for OpenCode, Codex, Aider once tool_result data is available for re-validation.
- **`tes install-hook`:** explicit opt-in SessionEnd hook for zero-latency scoring (modifies `~/.claude/settings.json` only on user request).

Recommended user follow-ups (not built): register `tracegauge.dev`; lawyer review of AGPL terms before any commercial raise.
