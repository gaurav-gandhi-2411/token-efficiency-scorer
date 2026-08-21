# Report 11 — Generalization Validation: 172-Developer Corpus, Corpus Characterization, and Detector Limits

**Author:** Gaurav Gandhi
**Date:** 2026-06-05
**Status:** FINAL — B5 run complete; detectors frozen (byte-identical to B4)

---

## 0. Purpose

B5 tests whether the B4 deterministic waste detectors (REPEATED-FAILED-RETRY, REDUNDANT-READ) generalize beyond the single 181-session pool. The dataset is SWE-chat (SALT-NLP/SWE-chat), a public corpus of real coding-agent sessions collected from real developers by the Entire.io CLI checkpoint logger. Two subsets: CC (1,053 sessions from 172 distinct users, processed through the native `claudecode_adapter`) and non-CC (893 sessions from OpenCode, Codex, Gemini CLI, Cursor; processed through `public_trace_adapter`).

> Contains information from [SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat) (Baumann et al., 2026, arXiv:2604.20779), made available under the [Open Data Commons Attribution License (ODC-BY) 1.0](https://opendatacommons.org/licenses/by/1-0/). AW1: added retroactively — every derived statistic in this report (172 developers, 1,053 sessions, the 1.4%/6.6% rate comparison, etc.) is a "Produced Work" under ODC-BY §4.3, which requires this notice. See [../DATA_SOURCES.md](../DATA_SOURCES.md).

**Detectors frozen throughout.** `scripts/waste_detectors.py` and `scripts/adapters/claudecode_adapter.py` are byte-identical to B4. `git diff --exit-code` on both files: clean.

This report supersedes report 10 §9 (cumulative limitations, single-developer scope) for the detector-generalization claim.

---

## 1. The Headline: Detectors Generalize — Single-Developer Asterisk Partially Retired

REPEATED-FAILED-RETRY fires across **172 independent developers**. PATH-A fires across real-world CC sessions from those same developers. Neither result required touching the detectors. The core finding B5 was built to produce:

**The observable-invariant waste patterns (exact-match retry loops, unchanged-file reads) are not artifacts of one developer's working style. They occur in real software engineering sessions from 172 developers worldwide using Claude Code.**

The single-developer generalization limitation in report 10 §9 — "derived from one 181-session CC pool; generalization to other agent tools or task domains is unvalidated" — is retired for REPEATED-FAILED-RETRY and REDUNDANT-READ PATH-A. These detectors fire on other developers' sessions. The boundary survives contact with external data.

---

## 2. The Rate Gap Is the Detector Working, Not Failing

The RFR fire rate is 1.4% on SWE-chat CC vs 6.6% in the pool — a 4.7x difference. Before interpreting this as a generalization failure, examine what produces it.

### 2.1 Numbers

| | Pool (B4) | SWE-chat CC |
|---|---|---|
| Sessions | 181 | 1,053 |
| Distinct developers | 1 | 172 |
| Median session length (turns) | 224 | 112 |
| RFR sessions fired | 12 | 15 |
| RFR fire rate | 6.6% | 1.4% |
| PATH-A sessions (REDUNDANT-READ) | 4 | 4 |
| PATH-B sessions (REDUNDANT-READ) | 18 | 0 (UNAVAILABLE — §4) |

### 2.2 What the 12 pool RFR events are

All 12 are long sessions (mean 787 turns; range 192–2,384) on a specific task mix:

- **GCP infrastructure errors:** `gcloud billing budgets create`, `gcloud compute instances get-serial-port-output`, `gcloud monitoring uptime delete` — quota errors and invalid-argument rejections that are unchanged between retries because the GCP state is unchanged.
- **SSH connectivity failures:** `Exit code 1` + recommendation to check SSH connectivity — the SSH tunnel is still down.
- **pytest environment misconfiguration:** `pytest_asyncio` plugin path error, identical traceback both times — the environment was not fixed between runs.
- **grep on non-existent file:** `grep: scraper/app.js: No such file or directory` — the file still does not exist.

These are structurally identical repeated failures: the underlying condition (quota unchanged, SSH still down, path still wrong) cannot improve between retries without a fix. That is exactly the pattern RFR was designed to count.

### 2.3 What SWE-chat CC sessions are

SWE-chat CC sessions are coding benchmark tasks — developers working on their own software projects, fixing CI, committing code, running builds. When a Bash command fails, these developers adapt: they read output, modify files, try different approaches. The error distribution is wider; the same command with the same output appearing twice consecutively without any state change is rarer in this population.

### 2.4 Verdict: H1 (task-mix), not H3 (format suppression)

Three hypotheses were diagnostically tested:

**H1 — Pool is a high-waste task-mix outlier (primary driver).** The pool skews toward infra/ML-ops work where identical-command failures are structurally more common. Supported: all 12 RFR events are GCP/SSH/environment sessions. Pool sessions are 2x longer (median 224 vs 112 turns) — more exposure — but RFR still fires 4.7x more per session. The excess is task-specific, not length-specific.

**H2 — SWE-chat sessions are shorter/simpler (secondary contribution).** Partially confirmed: SWE-chat sessions are shorter (2x), giving fewer retry opportunities per session. This contributes to the gap but cannot fully explain it, since longer pool sessions with more opportunities still fire at 4.7x the rate.

**H3 — CC v2.1.38 format changes suppress RFR fires (ruled out).** The error format `Exit code N\n...` is identical in pool sessions and SWE-chat CC sessions. The diagnostic found 23/1,053 sessions (2.2%) where consecutive Bash errors had the same 60-character prefix but differed beyond that — non-deterministic tool metadata (package resolution counts, test timing) breaks the exact match. Even counting all 23 as suppressed fires, the SWE-chat CC rate rises from 1.4% to at most 3.6% — still less than half the pool's 6.6%. H3 is a minor, general limitation (§6) not a version-specific format problem.

**The correct interpretation:** The rate gap is the detector correctly measuring a real population difference. High-intensity infra/ML-ops sessions produce more identical-failure retries. Ordinary coding sessions produce fewer. A detector that fires at different rates on genuinely different populations is discriminating correctly.

---

## 3. Corpus Characterization: The Pool Is a High-Waste Outlier

This is the sharpest finding B5 produces, and it qualifies all prior rate-based claims.

**The 181-session pool is calibrated to a high-intensity infrastructure and ML-ops developer, not a representative software engineer.**

The B2 baselines (report 08), the B4 detector fire rates (report 10), and the trajectory-quality judge calibration (reports 06–09) are all derived from this pool. That pool is now characterized as:
- One developer (gaurav-gandhi-2411 + 36 armand0e sessions)
- Skewed toward GCP/Cloud Run/GPU/ML-training/infra-debugging sessions
- RFR fire rate 4.7x higher than 172 real-world developers on coding tasks
- Session length 2x above the SWE-chat CC median

**What this qualifies:**

The **B2 token baselines** (report 08) are calibrated to high-intensity ML-ops sessions. A developer doing ordinary coding work will likely fall below the pool's p25 baseline rather than within the efficient band — not because they are inefficient, but because the pool's reference sessions are heavier. The domain-type taxonomy in B2 partially compensates (per-type baselines), but within each type, the pool developer's intensity skews the reference upward.

The **B4 RFR rate** (6.6%) should be read as "rate in high-waste infra context," not "universal rate in CC sessions." The SWE-chat CC rate (1.4% across 172 developers) is the more generalizable figure for ordinary software development.

This does not invalidate B2 or B4. The baselines are real; the detectors work. The characterization is: the system is calibrated for a specific operational context. Users in that context (heavy infra/ML-ops work) will get accurate measurements. Users doing lighter coding work are measured against a high-intensity reference.

**Report 08 is immutable.** This corpus-characterization finding is documented here as a qualification, not a correction. Report 11 is the authoritative superseding statement on scope.

---

## 4. PATH-B Version-Fragility (Finding 2 — Maintenance Flag)

PATH-B of REDUNDANT-READ fires zero times on SWE-chat CC (1,053 sessions). This is not a generalization failure — it is a **CC version issue that breaks the detector on current Claude Code**.

### 4.1 What happened

The PATH-B detector checks whether a Read tool result starts with `^\d+\t` — a tab-separated line-numbered prefix (e.g., `1\tdef authenticate...`). This is the format the CC Read tool produced when the detector was written.

CC v2.1.38 changed Read output from tab-separated (`1\tdef authenticate`) to arrow-separated (`   1→def authenticate`). The frozen regex `^\d+\t` does not match the arrow format. PATH-B fires zero times on v2.1.38 sessions — silently. No error, no warning, just zero fires.

**This means PATH-B is already silently broken on current Claude Code**, not merely unavailable on external datasets.

### 4.2 Evidence

A pool-vs-SWE-chat CC comparison isolates the break:
- Pool (sessions from ~2025): PATH-B fired on 18 sessions
- SWE-chat CC (sessions from 2026, v2.1.38): PATH-B fired on 0 sessions, PATH-A fired on 4

A format probe of a sampled SWE-chat CC transcript confirmed: `   1→package strategy\n   2→\n...` — spaces then number then U+2192 (→). The `^\d+\t` pattern cannot match this.

### 4.3 Maintenance action required

The detector is frozen for B5. The fix for a future unfrozen update:

```python
# Replace the single-format pattern with a dual-format pattern:
_LINE_NUMBERED_RE = re.compile(r"^\d+\t|^\s+\d+→")
```

This matches both the pre-v2.1.38 tab format and the v2.1.38+ arrow format. The `\s+` absorbs the leading spaces; `\d+` matches the line number; `→` is the U+2192 character.

**Until this fix is applied, PATH-B silently under-reports on current Claude Code sessions. All PATH-B counts in prior reports are from pre-v2.1.38 pool sessions.**

---

## 5. PATH-A: Confirmed Alive on Real-World Data

PATH-A of REDUNDANT-READ fires on 4 SWE-chat CC sessions (4/1,053 = 0.38%). The "File unchanged since last read" CC hint is still present in some v2.1.38 sessions. This is a low rate — the hint may fire less frequently in v2.1.38 or the SWE-chat population simply reads files less redundantly — but PATH-A is operational. The tool-authoritative verdict still appears in real sessions from real developers.

The pool also had 4 PATH-A sessions (4/181). The absolute counts match despite the 5.8x larger SWE-chat sample; the pool PATH-A rate (2.2%) is higher than SWE-chat CC's (0.38%), consistent with the corpus-characterization finding (§3) that the pool skews toward more-waste sessions.

---

## 6. H3 Detector Limitation: Non-Deterministic Tool Output (Minor)

The near-miss analysis found 23/1,053 SWE-chat CC sessions (2.2%) where consecutive qualifying Bash errors had the same 60-character prefix but differed in the full 300-character snippet. Two confirmed patterns:

1. **Package resolution count varies:** `Resolved, downloaded and extracted [18]` vs `[376]` — same workspace-dependency error, different package counts across resolution attempts. The exact-match check fails on the number.
2. **Test framework metadata varies:** Same failing test suite, but test timing or ordering information beyond character 150 differs between runs. The first 150 chars match; the tail differs.

In both cases, the same logical failure produces snippets that are not byte-identical. The exact-match requirement in the frozen detector misses these.

**This is NOT a CC version problem.** The error format (`Exit code N\n...`) is identical between pool and SWE-chat CC. The mismatch is caused by non-deterministic tool output metadata — a general property of some CLI tools, not a CC format change.

**Scale:** At most 23 suppressed fires ≈ 2.2pp contribution to the rate gap. Even fully applied, SWE-chat CC rate rises to at most 3.6% — still well below pool's 6.6%. H3 is a known limitation of the exact-match design (acknowledged in report 10 §3 as "an acceptable over-fire risk"), not a new finding that changes the interpretation.

No detector change required or appropriate. This is a documented edge case of the conservative design.

---

## 7. Non-CC Subset: Inconclusive

The non-CC subset (893 sessions: OpenCode 623, Codex 213, Gemini CLI 11, Cursor 19, others 27) produced RFR fire rate 0/893 = 0%. This result is **inconclusive** and should not be reported as a cross-agent finding.

### 7.1 Why it is inconclusive

RFR detection requires both a `tool_use` row (the shell call) and a `tool_result` row (the error output) to exist in the data. The SWE-chat `conversations.parquet` file does not store `tool_result` rows for OpenCode or Codex:

| Agent | Sessions | Tool_result rows in parquet |
|---|---|---|
| OpenCode | 623 | 0 |
| Codex | 213 | 0 (text-only conversations) |
| Gemini CLI | 11 (adapted) | ~224 (partial coverage) |
| Cursor, others | 46 | sparse |

865 of 893 sessions (96.9%) had zero tool-result turns after adaptation. Without tool results, the detector cannot observe error outputs; it cannot fire. The 0/893 rate reflects a dataset gap, not a behavioral finding.

### 7.2 What can be said

Gemini CLI (11 sessions with partial tool_result coverage): 0/11 RFR fires. This is a real but very small sample. It suggests lower RFR in Gemini CLI sessions, but 11 sessions is not enough to claim anything about the cross-agent pattern.

PATH-A and PATH-B are both UNAVAILABLE on non-CC agents: PATH-A requires the CC-proprietary "File unchanged since last read" string; PATH-B requires the CC Read tool's line-numbered output format. Neither appears in non-CC tool results.

**Cross-agent generalization remains unvalidated.** The dataset that could test it (full tool_result rows for OpenCode/Codex) is not publicly available in `conversations.parquet`. A future test would require either native OpenCode/Codex transcripts with full tool outputs, or a different public dataset with complete tool_result coverage.

---

## 8. Frozen Detector Verification

| File | B5 Status |
|---|---|
| `scripts/waste_detectors.py` | FROZEN — byte-identical to B4 (git diff: clean) |
| `scripts/adapters/claudecode_adapter.py` | FROZEN — byte-identical to B4 (git diff: clean) |

Detectors ran on SWE-chat CC sessions via the native CC JSONL adapter (no adaptation needed — the SWE-chat transcripts are CC JSONL format). Detectors ran on non-CC sessions via `scripts/public_trace_adapter.py` (new, non-frozen). No changes to frozen files throughout B5.

---

## 9. Updated Cumulative Limitations (Supersedes Report 10 §9)

**Carried from B1–B4:**
- No human gold set. rho≈0.79 vs provisional LLM reference (report 06) — instrument coherence, not human accuracy.
- Token axis covers ~50% of sessions; the other ~50% are UNAVAILABLE (scope gate).
- DEAD-END and EMPTY-TURN are unimplementable at current digest resolution.
- Deterministic waste fires on 3/18 of Qwen's WORSE sessions — conservative by design, not coverage failure.

**Updated from B4 (generalization validation adds new specificity):**

- **Corpus characterization (NEW — §3):** The 181-session pool is a high-waste infrastructure/ML-ops outlier. All rate baselines (B2 tokens, B4 waste rates) are calibrated to this context. The generalizable RFR rate for ordinary software development is ~1.4% (172 developers), not 6.6% (one infra-heavy developer). The pool's 6.6% is real for its context; it is not a universal baseline.

- **PATH-B maintenance issue (NEW — §4):** PATH-B is silently broken on CC v2.1.38+ due to a format change in Read tool output (`\d+\t` tab → `   \d+→` arrow). The regex must be updated to `^\d+\t|^\s+\d+→` before PATH-B is meaningful on current CC sessions. All prior PATH-B counts are from pre-v2.1.38 sessions.

- **Single-developer limitation (PARTIALLY RETIRED — §1):** Retired for RFR and PATH-A: both fire on sessions from 172 independent developers using Claude Code. Not retired for PATH-B (UNAVAILABLE, §4), for non-CC generalization (INCONCLUSIVE, §7), or for the task-mix calibration concern (pool remains infra-heavy, §3).

- **Non-CC generalization (NOT VALIDATED — §7):** Cannot be tested with current public data; conversations.parquet lacks tool_result rows for OpenCode and Codex (90% of non-CC corpus). Gemini CLI (11 sessions, 0/11 RFR) is too small to claim.

- **Exact-match edge case (minor — §6):** Non-deterministic tool metadata (package counts, test timing) causes ~2.2% of near-identical errors to miss the exact-match check. This is a known general limitation of the conservative design, not a CC version issue.

---

## 10. Outputs

| File | Description |
|---|---|
| `data/swechat_cc_adapted.jsonl` | 1,053 SWE-chat CC sessions adapted via claudecode_adapter |
| `data/swechat_noncc_adapted.jsonl` | 893 non-CC sessions adapted via public_trace_adapter |
| `data/public_waste_signals.jsonl` | 1,946 records (1,053 CC + 893 non-CC) with per-session detector results |
| `data/generalization_compare.json` | Three-way comparison: pool, SWE-chat CC, SWE-chat non-CC |
| `scripts/public_trace_adapter.py` | New adapter for SWE-chat conversations.parquet (non-frozen) |
| `scripts/generalization_run.py` | B5 pipeline: download, adapt, detect, compare |
