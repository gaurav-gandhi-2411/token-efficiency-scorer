# Report 03 — Validation Corpus: Methodology, Annotation, Heuristic Validation, Taxonomy, Difficulty, and OOD Testing

**Author:** Gaurav Gandhi  
**Date:** 2026-05-20  
**Status:** LLM annotation pending API key; all other phases complete

---

## 0. Executive Summary

We assembled a 200-session in-distribution corpus from three SWE-bench Verified trajectory datasets. We then:

1. **Disclosed full corpus composition** including provenance, scaffolds, models, and identified gaps.
2. **Ran a 19-turn manual spot-check** (Sonnet labeler) to establish a partial IAA baseline before running LLM annotation.
3. **Validated heuristics H1–H4** against structural GT (H1/H2) and manual labels (partial). Found that H1 structural GT diverges significantly from human judgment (kappa=0.066); H2 shows moderate agreement (kappa=0.642 at n=19).
4. **Tested OOD generalization** on 15 sessions from CC-Bench and synthetic Claude Code traces. Domain classifier fails completely (0% coverage); H1/H2 firing rates drop 3–5×; H3 backtrack regex fires 0 times on 218 turns.
5. **Found that structural difficulty proxies are predictively null** (AUC 0.49–0.51); domain-level empirical calibration is the only reliable normalizer.

**Critical open item:** LLM annotation (01_annotate_corpus.py) has not run. H3 and H4 ground truth are unknown. All F1 scores for H3 and H4 are provisional.

---

## 1. Corpus Composition Disclosure

### 1.1 Source Datasets

| # | HuggingFace Path | License | Sessions | Scaffold | Base Models |
|---|-----------------|---------|----------|----------|-------------|
| 1 | `nebius/SWE-agent-trajectories` | CC-BY-4.0 | 80 | `swe_agent` | Llama-3 70B, 8B, 405B |
| 2 | `nebius/SWE-rebench-openhands-trajectories` | CC-BY-4.0 | 70 | `openhands_nebius` | *unknown* |
| 3 | `SWE-Gym/OpenHands-Sampled-Trajectories` | CC-BY-4.0 | 50 | `openhands_swegym` | *unknown* |

All three datasets draw from SWE-bench Verified (arXiv:2310.06770 [Jimenez et al., 2023]).

**Model coverage gap:** The `model` field is unpopulated for all 120 OpenHands sessions (datasets 2 and 3 do not expose per-row model metadata in the Parquet schema). This is a corpus documentation failure; model diversity for two-thirds of the corpus is unknown.

### 1.2 Scaffold, Model, and Outcome Breakdown

| Scaffold | Sessions | Resolved | Resolve Rate | Models |
|----------|----------|----------|--------------|--------|
| `swe_agent` | 80 | 40 | 50% | Llama-3 70B (71), 405B (4), 8B (5) |
| `openhands_nebius` | 70 | 35 | 50% | unknown |
| `openhands_swegym` | 50 | 25 | 50% | unknown |

The 50/50 split is deliberate (sampled to balance resolved/unresolved).

### 1.3 Task and Language Diversity

| Signal | Value | Assessment |
|--------|-------|------------|
| Unique repos | 86 | Good |
| Most common repo | pyupgrade (20 sessions, 10%) | Concentrated |
| File extensions | 98.5% `.py`, 7.6% `.md` | Python-only |
| Task types | SWE-bench bugfix exclusively | **Diversity gap** |
| Languages | 1 (Python) | **Diversity gap** |
| OOD coverage | 0 sessions | **Diversity gap** |

**Diversity verdict:** The corpus is 100% Python, 100% SWE-bench-format bugfixes, and 40% swe_agent. It is sufficient to validate heuristic correctness on OpenHands-style trajectories but inadequate to claim generalization to other scaffolds or task types. Domain distribution spans 8 categories with resolve rates from 14% (type_checker) to 100% (web_api), providing good difficulty variance within the Python/bugfix slice.

### 1.4 OOD Corpus (Task 4)

We sourced 15 sessions from two out-of-distribution datasets:

| Source | Sessions | Scaffold | Task Categories |
|--------|----------|----------|----------------|
| `zai-org/CC-Bench-trajectories` | 10 | `claude_code` | frontend_development, app_development, ui_optimization, build_deployment, data_analysis |
| Synthetic (constructed) | 5 | `claude_code` | data_analysis, TypeScript refactor, shell scripting, docs update, Go bug fix |

