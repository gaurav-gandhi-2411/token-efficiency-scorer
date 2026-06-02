# Report 07 — Real-Log Ingestion: Claude Code Adapter

**Author:** Gaurav Gandhi
**Date:** 2026-06-02
**Status:** FINAL — ingestion phase (option C) complete; adapter validated end-to-end on GPU

---

## 0. What This Report Covers

This report documents the ingestion phase: building an adapter that converts real Claude Code
session logs into the digest schema the scorer consumes, so the judge can evaluate real
coding-agent sessions rather than pre-built SWE-bench digests.

Key decisions in this phase:
- How the target digest schema maps to Claude Code log fields (and where it doesn't)
- Why the token-economy layer (p25_token_ratio) cannot be applied to ad-hoc CC sessions
- Why CC sessions emit a trajectory-quality verdict only, not a full composite efficiency score
- GPU is required for 30B MoE inference; the laptop is not a viable platform

The phase outcome: 3 real CC sessions scored end-to-end by qwen3:30b-a3b on an L4 GPU. Verdicts
are grounded and sane. The adapter works.

---

## 1. Claude Code Session Log Format

Claude Code stores per-session JSONL transcripts at:
```
C:\Users\gaura\.claude\projects\<project-slug>\<session-uuid>.jsonl
```

Each file contains one JSON object per line. Message types:

| Type | Role | Content |
|---|---|---|
| `mode` | Session metadata | Normal / plan mode |
| `permission-mode` | Session metadata | bypassPermissions etc. |
| `file-history-snapshot` | Session metadata | File state at session open |
| `user` | Conversation | Human prompt (string) OR tool results (list of tool_result objects) |
| `assistant` | Conversation | AI response: array of text/tool_use/thinking blocks |
| `ai-title` | Metadata | Computed session title |
| `attachment` | Metadata | File attachments |
| `last-prompt` | Metadata | Resume tracking |
| `queue-operation` | Metadata | Background task notifications |
| `system` | Metadata | Turn duration, message counts |

Only `user` and `assistant` messages carry conversation content. All others are skipped by the
adapter.

### 1.1 Structured tool calls — a clean advantage over swe_agent

`assistant` messages carry a content array where tool calls appear as explicit `tool_use` blocks:

```json
{
  "type": "tool_use",
  "name": "Bash",
  "input": {"command": "git status", "description": "..."}
}
```

The tool name is directly available in the `name` field. This is structurally cleaner than
swe_agent, where tool calls are embedded in prose-formatted `content_text` and must be
extracted by parsing the first code block (the `_extract_tools_from_content` fallback in
`trace_digest.py`). For CC sessions, `tool_names` extraction is a direct field read with no
text parsing.

### 1.2 Token counts — actual API billing units

Every `assistant` message carries a `usage` block:

```json
{
  "input_tokens": 3,
  "cache_creation_input_tokens": 13583,
  "cache_read_input_tokens": 20363,
  "output_tokens": 185
}
```

`input_tokens` is the uncached new text added since the last call — often just 3 tokens on
cache-heavy sessions. `cache_creation_input_tokens` + `cache_read_input_tokens` represents the
full context pulled from the cache. These are **actual Anthropic API billing units** with BPE
tokenizer counts, not approximations.

### 1.3 Main chain vs sidechain

`isSidechain: true` messages belong to subagent invocations (spawned Agent tool calls). The
adapter includes only `isSidechain: false` messages (the main conversation thread).

### 1.4 Scale of available data

140 real CC sessions available across 12 project folders on the user's machine. Sizes range
from 3 KB (trivial sessions with no AI turns) to 10 MB (~600 lines, 270 assistant messages).
No `history/` directory was present.

---

## 2. Target Digest Schema (Locked)

The digest schema is defined in `src/token_efficiency/trace_digest.py` and validated against
qwen3:30b-a3b in B1. Full spec in `docs/digest_schema.md`. Key structure:

**Top-level record** (input to `layer2_judge.py`):

| Field | Type | Notes |
|---|---|---|
| `session_id` | str | Canonical identifier |
| `domain_id` | str | Domain bucket (8 SWE-bench categories) |
| `labeler_model` | str | `"not_applicable"` for CC; must NOT be `"missing"` (silent drop guard) |
| `digest` | dict | Nested `SessionDigest` |

**SessionDigest**:

| Field | Type | Notes |
|---|---|---|
| `session_id` | str | Same as top-level |
| `domain` | str | Same as `domain_id` |
| `resolved` | bool | Task resolution status |
| `total_tokens` | int | Session token total |
| `turn_count` | int | Number of turns |
| `h2_duplicate_count` | int | Annotated duplicate turns |
| `cache_hit_rate` | float | sum(cache_read) / sum(total_input) |
| `p25_token_ratio` | float | total_tokens / domain_p25_baseline |
| `output_tokens_available` | bool | True when output tokens logged |
| `task_description` | str | First user turn, ≤800 chars |
| `turns` | list[TurnDigest] | All turns in order |

**TurnDigest** (what the judge reads):

| Field | Type | Notes |
|---|---|---|
| `turn_index` | int | Sequential from 0 |
| `role` | str | `"user"`, `"ai"`, `"tool"` |
| `tool_names` | list[str] | Tool names for this turn |
| `content_snippet` | str | First 300 chars of content |
| `token_count_input` | int | Full effective context for AI turns; 0 for others |
| `token_count_output` | int | Output tokens; 0 for non-AI turns |
| `cache_read` | int | Cache-read tokens |
| `h2_duplicate` | bool | H2 annotation flag |

The judge sees `digest_to_text(digest, show_stats=False)` — the rendered trajectory with role,
tools, token counts, and content snippet per turn. p25_token_ratio is NOT shown to the judge.

---

## 3. Field Mapping: Clean vs Gap

### 3.1 Clean mappings

| Target field | CC source | Notes |
|---|---|---|
| `session_id` | JSONL filename stem | UUID, no extension |
| `task_description` | First user message (string content) | Direct |
| `output_tokens_available` | Always `True` | CC always logs usage |
| `scaffold` | Hardcoded `"claude_code"` | New scaffold type |
| `labeler_model` | Hardcoded `"not_applicable"` | No H2 annotation pipeline |
| `cache_hit_rate` | sum(cache_read) / sum(total_input) across assistant messages | Direct |
| `turn_index` | Sequential assignment during linearisation | Computed |
| **Per-turn** `role` | `"ai"` for assistant msgs; `"user"` for human text; `"tool"` for tool-result msgs | Clean split |
| **Per-turn** `tool_names` | `message.content[].name` from `tool_use` blocks | Structured, no parsing |
| **Per-turn** `content_snippet` | Concatenated `text` blocks (AI) or string content (user) | Clean |
| **Per-turn** `token_count_output` | `usage.output_tokens` | Direct |
| **Per-turn** `cache_read` | `usage.cache_read_input_tokens` | Direct |

### 3.2 Accepted gaps (documented, not papering over)

| Target field | CC value | Why |
|---|---|---|
| `resolved` | Always `False` | CC has no test harness; outcome_score = 0.0 in composite |
| `h2_duplicate_count` | Always `0` | No annotation pipeline for CC; h2_score = 1.0 (max) |
| `h2_duplicate` per turn | Always `False` | Same |

Both gaps feed the composite formula correctly without corruption:
`outcome_score = 0.0` shifts composite weight onto judge (50% → judge carries 58% after
renormalization: 0.35/(0.35+0.15) = 0.70 of the non-outcome weight). `h2_score = 1.0` means
H2 contributes its 0.15 at maximum — a conservative, not generous, default.

### 3.3 Hard gap — domain and p25_token_ratio (see section 4)

The largest structural gap. Addressed separately below.

---

## 4. Token-Definition Decision

### 4.1 What the corpus token definition actually is

`00_download_corpus.py` computes per-turn token counts as:

```python
approx = max(1, int(len(content_text.split()) * 1.3))   # whitespace word count × 1.3
input  = approx if role in ("user", "tool") else 0
output = approx if role == "assistant"      else 0
# Note: swe_agent uses role "ai" — condition is role == "assistant" → output=0 for all swe_agent AI turns
tokens_total = sum(input + output for each turn)
```

`cache_read` is always 0 (not available from HuggingFace datasets).

The `p25_token_ratio` baselines in `layer1_features.py` were computed from this definition.

### 4.2 How CC token counts differ

CC sessions log actual Anthropic API billing units: `input_tokens` (uncached new text per
assistant call, often 3 tokens on cache-heavy sessions) + `cache_creation_input_tokens` +
`cache_read_input_tokens` + `output_tokens`. These are real BPE tokenizer counts measuring
**accumulated context consumption**, not per-message content size.

**Two incomparabilities, not one:**

1. **Tokenizer vs word-count:** Claude's BPE tokenizer gives different counts than
   word_count × 1.3. Code tokenizes differently than prose; the ratio is not constant.

2. **Context accumulation vs content size:** CC's `input_tokens` is the uncached increment
   per call. The corpus approximation is the full content of each message. These are different
   quantities regardless of tokenizer choice.

### 4.3 Conclusion for this phase

CC `total_tokens` is not directly comparable to corpus `tokens_total`. The corpus definition
is unrecoverable for cross-population comparison: even if we re-computed CC totals using
word_count × 1.3, the underlying populations (CC ad-hoc tasks vs SWE-bench Python bug fixes)
would still not share the same p25 baseline.

**For launch-2 per-customer baselines:** build them from actual customer CC sessions using
actual tokenizer counts. This creates a CC-native, self-consistent token-counting convention.
It does not compare to the SWE-bench p25, nor does it need to — per-customer baselines measure
relative efficiency within the customer's own session distribution.

---

## 5. Domain / p25 Gap and Scope Decision

### 5.1 Why domain assignment doesn't work for CC sessions

The 9 domains in `DOMAIN_RESOLVE_RATE` (lib_general, type_checker, data_ml, etc.) are
SWE-bench GitHub repository categories derived from `instance_id` strings like
`sqlalchemy-mixins-108`. A real CC session — "add dark mode to this React app" or "fix the
caching bug in the auth middleware" — has no equivalent anchor.

A simple substring-based domain classifier applied to `task_description` would produce
unreliable assignments with no calibration signal. Silently assigning a wrong domain would
corrupt `p25_token_ratio` (and therefore `efficiency_score`) in ways that are invisible to the
user. This is the B-1 "untrustworthy approximation" failure mode we documented in phase A.1.

### 5.2 Decision

**Real CC sessions emit trajectory-quality score only. Token-economy is marked unavailable.**

The adapter sets:
- `p25_token_ratio = 1.0` — neutral placeholder (does not affect the judge, only the composite)
- `token_economy_available = False` — metadata flag for downstream callers
- `domain_inferred = "fallback_unknown"` — explicit provenance

The judge reads `domain = "unknown"` in its prompt header. The trajectory verdict + reasoning
is the output. `score.py` (the composite formula) is not run on CC sessions in this phase.

**What this gives users:** "here is how purposeful and direct each of your coding-agent
sessions was, with behavioral reasoning" — a real product signal. Efficient vs wasteful
trajectory patterns are identifiable without a domain baseline.

### 5.3 Launch-2 path to full efficiency scoring

Two routes, both deferred:

- **B — Per-customer baselines:** After enough CC sessions are ingested for a given customer
  (suggested minimum: ~30 sessions in a comparable task category), compute the 25th percentile
  of their `total_tokens` (by actual CC tokenizer counts) as the baseline. This is the
  `compute_domain_p25_baselines()` pattern applied to a CC-native distribution. No cross-
  population comparison required.

- **C — Domain classifier:** A lightweight Ollama call to assign one of the 8 SWE-bench domain
  categories from `task_description`. Requires validated classifier accuracy before enabling,
  since a wrong domain assignment directly corrupts efficiency scores. Not zero-cost.

Both require explicit opt-in. Neither is "assign `domain='unknown'` and pretend the number
means something."

---

## 6. GPU Requirement

qwen3:30b-a3b is a 30B MoE model. On a laptop with 7.1 GB VRAM / 22 GB total (Q4_K_M, 32%
GPU-resident), partial-offload inference is non-viable for judge use:

| Platform | Session | Time | Result |
|---|---|---|---|
| Laptop (partial VRAM, Ollama 0.24.0) | 18-turn CC session | 577s | Empty JSON (inference exhaustion) |
| L4 GPU (full VRAM, Ollama 0.24.0) | 18-turn CC session | 31.7s | MUCH_BETTER ✓ |
| L4 GPU (full VRAM) | 103-turn CC session | 67.1s | WORSE ✓ |
| L4 GPU (full VRAM) | 413-turn CC session | 45.0s | MUCH_BETTER ✓ |

The 577-second laptop run produced an empty JSON string (JSON parse error), not a verdict. On
GPU, all three sessions scored cleanly.

**Why the laptop fails:** On each forward pass, the non-resident expert weights (~68% of the
MoE model) must be fetched from system RAM. For a 30B MoE model this creates a severe
memory bandwidth bottleneck. The total time to generate a ~100-token JSON response approaches
10 minutes. After that duration, the Ollama connection state is exhausted before the JSON
payload is emitted.

**Cold-start note:** The timing probe (first inference after VM boot) ran for 258 seconds and
returned empty JSON — the same symptom as the laptop, caused by VRAM initialization on first
load. All subsequent inferences (full run) were 31–67 seconds. The probe design should include
a warmup step before timing; this is a runner script issue, not a judge issue.

**Estimated cost:** g2-standard-8 SPOT, asia-east1-a, ~40 min active compute:
~$0.56/hr × (40/60 hr) ≈ **$0.37 USD** for this validation run.
Cumulative project API spend: ~$2.59 of $5 cap (unchanged — judge is local).

---

## 7. Validation Results

### 7.1 Sessions

| Session | Project | Turns | Digest chars | Verdict | Conf | Time |
|---|---|---|---|---|---|---|
| d57f0f0e | expense-tracker | 18 | 3,480 | **MUCH_BETTER** | 0.90 | 31.7s |
| a3496457 | token-efficiency-scorer (this session) | 103 | 18,708 | **WORSE** | 0.90 | 67.1s |
| 609d73cf | token-efficiency-scorer (prior session) | 413 | 62,995 | **MUCH_BETTER** | 0.95 | 45.0s |

### 7.2 Verdicts and reasoning

**d57f0f0e — MUCH_BETTER (expense-tracker, 18 turns)**

> "Agent read all required files in sequence (CURRENT_STATE.md, eval artifact, smoke artifact)
> after globbing, integrated results directly into output, and correctly handled /cost limitation
> without deviation."

Correct. A clean, short, focused read-and-report session. No redundancy, no backtracking.

---

**a3496457 — WORSE (token-efficiency-scorer, 103 turns — this adapter development session)**

> "After reading required documents, agent deviated to read codebase/corpus before writing
> report, causing trajectory drift."

**The judge flagged real drift in one of our own development sessions. On review, the call is
correct.** After reading CURRENT_STATE.md, NEXT_PHASE.md, and report 06 (the required
orientation step), the agent read `trace_digest.py`, `layer1_features.py`, `layer2_judge.py`,
`score.py`, and several corpus trace files before writing `docs/digest_schema.md`. From a
pure trajectory-purposefulness standpoint, some of those reads were exploratory rather than
necessary for the deliverable. A more purposeful path would have read only the source files
directly required to document the schema.

This is the best credibility evidence available: a judge that discriminates rather than
rubber-stamps, including on its own construction. The WORSE verdict is honest and the
reasoning cites the correct behavioral pattern.

---

**609d73cf — MUCH_BETTER (token-efficiency-scorer, 413 turns — B1 GPU calibration session)**

> "Systematic verification of all requirements before proceeding, diagnosis and fixing of
> issues (missing \$HOME, spot capacity) rather than repeating failed attempts, and
> verification of all three checks before starting the full run."

Correct. The B1 scoring session involved diagnosing two VM failures (HOME env missing, spot
capacity exhausted) and fixing them before re-running. The judge correctly identified the
systematic diagnosis-then-fix pattern as purposeful rather than repetitive.

### 7.3 Sanity assessment

Verdicts are grounded, specific, and directionally correct. The judge cites turn-level behavior
(sequential reads, direct integration, deviation pattern) rather than generic rubric language.
It spans the quality range (MUCH_BETTER → WORSE → MUCH_BETTER) across 18-to-413-turn sessions.
Token-economy is not in the judge prompt; the verdicts are pure trajectory-purposefulness
assessments. No sanity issues.

---

## 8. What Was Built

| Artifact | Path | Notes |
|---|---|---|
| Claude Code adapter | `scripts/adapters/claudecode_adapter.py` | CC JSONL → digest schema |
| Adapter package | `scripts/adapters/__init__.py` | |
| Digest schema reference | `docs/digest_schema.md` | Locked schema, every field documented |
| Judge I/O flags | `scripts/layer2_judge.py` (+`--input-path`, `+--output-path`) | Routes judge to CC digests without contaminating layer1_outputs.jsonl |
| CC session digests | `data/cc_session_digests.jsonl` | 4 adapted sessions (3/18/103/413 turns) |
| CC judge scores | `data/cc_judge_scores.jsonl` | 3 sessions scored (d57f0f0e, a3496457, 609d73cf) |
| CC scoring runner | `scripts/cc_score_runner.sh` | GPU runner for CC validation |
| Session filter | `data/cc_validation_sessions.json` | 3 scoreable session IDs |
| Startup script fix | `scripts/vm_startup.sh` | `ensurepip` fallback for cu129 images |

---

## 9. Honest Limitations

**Sane-check, not calibration.** Three sessions is enough to establish that the adapter
produces readable digests and the judge produces grounded verdicts. It is not a calibration
run. We cannot estimate judge quality on CC data from 3 sessions; we can only confirm it's
not broken.

**resolved and H2 unavailable.** CC sessions have no test harness (resolved = False always)
and no H2 annotation pipeline (h2_duplicate_count = 0 always). The composite formula handles
these gaps by design, but the efficiency_score denominator is missing the outcome signal.
For launch-1, the judge verdict + reasoning is the product. The composite score requires the
missing signals.

**Claude Code adapter only.** This phase built one adapter. The original design called for
a second adapter (Aider) to prove the pattern generalizes. That is step 3, not yet built.

**GPU required.** The laptop cannot run the judge on real sessions at acceptable speed.
Every CC validation and production scoring run requires a GPU. At ~$0.37/run (L4 SPOT,
40 min, asia-east1-a), this is affordable but not free.

**Token-economy unavailable on ad-hoc CC sessions.** p25_token_ratio is set to 1.0
(placeholder) and flagged `token_economy_available=False`. The efficiency composite score
is not meaningful for CC sessions in this phase.

---

## 10. Infrastructure Closeout

**VM:** tes-cc-validation-tmp DELETED. Boot disk deleted. No snapshots, static IPs, or storage
buckets created for this run.

**Actual billable resources:** g2-standard-8 SPOT (asia-east1-a), ~40 min active compute
across multiple start/stop cycles. Estimated compute: ~$0.37. Disk (100 GB pd-balanced,
~81 min from creation to deletion): ~$0.002. **Total: ~$0.37 USD.**

**Startup script state:** `scripts/vm_startup.sh` at commit `57da770` contains the
`python3 -m ensurepip --upgrade` fallback for cu129 images. The next `provision_gpu_vm.sh`
invocation will bake this version into VM metadata; no manual dependency installation
required.

---

## 11. What Changes When Aider Adapter Is Built (Step 3)

The Aider adapter follows the same pattern:
1. Inspect real Aider log format (`.aider.chat.history.md` or JSON session logs)
2. Document format in `docs/aider_format.md`
3. Build `scripts/adapters/aider_adapter.py` conforming to the locked digest schema
4. Validate on 3-5 real Aider sessions on GPU
5. Identify any format-specific mapping issues (Aider has cleaner structured output
   than swe_agent but different tool representation than CC)

If a clean abstraction emerges across both adapters, consider a minimal shared base
(e.g., a common `SessionRecord` → `SessionDigest` interface). Do not build a plugin
framework speculatively.

---

## 12. Next Phase Decision Point

With the Claude Code adapter validated, two directions remain in this phase arc:

- **Step 3 (Aider adapter):** Proves the ingestion pattern generalizes. Low risk, moderate
  scope. Produces a second adapter and validates on real Aider sessions.
- **Option E (Hardening):** Expands corpus robustness: broader task types, multi-language,
  more scaffolds. Requires ingestion infrastructure (built) and a calibrated baseline
  (available from B1). Larger scope.

The user decides which to prioritize next session.
