# Phase A.1 — Heuristic Remeasurement on LLM-Labeled Corpus

**Status:** PENDING ANNOTATION RUN  
**Date:** TBD (populate after running `01_annotate_corpus.py`)  
**Budget ceiling:** $5 USD total ($4 GPT-OSS + $2 Sonnet IAA hard limits)

---

## 1. Methodology

### 1.1 Corpus

- **200 sessions** from `data/validation-corpus/traces_normalized/`
- Source: nebius/SWE-agent-trajectories (CC-BY-4.0), normalized in Phase 2
- Scaffolds: swe\_agent, claude\_code, codecompass, agentless
- Outcomes: pass/fail mix (see Phase 2 corpus audit)

### 1.2 Annotation Protocol

**Bulk annotation (200 sessions):**  
Model: `openai/gpt-oss-120b` via Groq (OpenAI-compatible endpoint).  
Temperature: 0 for determinism. JSON mode enforced.  
One API call per session; all turns annotated in a single response.  
Context window: 131,072 tokens. Sessions exceeding this are skipped and logged.  
Prompt: session transcript + rubric with positive/negative examples (see `scripts/01_annotate_corpus.py`).

**IAA annotation (25 sessions, seed=42):**  
Model: `claude-sonnet-4-6` via Anthropic API.  
Same prompt as bulk pass. Ephemeral cache\_control applied to system rubric block.  
IAA subset selected via `random.Random(42).sample(all_ids, k=25)`.

**Output fields (prefixed `llm_`):**

| Field | Type | Description |
|---|---|---|
| `llm_h1_redundant_read` | bool | File read is redundant (same path, no intervening edit or failure) |
| `llm_h2_duplicate_message` | bool | Turn is >90% identical to a prior assistant turn |
| `llm_h3_backtrack` | bool | Agent reverses prior approach after evidenced failure |
| `llm_h4_tool_result_used` | bool | Tool result is incorporated in subsequent reasoning |
| `llm_total_waste_pct` | int | Estimated % of tokens wasted this session |
| `llm_waste_categories` | list | Qualitative waste taxonomy labels |

### 1.3 Heuristic–Label Mapping

| Phase A.1 Label | LLM Field | Evaluated Against |
|---|---|---|
| H1 redundant\_read (original) | `llm_h1_redundant_read` | `compute_h1_orig_redundant_read` |
| H1 redundant\_read (revised) | `llm_h1_redundant_read` | `compute_h1_revised_redundant_read` |
| H2 duplicate\_message | `llm_h2_duplicate_message` | `compute_h2_duplicate_message` |
| H3 backtrack | `llm_h3_backtrack` | `compute_h3_backtrack` |
| H4 tool\_result\_used | `llm_h4_tool_result_used` | `compute_h4_tool_result_used` |

**Ground-truth caveat:** LLM labels are not gold-standard truth. All F1 scores
are upper-bounded by the IAA kappa between GPT-OSS and Sonnet; both are reported.

---

## 2. Annotation Cost

**(Populate after running `01_annotate_corpus.py`)**

| Provider | Model | Sessions | Input tokens | Output tokens | Cost (USD) |
|---|---|---|---|---|---|
| Groq | openai/gpt-oss-120b | 200 | TBD | TBD | TBD |
| Anthropic | claude-sonnet-4-6 | 25 | TBD | TBD | TBD |
| **Total** | | | | | **TBD** |

- Sessions skipped (context exceeded 131k): TBD
- Pre-flight projected vs actual variance: TBD

---

## 3. Inter-Annotator Agreement (IAA)

**(Populate from `data/validation-corpus/annotations/iaa_phaseA1.json`)**

| Label | GPT-OSS pos rate | Sonnet pos rate | Cohen's κ | Agreement |
|---|---|---|---|---|
| H1 redundant\_read | TBD | TBD | TBD | TBD |
| H2 duplicate\_message | TBD | TBD | TBD | TBD |
| H3 backtrack | TBD | TBD | TBD | TBD |
| H4 tool\_result\_used | TBD | TBD | TBD | TBD |
| **Mean** | | | TBD | |

Threshold: κ ≥ 0.6 = substantial agreement (acceptable ground truth).  
Labels with κ < 0.6 flagged for manual review of 10 disputed turns.

