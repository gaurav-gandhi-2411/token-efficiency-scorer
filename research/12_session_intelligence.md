# Research Note 12 — Session Intelligence: ML Methodology & Validation

**tracegauge 0.7.0 | 2026-06-15**

This note documents the design decisions, feature engineering choices, clustering
methodology, validity results, and anomaly-detection rationale for the Session
Intelligence feature. It is the portfolio artifact that demonstrates rigor:
every design choice is explained, every validity result is reported, and the
honest limits of what was found are stated explicitly.

---

## 1. What Problem Is Being Solved

Single-session scoring (0.1.0–0.6.0) measures each session in isolation: token
bands, waste detection, trajectory grade. It cannot see patterns that only emerge
across many sessions. Questions like "do my sessions cluster into behavioral
types?" or "which sessions are statistical outliers?" require a corpus view.

The goal is not to predict or evaluate — it's to describe what the measured
corpus actually looks like using a principled, validated method.

---

## 2. Feature Engineering

### 2.1 Feature selection rationale

Eight features were selected to represent the two dimensions that vary meaningfully
across sessions: **what happened with the tokens** (attribution mix) and
**how large/costly the session was** (size).

| Feature | Type | Rationale |
|---|---|---|
| `context_resend_pct` | Attribution fraction | B3 / billed tokens; the dominant cost driver in long-context work |
| `context_growth_pct` | Attribution fraction | B6 / billed tokens; distinguishes sessions building context vs. holding steady |
| `output_pct` | Attribution fraction | B4 / billed tokens; model-generated text fraction |
| `waste_pct` | Attribution fraction | (B1+B2) / billed tokens; detected waste fraction |
| `log_real_tokens` | Log-scale size | log1p(real_tokens); normalises the 10x+ size range in the corpus |
| `log_turn_count` | Log-scale size | log1p(turn_count); turn depth as a size proxy |
| `log_cost` | Log-scale size | log1p(session_cost_usd); cost integrates tokens + model tier |
| `has_waste` | Binary flag | 1.0 if any waste event detected, 0.0 otherwise |

**Attribution fractions sum to ≤ 1.0** (B5 fresh input, not included, accounts
for the remainder). Four attribution features + 3 log-scale sizes + 1 binary = 8
total.

### 2.2 Why task_type was excluded

The initial 13-feature model included 5 one-hot task_type columns alongside the
8 behavioral features. Result: k=7, silhouette=0.37. Diagnosis revealed 5 of 7
clusters were pure task-type groups — the algorithm re-discovering the folder
hierarchy already visible in the session metadata. That is re-labelling, not
pattern-finding.

Excluding task_type from the feature vector gave k=3, silhouette=0.466 with
clusters that cross task boundaries. The behavioral structure (size, context
stage, waste presence) is orthogonal to task type — the same behavioral pattern
can appear in a feature-build session and an infra-deploy session. This is the
structure worth finding; it would not emerge from a per-task-type analysis.

task_type is still reported as a per-cluster characteristic after clustering —
confirming that the behavioral archetypes are genuinely cross-type.

### 2.3 Turn-count data quality fix

66 content sessions had `turn_count = 0` in the database. These were sessions
scored before turn-counting was wired into the pipeline; the null was stored as
0 rather than NULL. This created a spurious cluster: high real_tokens, turn_count=1
(after log1p), a shape that doesn't correspond to any real behavioral mode.

Fix: at feature-extraction time, when `turn_count = 0` and a source JSONL path
is available, re-derive the turn count by counting assistant turns in the JSONL.
This preserved all 235 sessions that would otherwise have been excluded or
misclassified.

---

## 3. Clustering Method

### 3.1 Algorithm selection

**KMeans** was chosen over HDBSCAN for three reasons:
1. **N=235 is well-bounded** — small enough that density-based methods over-segment
   or fail to converge; large enough that KMeans is stable.
2. **Clean centroids for archetype naming** — KMeans centroids are interpretable
   as the "average session" of each cluster in feature space. HDBSCAN produces
   cluster cores, not centroids; naming archetypes from cores is harder to justify.
3. **Silhouette-based k selection is well-understood** — the k sweep is
   transparent and auditable; the optimal k has a clear interpretation.

### 3.2 Hyperparameters

| Parameter | Value | Rationale |
|---|---|---|
| k range | 2..8 | Below 2: trivial; above 8: over-segmentation for N=235 |
| n_init | 30 | Reduces sensitivity to random initialisation |
| random_state | 42 | Reproducibility |
| Stability seeds | 10 (0..9) | CV < 0.15 required for "stable" verdict |

### 3.3 K selection

Best k chosen by silhouette score sweep over k=2..8. Silhouette score measures
how well each point fits its cluster relative to neighboring clusters (range
−1..1; higher is better for compact, well-separated clusters).

| k | Silhouette |
|---|---|
| 2 | 0.401 |
| **3** | **0.466** |
| 4 | 0.421 |
| 5 | 0.387 |
| 6 | 0.369 |
| 7 | 0.352 |
| 8 | 0.331 |

k=3 selected (optimal silhouette).

### 3.4 Validity thresholds

| Threshold | Value | Interpretation |
|---|---|---|
| `SILHOUETTE_STABLE_THRESHOLD` | 0.20 | ≥ 0.20: meaningful structure |
| `SILHOUETTE_WEAK_THRESHOLD` | 0.10 | 0.10–0.19: weak structure |
| `STABILITY_CV_THRESHOLD` | 0.15 | CV ≥ 0.15: unstable across seeds |