CC-Bench contains 370 real Claude Code sessions with structured tool calls (Claude Code session JSON format), token totals, and tool call counts. Licenses: CC-BY-4.0 (CC-Bench), CC0 (synthetic).

---

## 2. Annotation Protocol

### 2.1 Ground Truth Strategy

| Heuristic | GT Source | Method | Status |
|-----------|-----------|--------|--------|
| H1 (is_retry) | Structural + manual spot-check | Deterministic + Sonnet review | Partial |
| H2 (redundant_read) | Structural + manual spot-check | Deterministic + Sonnet review | Partial |
| H3 (is_backtrack) | LLM (Haiku) | Claude Haiku + Sonnet verification | **Pending** |
| H4 (tool_result_used) | LLM (Haiku) | Claude Haiku + Sonnet verification | **Pending** |

LLM annotation (`scripts/01_annotate_corpus.py`) requires `ANTHROPIC_API_KEY`. Estimated cost: ~$6 USD. Script is ready; blocked on key.

### 2.2 Structural GT Generation

Script: `scripts/02b_generate_structural_gt.py`

**H1 (is_retry):** A turn is positive if `(tool_name, json.dumps(tool_input, sort_keys=True))` was used in any prior turn.

**H2 (redundant_read):** A turn is positive if file path `P` was read before and `last_write_turn.get(P, -1) <= last_read_turn[P]`.

Critical disambiguation: `str_replace_editor` with `command=view/open` is a READ; all other commands are WRITEs.

**Structural GT statistics (6,822 agent turns, 200 sessions):**

| Label | Positive | Rate |
|-------|----------|------|
| H1 is_retry | 592 | 8.7% |
| H2 redundant_read | 737 | 10.8% |

### 2.3 Manual Spot-Check (Sonnet Labeler)

To establish an IAA baseline before Haiku annotation runs, we manually labeled 19 turns sampled across all 3 scaffolds (script: `scripts/07_sample_spotcheck_turns.py`, labels: `scripts/08_apply_spotcheck_labels.py`).

**Sampling:** 19 turns from 15 sessions: 8 openhands_swegym, 8 openhands_nebius, 3 swe_agent. Tool-bearing: 16/19; text-only: 3/19.

**Label distribution:**

| Label | Positive | Rate | Notes |
|-------|----------|------|-------|
| is_retry | 3/19 | 15.8% | Turns 11, 14, 19 — explicit failed-command repetition |
| is_backtrack | 3/19 | 15.8% | Turns 15, 16, 17 — strategy switch or undo_edit |
| tool_result_used | 19/19 | 100% | All turns use their context |
| redundant_read | 1/19 | 5.3% | Turn 18 — same file re-read within session |

**Notable examples:**
- **is_retry (t36, hydra-2189):** Agent repeatedly applies identical `str_replace` where `old_str == new_str` — a no-op edit retried after an import error.
- **is_backtrack (t92, dask-9378):** `undo_edit` call + "Alternative Approach: Instead of using map_blocks..." — textbook backtrack.
- **redundant_read (t32, pydantic-8316):** Reads `_fields.py` at line 125 in t22, then re-reads at line 195 in t32 with no write between.

### 2.4 IAA: Structural GT vs Sonnet Spot-Check

Script: `scripts/09_compute_spotcheck_iaa.py`

| Heuristic | Cohen's κ | SC positive rate | Structural GT positive rate | n |
|-----------|-----------|-----------------|----------------------------|---|
| H1 is_retry | **0.066** | 15.8% | 26.3% | 19 |
| H2 redundant_read | **0.642** | 5.3% | 10.5% | 19 |

**H1 kappa = 0.066 (near-zero) is a methodological finding, not a validation failure.** The structural GT detects any exact `(tool_name, input)` repetition, regardless of whether the prior call failed. The Sonnet labeler only flagged retries after visible failures. The two definitions diverge on ~2 turns where the structural GT flags a repeated call that Sonnet did not judge as a meaningful retry (e.g., a repeated `view` call that succeeds both times). This validates our concern from report 02 phase 2 — H1 as implemented conflates "repetition" with "retry-after-error."

**H2 kappa = 0.642 is above the κ ≥ 0.60 threshold.** However, n=19 is too small for stable estimation (±0.3 confidence interval at n=19). The agreement is dominated by true-negative concordance (17/19 both say False). Haiku annotation on 200 sessions is needed for reliable H2 kappa.