### 3.1 Low-κ Analysis (if applicable)

**(Populate if any label has κ < 0.6)**

**Label: [TBD]** — κ = TBD  
Systematic disagreement: TBD  
Proposed rubric fix: TBD  
Evidence (10 hand-annotated disputed turns): see `evals/fixtures/iaa_disputes_[label].json`

---

## 4. Heuristic F1 Remeasurement

**(Populate from `data/validation-corpus/heuristic_results/phaseA1_results.json`)**

### 4.1 Results Table

| Heuristic | TP | FP | FN | TN | Precision | Recall | F1 | IAA κ | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| H1 orig redundant\_read | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| H1 revised redundant\_read | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| H2 duplicate\_message | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| H3 backtrack | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| H4 tool\_result\_used | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

Verdicts: **PRODUCTION\_READY** (F1 ≥ 0.7) · **SALVAGEABLE** (0.5–0.7) · **DEAD** (< 0.5)

### 4.2 Confusion Matrices

#### H1 Original Redundant Read
```
              Predicted +    Predicted −
Actual +      TP=TBD         FN=TBD
Actual −      FP=TBD         TN=TBD
```

#### H1 Revised Redundant Read
```
              Predicted +    Predicted −
Actual +      TP=TBD         FN=TBD
Actual −      FP=TBD         TN=TBD
```

#### H2 Duplicate Message
```
              Predicted +    Predicted −
Actual +      TP=TBD         FN=TBD
Actual −      FP=TBD         TN=TBD
```

#### H3 Backtrack
```
              Predicted +    Predicted −
Actual +      TP=TBD         FN=TBD
Actual −      FP=TBD         TN=TBD
```

#### H4 Tool Result Used
```
              Predicted +    Predicted −
Actual +      TP=TBD         FN=TBD
Actual −      FP=TBD         TN=TBD
```

### 4.3 Failure Mode Examples

**(5 FP + 5 FN per heuristic, see `data/validation-corpus/heuristic_results/phaseA1_failures/`)**

#### H1 — Redundant Read Failure Modes

**False Positives (heuristic fired, LLM said NOT redundant):**
1. TBD
2. TBD
3. TBD
4. TBD
5. TBD

**False Negatives (heuristic missed, LLM said IS redundant):**
1. TBD
2. TBD
3. TBD
4. TBD
5. TBD

#### H2 — Duplicate Message Failure Modes
*(TBD after annotation run)*

#### H3 — Backtrack Failure Modes
*(TBD after annotation run)*

#### H4 — Tool Result Used Failure Modes
*(TBD after annotation run)*

---

## 5. H1 Revision Analysis

Original H1 fired on any same-path file read without intervening write — no
failure gate, no content-hash check. This caused the previously-observed
kappa=0.066 against human labels (spurious redundancy on justified re-reads).

**Revised H1 requirements:**
1. Same file path read at two turns
2. No write/edit to that path between the two reads
3. Tool result hash identical at both reads (content unchanged)
4. No failure/error/test-fail between the two reads (no legitimate re-check reason)

**Result:**

| Version | F1 | Delta |
|---|---|---|
| Original H1 | TBD | — |
| Revised H1 | TBD | TBD |

Decision: **TBD** (lock revised H1 if delta > 0.02, else flag for A.2 redesign)

---

## 6. Verdicts and Phase A.2 Scope

| Heuristic | F1 | κ | Verdict | Recommendation |
|---|---|---|---|---|
| H1 redundant\_read (orig) | TBD | TBD | TBD | TBD |
| H1 redundant\_read (revised) | TBD | TBD | TBD | TBD |
| H2 duplicate\_message | TBD | TBD | TBD | TBD |
| H3 backtrack | TBD | TBD | TBD | TBD |
| H4 tool\_result\_used | TBD | TBD | TBD | TBD |

### Recommended Phase A.2 Scope

**(To be filled after results are in)**

Based on the verdicts above, Phase A.2 should prioritize:
1. TBD — full redesign candidates (DEAD heuristics)
2. TBD — targeted fixes (SALVAGEABLE heuristics with clear failure mode)
3. TBD — PRODUCTION\_READY heuristics to ship as-is

DEAD heuristics should be redesigned using semantic approaches (embeddings,
LLM-judge-per-turn) rather than regex/string-matching fixes in A.2.