### 3.5 Validity results on the live corpus (2026-06-15)

| Metric | Value | Verdict |
|---|---|---|
| k | 3 | — |
| Silhouette | 0.466 | **meaningful structure** (≥ 0.20) |
| Stability mean | 0.466 | — |
| Stability CV | 0.000 | **stable** (< 0.15 across 10 seeds) |
| Content sessions | 235 | above floor (30) |

CV = 0.000 means the same three clusters emerge regardless of random initialisation.
This is strong evidence of genuine structure, not a local minimum artifact.

---

## 4. Named Archetypes

Archetype names are derived from the centroid's dominant features (top-Z scores
from global mean). Evaluative terms (efficient, wasteful, good, bad, optimal)
are prohibited — names describe measured shape, not quality.

### Archetype [1]: medium high context re-send sessions (64.7%)
- context_resend: 95.7% of billed tokens
- context_growth: 3.3% of billed tokens
- output: 1.0% of billed tokens
- has_waste: NO
- Interpretation: Most sessions. Context is carried forward across turns at high
  rate; context window is stable (low growth). Task mix is balanced across all
  five types.

### Archetype [0]: small active context-building sessions (24.3%)
- context_resend: 87.3% of billed tokens
- context_growth: 10.5% of billed tokens
- output: 2.2% of billed tokens
- has_waste: NO
- Interpretation: Shorter sessions actively building context. Context growth is
  3× the median — these are early-stage sessions where new information is still
  entering the context window.

### Archetype [2]: medium with detected waste sessions (11.1%)
- context_resend: 95.3% of billed tokens
- context_growth: 3.2% of billed tokens
- output: 0.9% of billed tokens
- has_waste: YES
- Interpretation: Sessions where at least one waste event was detected. Behaviorally
  similar to Archetype [1] (same size, same resend rate) but distinguished by the
  presence of waste. Task mix skews toward infra-deploy and ml-eval.

### Honest framing of the archetypes

Context re-send is near-constant (~0.93–0.96) in two of three clusters. The three
archetypes primarily separate on **size** (log_real_tokens, log_turn_count),
**context-building stage** (context_growth_pct), and **waste presence** (has_waste).
They do not represent dramatically different working styles — this is a fairly
homogeneous corpus where one developer runs similar sessions with modest variation.
The archetypes are real (silhouette 0.466, CV 0.000) but they describe modest
differentiation, not distinct personalities.

---

## 5. Anomaly Detection

### 5.1 Method: Tukey outer fence on centroid distance

For each session, compute its Euclidean distance to its assigned cluster centroid
in the **scaled** feature space (post-StandardScaler). Sessions far from the
centroid are unusual for their cluster.

Threshold per cluster: **Q3 + 1.5 × IQR** (Tukey outer fence), where Q3 and IQR
are computed from the distribution of centroid distances within that cluster.

### 5.2 Why Tukey fence over IsolationForest or Z-score

- **Per-cluster**: a session is anomalous *relative to its behaviorally similar
  peers*, not relative to the full corpus. Cluster-level thresholds are more
  meaningful than global ones.
- **Tukey fence**: principled, non-parametric, no hyperparameters to tune.
  Q3 + 1.5×IQR is a well-understood threshold (classic box-plot outlier
  definition). IsolationForest would add a contamination hyperparameter that
  requires justification we don't have.
- **Z-score**: assumes Gaussian distribution; centroid distances in small clusters
  may not be Gaussian.

### 5.3 Results on the live corpus

- 10 of 235 sessions (4.3%) flagged as anomalies
- Each anomaly has top-3 deviating features reported (signed deviation from
  centroid in scaled space, sorted by |deviation|)
- 4.3% is consistent with Tukey's expected false-positive rate (~7% for Gaussian
  data); the actual rate is slightly lower, meaning the flagged sessions are
  genuine tail events

---

## 6. Honesty Boundary

The ML layer is **descriptive only**. It describes what was measured; it does not
predict, evaluate, or recommend.

| Question type | Response |
|---|---|
| "What kind of sessions do I run?" | Describes measured archetypes, with fractions and feature values |
| "Which sessions are outliers?" | Reports count and percentage; names deviating features |
| "What will my next session cost?" | "I don't predict future behavior" |
| "Was this session efficient?" | "tracegauge doesn't rate session quality" |
| "How many tokens did caching save?" | "I don't have that measured" |

The chat context contains **only computed numbers** — no session content, code,
tool inputs/outputs, or file paths. The context format uses unambiguous labels
(has_waste: YES/NO not 0/1; "% of billed tokens" not bare percentages).

---

## 7. What Didn't Work

- **13-feature model with task_type one-hot**: k=7, silhouette=0.37, 5/7 clusters
  were pure task-type groups. Rejected — re-discovers known labels, not new patterns.
- **turn_count=0 as-stored (66 sessions)**: created a spurious cluster (high tokens,
  1 effective turn). Fixed by re-deriving turn count from JSONL at extraction time.
- **qwen3:30b-a3b as local chat model**: extended thinking mode (even with
  `think=False`) caused response truncation for complex questions. Switched to
  `qwen3:8b` as default — better context-following for this constrained-explainer use case.
- **`waste=1` in context format**: misread by model as "1.0% waste rate" instead
  of a binary flag. Fixed with unambiguous `has_waste: YES/NO` labeling; guarded
  by `TestContextFormatUnambiguous`.