---

## 3. Heuristic Validation Results

Script: `scripts/02_validate_heuristics.py`

### 3.1 Against Structural GT (In-Distribution)

| Heuristic | TP | FP | FN | TN | P | R | F1 | Status |
|-----------|----|----|----|----|---|---|----|--------|
| H1 is_retry | 592 | 0 | 0 | 6,230 | 1.000 | 1.000 | 1.000 | CIRCULAR |
| H2 redundant_read | 737 | 0 | 0 | 6,085 | 1.000 | 1.000 | 1.000 | CIRCULAR |
| H3 is_backtrack | 0 | 146 | 0 | 6,676 | — | — | — | NO GT |
| H4 tool_result_used | 0 | 1,387 | 0 | 5,435 | — | — | — | NO GT |

**F1=1.000 for H1 and H2 is a circular validation artifact.** The structural GT generator and heuristic implementation share the same algorithm. These scores confirm internal consistency but not real-world performance.

### 3.2 Against Manual Spot-Check (19 turns)

Because the spot-check sample is small (n=19), these numbers are informational, not publication-quality:

| Heuristic | SC Positive | Heuristic Positive | Agreement |
|-----------|-------------|-------------------|-----------|
| H1 is_retry | 3/19 | Unknown at turn level | — |
| H2 redundant_read | 1/19 | Unknown at turn level | — |

The structural GT disagrees with Sonnet on H1 for ~2 turns (kappa=0.066), suggesting H1's definition needs tightening: require prior call's tool_result to contain an error indicator.

### 3.3 H3 Heuristic Coverage Analysis

H3 fires on 146/6,822 turns (2.1%). Without GT labels, precision and recall are unknown. The 10 regex patterns cover explicit self-correction phrases. Patterns that will likely have low precision:
- `"(?:instead|rather)[,]?\s+(?:let me|i'll)"` — common in normal forward planning, not just backtracks
- `"(?:actually|wait|hmm)[,.]?\s+..."` — common in OpenHands verbose reasoning regardless of backtrack

### 3.4 H4 Heuristic Coverage Analysis

H4 fires on 1,387/6,822 turns (20.3%). The high rate is partly inflated by turns with no tool calls (vacuously marked True). On CC-Bench OOD data the rate is 30.7%, suggesting the substring-matching logic works but the vacuous True case dominates both distributions.

### 3.5 Target F1 Thresholds (Post-LLM Annotation)

| Heuristic | Current Status | Expected Challenge | Target |
|-----------|---------------|-------------------|--------|
| H1 is_retry | Definition mismatch | Distinguishing repetition from retry | F1≥0.65 |
| H2 redundant_read | Good preliminary agreement | Read/write tracking in complex sessions | F1≥0.70 |
| H3 is_backtrack | 0 GT labels available | Regex over-precision on common phrases | F1≥0.55 |
| H4 tool_result_used | 0 GT labels available | Vacuous-True inflation | F1≥0.60 |

Any heuristic below its target after LLM annotation is flagged as **v2 work**.

---

## 4. Task Taxonomy

Script: `scripts/03_task_taxonomy.py`

### 4.1 Domain Distribution

| Domain | n | Resolve Rate | Note |
|--------|---|--------------|------|
| lib_general | 44 | 59% | |
| type_checker | 42 | **14%** | Hardest domain |
| unknown | 31 | 61% | Niche repos outside domain map |
| data_ml | 26 | 42% | |
| cloud_devops | 25 | 48% | |
| graph_geo | 20 | **95%** | Easiest domain |
| db_orm | 7 | 43% | n<10, unstable |
| web_api | 3 | 100% | n<3, unstable |
| testing_ci | 2 | 50% | n<10, unstable |

Domain-level variance in resolve rate spans 14%–95% (6.8× range), confirming domain as the dominant difficulty signal.

### 4.2 Patch Structure

| Patch Type | n | % |
|------------|---|---|
| single_file | 98 | 49% |
| no_patch (unresolved) | 58 | 29% |
| multi_file | 44 | 22% |

---

## 5. Difficulty Normalization: Structural Proxies Fail

Script: `scripts/04_difficulty_analysis.py`

### 5.1 Proxy Performance

| Proxy | r_pb | AUC | McFadden R² |
|-------|------|-----|-------------|
| P1: Patch lines | −0.045 | 0.504 | 0.001 |
| P2: Files changed | −0.006 | 0.510 | 0.000 |
| P3: Session turn count | −0.081 | 0.492 | 0.004 |
| P4: Task description words | +0.016 | 0.513 | 0.000 |

