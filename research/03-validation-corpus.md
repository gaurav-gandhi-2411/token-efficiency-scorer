# Report 03 — Validation Corpus: Methodology, Heuristic Results, Taxonomy, and Difficulty Normalization

**Author:** Gaurav Gandhi  
**Date:** 2026-05-19  
**Status:** Phase 2 complete (structural GT); Phase 2 LLM annotation pending API key

---

## 0. Executive Summary

We assembled a 200-session validation corpus from three public SWE-bench trajectory datasets across three agent scaffolds. We generated structural ground truth for two deterministic heuristics (H1, H2) and validated them. H1 and H2 implementations are self-consistent by construction — their "perfect" F1 scores reflect algorithmic identity between heuristic and GT generator, not generalization. H3 and H4 cannot be validated without LLM annotation. Task taxonomy reveals domain-level resolve-rate variance of 14%–100%, while four structural difficulty proxies individually explain near-zero variance in per-session resolve probability (AUC 0.492–0.513). We conclude that **domain is the only reliable structural difficulty signal**; individual structural proxies are insufficient as difficulty normalizers.

---

## 1. Corpus Sourcing

### 1.1 Source Datasets

| Dataset | HuggingFace Path | License | Scaffold | Split |
|---------|-----------------|---------|----------|-------|
| SWE-agent trajectories | `nebius/SWE-agent-trajectories` | CC-BY-4.0 | `swe_agent` | `train` |
| OpenHands rebench (Nebius) | `nebius/SWE-rebench-openhands-trajectories` | CC-BY-4.0 | `openhands_nebius` | `train` |
| OpenHands sampled (SWE-Gym) | `SWE-Gym/OpenHands-Sampled-Trajectories` | CC-BY-4.0 | `openhands_swegym` | `train.raw` |

All three datasets derive from SWE-bench Verified (arXiv:2310.06770 [Jimenez et al., 2023]), which contains 500 curated, human-verified GitHub issues from real Python repositories. SWE-bench Verified eliminates the 19.71% false-positive rate identified in SWE-ABS (arXiv:2603.00520 [Yang et al., 2026]).

### 1.2 Sampling Protocol

Target: 200 sessions, ≥3 scaffolds, ≥2 base models, balanced resolved/unresolved.

| Scaffold | Resolved | Unresolved | Total | Model(s) |
|----------|----------|------------|-------|----------|
| `swe_agent` | 40 | 40 | 80 | gpt-4o, gpt-4o-mini |
| `openhands_nebius` | 35 | 35 | 70 | gpt-4o-mini, claude-3-5 |
| `openhands_swegym` | 25 | 25 | 50 | claude-3-7, gpt-4o |
| **Total** | **100** | **100** | **200** | 4 distinct models |

Reservoir sampling seeded at `random.seed(42)` via HuggingFace `datasets` streaming API. Script: `scripts/00_download_corpus.py`.

### 1.3 Normalization Schema

Each session is stored as:
```json
{
  "session_id": "<sha256 of instance_id+scaffold+model>",
  "scaffold": "swe_agent | openhands_nebius | openhands_swegym",
  "instance_id": "<org>__<repo>-<issue_number>",
  "model": "<model_name>",
  "outcome": {"result": "pass|fail", "patch_diff": "..."},
  "turns": [
    {
      "turn_index": 0,
      "role": "system|user|assistant|ai|tool",
      "content_text": "...",
      "tool_uses": [{"tool_name": "...", "tool_input": {...}, "tool_result": "..."}]
    }
  ],
  "session_token_totals": {"input": int, "output": int, "cache_read": int, "total": int},
  "turn_count": int
}
```

**Known schema gap:** SWE-agent embeds tool calls as formatted text in the `ai` turn body, not as structured JSON `tool_use` objects. Consequently, `tool_uses` is always empty for `swe_agent` sessions (n=80), limiting heuristic H1 and H2 to OpenHands sessions (n=120).

Corpus statistics (script: `scripts/debug_stats.py`):
- Sessions: 200
- Median turn count: 40 (p25=19, p75=113)
- Token coverage: 200/200 have input tokens; 120/200 have output tokens (swe_agent logs 0 output tokens)

---

## 2. Annotation Protocol

### 2.1 Ground Truth Strategy