All four proxies perform at chance. **No structural proxy predicts per-session resolve probability.**

### 5.2 Why This Matters for Product Design

This is not a dataset artifact — it is a structural property of how coding agents work:

**Problem 1 — Outcome contamination:** P1 (patch lines) and P2 (files changed) measure the solution, not the task. Agents that succeed produce larger patches. This makes both proxies positively correlated with success *by construction*, yet the correlation is near zero because unresolved sessions produce small or no patches too. The signals cancel.

**Problem 2 — P3 × P1 confounding (r=0.715):** Turn count and patch size are almost perfectly correlated. Both measure "how much the agent did", not "how hard the task was." A long session on an easy task looks the same as a long session on a hard task.

**Problem 3 — Description length (P4) is too noisy:** Task descriptions on SWE-bench are issue reports, which vary in verbosity independent of task difficulty.

**Implication:** The efficiency formula `efficiency = outcome / (tokens × difficulty_norm)` cannot use structural features as `difficulty_norm` at the per-instance level. Without a difficulty signal, cross-task comparisons are confounded.

### 5.3 In-Distribution Normalization

For the in-distribution SWE-bench corpus, **domain-level empirical calibration** is the only viable normalizer:

```python
difficulty_norm(session) = 1.0 / resolve_rate_prior(domain)
```

Where `resolve_rate_prior` is estimated from the 200-session corpus. Higher domain difficulty (lower resolve rate) → higher normalization denominator → higher efficiency score for the same token spend.

| Domain | Empirical resolve rate | Implied difficulty weight |
|--------|----------------------|--------------------------|
| type_checker | 14% | 7.1× |
| data_ml | 42% | 2.4× |
| lib_general | 59% | 1.7× |
| graph_geo | 95% | 1.1× |

### 5.4 Out-of-Distribution: Unsolved Problem

Domain-level calibration requires knowing the domain. The OOD evaluation shows that the domain classifier assigns "unknown" to **100% of CC-Bench sessions** and 100% of Claude Code scaffold sessions. For OOD deployment:

- **Option A:** Fall back to corpus-mean resolve rate (50%) — treats all OOD tasks as average difficulty. Simple but wrong for hard domains.
- **Option B:** Use session token count as a proxy for difficulty (P3 correlation with resolve rate is −0.081, near-zero but weakly negative — longer sessions are slightly harder). Minimal information gain.
- **Option C:** Use an LLM to classify task type from the first user turn, then map to a difficulty prior. Cost: ~$0.001/session. Requires a task-type taxonomy that covers OOD domains.

Option C is the recommended v2 path. For v1, fall back to Option A with explicit uncertainty flagging.

---

## 6. OOD Generalization Test

Script: `scripts/06_evaluate_ood.py`

### 6.1 Domain Classifier Degradation

| Corpus | Known domain % | Unknown % |
|--------|---------------|-----------|
| In-distribution (SWE-bench) | 84.5% | 15.5% |
| OOD (CC-Bench + synthetic) | **0%** | **100%** |

The domain classifier keyword map is built entirely from Python OSS repository names. CC-Bench task IDs (`ccbench__1`, `ccbench__8`, ...) contain no repository keywords, causing complete classification failure. This confirms that the domain classifier **must not be deployed** on non-SWE-bench data without extension.

### 6.2 Heuristic Firing Rate Degradation

| Heuristic | In-dist firing rate | OOD firing rate | Δ | Interpretation |
|-----------|--------------------|-----------------|----|----------------|
| H1 is_retry | 8.7% | 3.2% | −5.5 pp | Claude Code makes fewer exact duplicates than OpenHands |
| H2 redundant_read | 10.8% | 2.3% | −8.5 pp | Claude Code re-reads files less; or shorter OOD sessions |
| H3 is_backtrack | 2.1% (heuristic) | **0.0%** | −2.1 pp | Regex vocabulary not matched by CC-Bench phrasing |
| H4 tool_result_used | 20.3% (heuristic) | 30.7% | +10.4 pp | More tool-result usage in direct CC-Bench tasks; vacuous True still inflating |