| Heuristic | GT Source | Method | Status |
|-----------|-----------|--------|--------|
| H1 (is_retry) | Structural | Deterministic: exact (tool_name, normalized_input) repeat | Complete |
| H2 (redundant_read) | Structural | Deterministic: file path re-read with no write in between | Complete |
| H3 (is_backtrack) | LLM (Haiku) | Claude Haiku + Sonnet verification (20% overlap) | Pending |
| H4 (tool_result_used) | LLM (Haiku) | Claude Haiku + Sonnet verification (20% overlap) | Pending |

LLM annotation (script: `scripts/01_annotate_corpus.py`) requires `ANTHROPIC_API_KEY`, which was not available during this phase. Estimated cost: ~$0.03/session × 200 sessions = $6 USD for Haiku + Sonnet 20% verification.

### 2.2 Structural GT Generation

Script: `scripts/02b_generate_structural_gt.py`

**H1 (is_retry):** A turn is labeled positive if the same `(tool_name, json.dumps(tool_input, sort_keys=True))` key was used in any prior turn of the same session.

**H2 (redundant_read):** A turn is labeled positive if:
- A file path `P` is extracted from a read-type tool call, AND
- `P` appears in `last_read_turn`, AND
- `last_write_turn.get(P, -1) <= last_read_turn[P]` (no write since last read)

Critical disambiguation for `str_replace_editor`: calls with `command=view/open/scroll_down/scroll_up` are reads; all other commands are writes. This distinction prevents every `str_replace_editor` invocation from resetting the "last write" counter.

**Structural GT statistics (6,822 agent turns across 200 sessions):**

| Label | Positive | Rate |
|-------|----------|------|
| H1 is_retry | 592 | 8.7% |
| H2 redundant_read | 737 | 10.8% |
| H3 is_backtrack | — (no structural GT) | — |
| H4 tool_result_used | — (no structural GT) | — |

### 2.3 Inter-Annotator Agreement

IAA via Cohen's kappa is planned for the LLM annotation phase (threshold: κ ≥ 0.60). Target: 40-session overlap between Haiku (bulk) and Sonnet (verification). Not yet computed.

---

## 3. Heuristic Validation Results

Script: `scripts/02_validate_heuristics.py`

### 3.1 Results Table

| Heuristic | TP | FP | FN | TN | P | R | F1 | Status |
|-----------|----|----|----|----|---|---|----|--------|
| H1 is_retry | 592 | 0 | 0 | 6,230 | 1.000 | 1.000 | 1.000 | READY* |
| H2 redundant_read | 737 | 0 | 0 | 6,085 | 1.000 | 1.000 | 1.000 | READY* |
| H3 is_backtrack | 0 | 146 | 0 | 6,676 | 0.000 | 0.000 | 0.000 | PENDING |
| H4 tool_result_used | 0 | 1,387 | 0 | 5,435 | 0.000 | 0.000 | 0.000 | PENDING |

### 3.2 H1 and H2: Circular Validation Caveat

**H1 and H2 F1=1.000 is a circular validation artifact.** The structural GT generator (`02b_generate_structural_gt.py`) and the heuristic implementation (`02_validate_heuristics.py`) use functionally identical algorithms for H1 and H2:

- Both H1 implementations: maintain a dict keyed by `(tool_name, normalized_input)`; mark positive if key seen before.
- Both H2 implementations: track `last_read_turn[path]` and `last_write_turn[path]`; mark positive if `last_write <= last_read`.

The only implementation differences between GT and heuristic are cosmetic (variable names, ordering of passes). Consequently, perfect agreement is expected and tells us nothing about real-world precision or recall against human-labeled data.

**What the perfect scores do establish:**
1. The H1 and H2 logic is internally consistent — the implementations agree on 100% of the 6,822 turns they both process.
2. The heuristic is deterministic and reproducible: re-running it on the same data produces identical results.

**What they do not establish:**
- Whether H1 catches all subjectively meaningful retries (a human might require an error in the prior call's result).
- Whether H2 catches all cases where re-reading was genuinely wasteful vs. intentional (e.g., checking a file changed by an external tool).

**Implication for v1 architecture:** H1 and H2 should be treated as "lower-bound validated" — they implement the defined algorithm correctly, but human recall and precision remain unknown until LLM annotation completes.

### 3.3 H3: is_backtrack — Regex Coverage

The H3 heuristic fires 146 times (2.1% of turns) on the 200-session corpus. Without ground truth, we cannot compute precision or recall. However, the firing count gives an upper bound on prevalence: backtracking occurs in at most 2.1% of turns if the regex has perfect precision, or somewhat more if it has false negatives.

The regex patterns cover explicit self-correction phrases ("let me try a different approach", "scratch that", "never mind"). They will miss implicit backtracks where the agent silently tries a new strategy without acknowledging the previous attempt.

### 3.4 H4: tool_result_used — High False Positive Rate

The H4 heuristic marks 1,387 turns (20.3%) as positive. The high count reflects the "vacuous true" fallback — turns with no tool calls are always labeled as `tool_result_used=True`. When restricted to turns with at least one tool call, the firing rate is lower. Without LLM ground truth we cannot distinguish true positives (result genuinely referenced) from false positives (result present but ignored).

---

## 4. Task Taxonomy

Script: `scripts/03_task_taxonomy.py`

### 4.1 Domain Classification

Domains are assigned by matching the repository portion of `instance_id` (`org__repo-N` → `repo-N`) against a keyword map (63 keywords across 8 categories). Sessions with no match are labeled `unknown`.

| Domain | n | Resolve Rate | Label |
|--------|---|--------------|-------|
| lib_general | 44 | 59% | General-purpose library |
| type_checker | 42 | 14% | Type-checker / linter |
| unknown | 31 | 61% | Uncategorised |
| data_ml | 26 | 42% | Data / ML / scientific |
| cloud_devops | 25 | 48% | Cloud / DevOps / infra |
| graph_geo | 20 | 95% | Graph / geo / visualization |
| db_orm | 7 | 43% | DB / ORM / data-layer |
| web_api | 3 | 100% | Web / REST API |
| testing_ci | 2 | 50% | Testing / CI tools |

**Notable finding:** `type_checker` tasks (mypy, pyflakes, cognitive_complexity) have a 14% resolve rate — nearly 5× lower than the corpus mean (50%) and 7× lower than `graph_geo` (95%). This domain-level variance is the dominant difficulty signal in the corpus.

### 4.2 Patch Structure Distribution

| Patch Type | n | % |
|------------|---|---|
| single_file | 98 | 49% |
| no_patch | 58 | 29% |
| multi_file | 44 | 22% |

The 58 "no_patch" sessions are predominantly unresolved: the agent either produced no diff or an empty diff. Note that patch structure is an **outcome** variable, not a task input variable — it cannot be used as a prospective difficulty signal.

---

## 5. Difficulty Normalization Analysis

Script: `scripts/04_difficulty_analysis.py`

### 5.1 Structural Proxy Performance

We tested four structural proxies against the binary resolve label (0/1). All four were evaluated using point-biserial correlation (r_pb), AUC (Wilcoxon-Mann-Whitney), and McFadden pseudo-R².

| Proxy | r_pb | AUC | McFadden R² | Direction |
|-------|------|-----|-------------|-----------|
| P1: Patch lines (add+del) | −0.045 | 0.504 | 0.001 | higher = resolved |
| P2: Files changed | −0.006 | 0.510 | 0.000 | higher = resolved |
| P3: Session turn count | −0.081 | 0.492 | 0.004 | higher = harder |
| P4: Task description words | +0.016 | 0.513 | 0.000 | higher = harder |

All four proxies perform at or below chance (AUC ≈ 0.50). **No structural proxy explains per-session resolve variance.**

### 5.2 Why Structural Proxies Fail

**Confounding by outcome:** P1 (patch lines) and P2 (files changed) measure the agent's output, not the task's input difficulty. A larger patch means the agent succeeded more comprehensively — P1 × P3 Pearson r = 0.715, confirming that turn count and patch size both measure session productivity, not task difficulty.

**Signal dilution by domain:** Domain-level resolve rates span 14%–100%. Within each domain, per-instance variance is much smaller. The structural proxies cannot distinguish easy from hard instances within a domain because within-domain variance is dominated by agent non-determinism and prompt sensitivity, not structural task features.

### 5.3 Recommended Difficulty Normalization for v1

Given the failure of structural proxies, the v1 architecture should use **domain-level empirical difficulty calibration**:

```
difficulty_norm(session) = resolve_rate_prior(session.domain)
```

Where `resolve_rate_prior` is the domain's historical average resolve rate across the corpus. This is a cheap, interpretable, and empirically grounded normalizer that captures the dominant source of between-session variance. It can be updated as more trajectories are processed.

For sessions in the `unknown` domain (31 sessions, 61% resolve rate), fall back to the corpus-wide mean (50%).

**Limitation:** This calibration is computed on a 200-session corpus with significant per-domain imbalance (web_api n=3, testing_ci n=2). Domains with <10 sessions should be treated with caution. A production deployment would need ≥30 sessions per domain to stabilize these priors.

---

## 6. Open Gaps and Next Steps

| Gap | Severity | Mitigation |
|-----|----------|------------|
| LLM annotation (H3, H4) not run | High | Run `01_annotate_corpus.py` with `ANTHROPIC_API_KEY` set; ~$6 USD |
| H1/H2 human validation not done | Medium | Sample 50 turns per heuristic for manual review |
| SWE-agent tool_uses not parsed | Medium | Parse `ai` turn text blocks to extract structured tool calls |
| Domain coverage: 31 unknowns | Low | Extend keyword map; or accept 16% uncategorised rate |
| IAA kappa not computed | Low | Blocked on LLM annotation |
| web_api, testing_ci n<10 | Low | Note instability in domain priors; do not report as findings |

---

## 7. Self-Critique

**What worked:**
- Structural GT generation is cheap, reproducible, and correctly handles the `str_replace_editor` read/write ambiguity.
- Domain-level difficulty calibration is a practical and empirically grounded v1 approach.
- The corpus covers 3 scaffolds and 4 models with balanced outcomes.

**What fell short:**
- H1/H2 "perfect scores" are a methodological artifact, not a scientific result. This should have been caught before running the validation script — the GT generator and heuristic were written by the same author with the same algorithm.
- SWE-agent trajectory structure (tool calls embedded in text) was discovered late. A pre-corpus audit of raw HuggingFace field schemas would have surfaced this earlier.
- All four structural proxies had AUC ≈ 0.50. We should have suspected this given that patch_diff is an outcome variable, not a feature. A more principled approach would have been to measure difficulty from the task *description* (issue text embedding similarity to "easy" vs "hard" prior examples) rather than from the solution.

**Revised claim for report 02:** The difficulty normalization section of report 02 recommended using "lines changed" and "files touched" as difficulty proxies (§5.1). These proxies are invalid — they measure outcome, not difficulty. The recommended normalization for v1 is domain-level empirical calibration as described in §5.3 above.

---

## 8. Reproducibility

All scripts are in `scripts/`. To reproduce the full pipeline:

```bash
# 1. Download corpus (requires HuggingFace internet access)
python scripts/00_download_corpus.py

# 2. Generate structural GT (no API key needed)
python scripts/02b_generate_structural_gt.py

# 3. Validate heuristics against structural GT
python scripts/02_validate_heuristics.py

# 4. Classify task domains
python scripts/03_task_taxonomy.py

# 5. Analyze difficulty proxies
python scripts/04_difficulty_analysis.py

# 6. (Optional, requires ANTHROPIC_API_KEY) LLM annotation for H3/H4
# ANTHROPIC_API_KEY=your-key python scripts/01_annotate_corpus.py --model both
```

Output directories:
- `data/validation-corpus/traces_normalized/` — 200 normalized JSON sessions
- `data/validation-corpus/annotations/structural_gt/` — H1/H2 ground truth (200 files)
- `data/validation-corpus/heuristic_results/` — validation metrics + failure cases
- `data/validation-corpus/taxonomy/` — domain classifications
- `data/validation-corpus/difficulty/` — proxy analysis

**Environment:** Python 3.13 (conda base), packages: `datasets`, `anthropic`, `huggingface-hub`. No GPU required. Runtime: ~3 min for corpus download (bandwidth-dependent); <10 sec for all other scripts.

---

## References

1. Jimenez, C. E., et al. (2023). "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" arXiv:2310.06770
2. Yang, J., et al. (2026). "SWE-ABS: Unifying the Evaluation of AI Software Engineering Agents." arXiv:2603.00520
3. Tang, X., et al. (2024). "AgentDiet: Improving the Efficiency of LLM-based Agents via Diet Strategy." arXiv:2509.23586 *(trajectory waste taxonomy)*
4. Wang, P., et al. (2024). "Math-Shepherd: Verify and Reinforce LLMs Step-by-step without Human Annotations." arXiv:2312.08935 *(Monte Carlo process supervision)*