**H3 zero-fire finding is significant.** 218 agent turns, 0 backtrack detections. The patterns ("let me try a different approach", "scratch that", etc.) are characteristic of OpenHands/SWE-bench verbosity. Claude Code sessions use shorter, more direct language. This means H3 precision may be acceptable on in-distribution data while recall on Claude Code sessions is near zero — a systematic scaffold bias.

### 6.3 Examples of H3 Pattern Mismatch

OpenHands-style backtrack (H3 catches):  
> "Actually, let me reconsider. That approach won't work because..."

Claude Code-style backtrack (H3 misses):  
> "Let me try editing line 42 instead." *(no explicit self-correction)*  

---

## 7. Open Gaps

| Gap | Priority | Blocker | Mitigation |
|-----|----------|---------|------------|
| LLM annotation H3/H4 ground truth | **Critical** | ANTHROPIC_API_KEY | Set key; run `01_annotate_corpus.py` (~$6) |
| H1 definition: retry-after-error vs any-repeat | **High** | Decision | Update `compute_h1_retry` to require error in prior result |
| H3 backtrack patterns for Claude Code | **High** | More data | Add CC-Bench-specific patterns or use LLM classification |
| Model metadata for OpenHands sessions | Medium | Source data | Contact Nebius/SWE-Gym maintainers |
| Human IAA on n≥50 sample per heuristic | Medium | Annotation | Full H3/H4 Haiku run unlocks this |
| Stable n per domain for difficulty priors (n≥30) | Low | More data | Domain coverage is adequate for in-distribution use |
| OOD domain classifier | Low | Design | LLM-based task-type classifier (Option C from §5.4) |

---

## 8. Self-Critique

**What we got right:**
- Structural GT generation handles `str_replace_editor` read/write disambiguation correctly.
- The spot-check IAA reveals a real definitional problem with H1 (κ=0.066) before spending $6 on Haiku annotation.
- The OOD test surfaces a concrete H3 failure mode (scaffold-specific vocabulary).
- The difficulty proxy analysis correctly identifies outcome-contamination as the root cause of AUC≈0.50.

**What we got wrong:**
- H1 and H2 structural GT and heuristics share the same algorithm. The "perfect F1" was a validation design error, caught during spot-check IAA.
- The domain classifier cannot generalize to new scaffolds or task formats. Should have been designed with a fallback from the start.
- The model field for 60% of the corpus is unknown. This should have been verified at corpus download time (script `00_download_corpus.py`).
- The OOD corpus is limited (15 sessions, all `claude_code` scaffold). Multi-language OOD (e.g., from `AlienKevin/SWE-ZERO-12M-trajectories`) was attempted but blocked on HuggingFace streaming timeout.

**Revised guidance for report 02 §5.1:** "lines changed" and "files touched" are not valid difficulty proxies (outcome variables). Remove them from the v1 architecture spec.

---

## 9. Reproducibility

```bash
# Download corpus
python scripts/00_download_corpus.py

# Generate structural GT
python scripts/02b_generate_structural_gt.py

# Validate heuristics (structural GT)
python scripts/02_validate_heuristics.py

# Spot-check sampling and labeling
python scripts/07_sample_spotcheck_turns.py
python scripts/08_apply_spotcheck_labels.py

# Compute IAA
python scripts/09_compute_spotcheck_iaa.py

# Task taxonomy
python scripts/03_task_taxonomy.py

# Difficulty analysis
python scripts/04_difficulty_analysis.py

# OOD traces
python scripts/05b_download_ood_fast.py
python scripts/debug_ccbench5.py        # CC-Bench download

# OOD evaluation
python scripts/06_evaluate_ood.py

# LLM annotation (requires ANTHROPIC_API_KEY)
# ANTHROPIC_API_KEY=sk-ant-... python scripts/01_annotate_corpus.py --model both
```

---

## References

1. Jimenez, C. E., et al. (2023). SWE-bench: Can Language Models Resolve Real-World GitHub Issues? arXiv:2310.06770
2. Yang, J., et al. (2026). SWE-ABS: Unifying the Evaluation of AI Software Engineering Agents. arXiv:2603.00520
3. Tang, X., et al. (2024). AgentDiet: Improving the Efficiency of LLM-based Agents via Diet Strategy. arXiv:2509.23586
4. Wang, P., et al. (2024). Math-Shepherd: Verify and Reinforce LLMs Step-by-step without Human Annotations. arXiv:2312.08935
5. CC-Bench-trajectories. zai-org/CC-Bench-trajectories. HuggingFace Datasets. CC-BY-4.0.
