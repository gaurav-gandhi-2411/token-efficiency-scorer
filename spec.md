# Project Spec: tracegauge — Session Intelligence (Iteration 0.7.0)

## Goal

Add a genuine Applied-AI capability that does work the deterministic engine cannot: (A) unsupervised ML that finds RECURRING PATTERNS and ANOMALIES across the user's whole session corpus (single-session views miss these), and (B) a conversational layer that lets the user ASK about their sessions in natural language and get honest answers grounded in the real, already-measured metrics.

The two compose: the ML (B-foundation) finds the patterns; the chat (A-layer) explains them. Together: "understand your sessions, across all of them, in plain language."

This is productional portfolio-grade: the ML is done with real methodological rigor (justified features, validated clusters, principled anomaly thresholds) AND it is honest by construction (describes measured patterns, never predicts/scores/labels sessions good-bad). The chat explains measured numbers and the cluster/anomaly results; it NEVER invents claims the engine didn't measure.

## The honesty boundary (non-negotiable — this is the phase's whole risk)

This is the phase where "Applied AI" most tempts the project's one failure mode: a model that SOUNDS smart while quietly making things up. The hard rule:

**The AI describes/explains ALREADY-MEASURED truth. It NEVER becomes a new source of claims.**

Concretely:
- **The chat answers ONLY from real computed metrics + cluster/anomaly results.** It reads the honest attribution/cost/waste/baseline numbers and the ML outputs, and explains them in plain language. It does NOT re-judge sessions, invent new analysis, or assert anything not in the measured data. If it can't answer from measured data, it SAYS "I don't have that measured" — it does NOT hallucinate.
- **The ML DESCRIBES, never PREDICTS.** Clustering names recurring archetypes from real feature vectors; anomaly detection flags sessions that statistically deviate from their cluster. NEITHER predicts future cost, scores quality, or labels a session "good/bad/wasteful-as-judgment." A cluster is "these sessions share this measured shape," an anomaly is "this session statistically deviates from its group" — both descriptive, both falsifiable against the data.
- **Clusters must be VALIDATED as real, not noise.** Report silhouette score (or equivalent) + stability; if the clusters aren't statistically meaningful, SAY SO ("no stable clusters found at N sessions") rather than presenting noise as patterns. Portfolio-grade = honest about cluster validity, not just "here are 5 pretty clusters."
- **The chat carries provenance.** When it states a number, it's traceable to the real metric (e.g. "this session's context re-send was 95% of tokens — from the attribution"). No unsourced assertions.
- **Metrics-only egress (chat).** The chat sends COMPUTED METRICS (numbers, bucket %s, cluster labels, anomaly flags) to the LLM — NEVER raw session content/code/prompts. This is both safer (far less egress than the judge's snippets) and sufficient (the LLM explains numbers, doesn't need code). Same local-OR-API choice as the judge, API path consented.

If any design makes the AI a source of unmeasured claims, or sends code in the chat path, or presents unvalidated clusters as real: STOP, escalate.

## Decisions (locked by user)
- **Chat LLM:** BOTH local (Ollama) and API (user's key), like the judge. API path consented. Chat sends METRICS ONLY, never code.
- **ML rigor:** PRODUCTIONAL PORTFOLIO-GRADE — justified feature engineering, a real clustering method with validated parameters, cluster-validity metrics reported, principled anomaly thresholds, documented methodology. Rigor here IS honesty (a validated cluster doesn't claim patterns that are noise).

## Current state
tracegauge 0.6.0 LIVE on PyPI — complete, honest, frictionless. ~700-1000 sessions in the user's store (the corpus the ML runs on). Engine (attribution/cost/waste/judge/self-baseline) correct and frozen. This phase ADDS a descriptive ML + conversational layer ON TOP; it changes nothing in the measurement engine.

## The ML (B — the foundation, portfolio-grade)

**Feature vector per session** (from already-computed metrics — no new measurement): attribution bucket proportions (context-resend %, context-growth %, output %, fresh %, waste %), cost shape, waste signature (event count/type), turn count, real_tokens (normalized), task_type (encoded). Document the feature choices + any normalization/scaling.

**Clustering:** a justified method (e.g. KMeans with k chosen by silhouette/elbow, or HDBSCAN if density-based fits better — pick and JUSTIFY). Output: named recurring archetypes ("context-heavy ml-eval", "clean quick feature-build", "retry-prone debug") — names DERIVED from each cluster's dominant measured features, not hand-waved. Report cluster-validity (silhouette, cluster sizes, stability across re-runs). If no stable clustering exists at the current N, report that honestly.

**Anomaly detection:** flag sessions that statistically deviate from their cluster (e.g. distance-from-centroid beyond a principled threshold, or isolation-forest score). Output: "this session is an outlier for its type — [which measured features deviate]." Descriptive, with the deviating features named. Principled threshold (justified, not arbitrary).

**Honest framing everywhere:** clusters/anomalies are DESCRIPTIVE summaries of measured data. No prediction, no quality judgment. Carry a domain-of-validity ("patterns from YOUR N sessions; descriptive not predictive; single-developer corpus").

## The conversational layer (A — on top of the ML + metrics)

A chat interface (dashboard panel and/or CLI `tes ask "..."`) where the user asks natural-language questions and gets answers GROUNDED in real metrics + ML results:
- "Why was yesterday's session expensive?" → reads that session's attribution, explains in plain language ("context re-send was 95% of tokens / 49% of cost — a long context drove it").
- "What's my most common waste pattern?" → reads waste data across sessions, summarizes.
- "Which sessions are outliers?" → reads the anomaly results, lists + explains.
- "What kind of sessions do I run?" → reads the clusters, describes the archetypes.

Implementation: the LLM gets a structured context of the RELEVANT computed metrics + ML outputs (metrics-only, never code) + a system prompt that CONSTRAINS it to explain only what's in that context and to say "not measured" otherwise. Tool/retrieval pattern: the chat pulls the real numbers for the question, hands them to the LLM, the LLM explains. The LLM is an EXPLAINER over measured data, not an analyst inventing findings.

**Honesty guards (tested):** the system prompt + a post-check that the chat doesn't assert numbers absent from the provided context; a test that an out-of-scope question ("what will my next session cost?") gets "I don't predict / that's not measured", not a fabricated answer.

## Scope
### In scope
1. `tes/intelligence/` — feature extraction (from stored metrics), clustering (validated), anomaly detection (principled threshold). Portfolio-grade, documented.
2. The conversational layer — `tes ask "..."` CLI + optionally a dashboard chat panel; LLM local-or-API (metrics-only egress, API consented), constrained-to-measured-data system prompt.
3. Dashboard: a "Patterns" / "Intelligence" view showing the archetypes + anomalies (descriptive, honest framing, validity reported).
4. Validation + methodology doc (research/12 or a docs page): feature choices, method justification, cluster-validity results, anomaly-threshold rationale — the portfolio artifact.
5. Tests: clustering produces validated output (or honestly reports no-stable-clusters); anomaly threshold principled; chat answers from context only + says "not measured" for out-of-scope; no-code-in-chat-egress; API consent preserved.

### Out of scope
- ANY change to the measurement engine (attribution/cost/waste/judge/self-baseline/detectors) — this layer CONSUMES measured data, never alters it.
- Prediction of any kind (future cost, quality forecast) — descriptive only.
- Sending raw session content/code to the chat LLM (metrics-only).
- Weakening API consent. Trends (parked). Reports 01-11 immutable.

### Hard rules
- AI EXPLAINS MEASURED TRUTH, NEVER INVENTS. Chat answers only from provided real metrics + ML outputs; "not measured" when it can't; no hallucinated numbers.
- ML DESCRIBES, NEVER PREDICTS/JUDGES. Clusters/anomalies are descriptive; validity reported; no-stable-clusters reported honestly if so.
- METRICS-ONLY chat egress (never code); API path consented; local option exists.
- Engine frozen (no scoring/attribution/detector change; git diff _waste_detectors.py empty). Reports 01-11 immutable.
- Portfolio-grade ML: justified, validated, documented — rigor as honesty.

## Tech stack
- Python, tes/. ML: scikit-learn (KMeans/HDBSCAN, silhouette, IsolationForest) — standard, justifiable, portfolio-legible. numpy/pandas for features.
- Chat: reuse the judge's local(Ollama)/API(key) client; metrics-only context; constrained system prompt. httpx already present.
- pytest: cluster-validity, anomaly-threshold, chat-grounding (answers-from-context-only, not-measured-for-out-of-scope), no-code-egress, consent-preserved.

## Architecture
```
tes/intelligence/
├── features.py      # session -> feature vector (from stored metrics); documented choices
├── cluster.py       # validated clustering; archetype naming from dominant features; validity metrics
├── anomaly.py       # principled deviation detection; names deviating features
├── chat.py          # the conversational explainer: metrics-only context, constrained prompt, local/API
tes/cli.py           # `tes ask "..."`; maybe `tes patterns`
tes/web/             # a "Patterns/Intelligence" dashboard view (descriptive, honest framing)
research/12_session_intelligence.md   # methodology + validation = the portfolio artifact
tests/
├── test_cluster_validity.py
├── test_anomaly_threshold.py
├── test_chat_grounding.py      # answers-from-context-only; "not measured" for out-of-scope; no hallucinated numbers
└── test_chat_no_code_egress.py # chat context contains metrics, never raw content; API consent gates send
```

## Verification commands
```yaml
- name: cluster-validity
  cmd: python -m pytest tests/test_cluster_validity.py -v   # clusters validated or no-stable-clusters reported
  required: true
- name: chat-grounding
  cmd: python -m pytest tests/test_chat_grounding.py -v     # answers from context only; not-measured for out-of-scope
  required: true
- name: chat-no-code-egress
  cmd: python -m pytest tests/test_chat_no_code_egress.py -v  # metrics-only; consent gates send
  required: true
- name: detectors-frozen
  cmd: git diff --exit-code tes/_waste_detectors.py && echo frozen
  required: true
- name: full-suite
  cmd: python -m pytest -q
  required: true
```

## Escalation rules (autonomous mode — escalate ONLY these)
- PUBLISHING 0.7.0 to PyPI (irreversible; user's token).
- If the chat could assert UNMEASURED claims / hallucinate numbers (honesty regression) — escalate the grounding design before shipping.
- If the chat path could send CODE (not just metrics) or weaken API consent — STOP, escalate.
- If clustering is being presented as real when validity is poor — escalate (don't ship noise-as-patterns).
- Touching the frozen engine/detectors/reports — out of scope, escalate.
- Otherwise DECIDE AND ACT: method choice, feature engineering, archetype naming, chat UX, prompt design — your call; report, don't ask. (But HOLD once for consultant review of: the feature set + clustering method + validity results BEFORE building the chat on top — the ML foundation must be sound before the explainer sits on it.)

## Budget
- Soft: 4-6 CC sessions. Local/$0 for ML + local chat. API chat testing uses the user's key (minimal); confirm before real API calls in testing.

## Success criteria (verify ALL)
- ML: validated clustering (silhouette/stability reported) producing named archetypes from real features — OR an honest "no stable clusters at N" if that's the truth. Anomaly detection with a principled threshold, naming deviating features. Descriptive, not predictive. Methodology documented (research/12).
- Chat: `tes ask "..."` answers grounded in real metrics + ML outputs, in plain language, with provenance; says "not measured"/"I don't predict" for out-of-scope; never hallucinates numbers (tested). Local OR API (metrics-only egress, API consented).
- Dashboard "Patterns" view: descriptive archetypes + anomalies, honest framing + validity, no prediction/judgment.
- Engine untouched (same numbers); detectors frozen; reports 01-11 immutable; full suite green.
- 0.7.0 built, clean-roomed (ML + chat work from installed wheel), PUBLISHED (publish-immediately), fresh-install confirmed.

## Build order (orchestrator decides details; ONE consultant hold)
1. Read CURRENT_STATE.md + spec.md + the stored-metric schema + tes/judge.py (for the local/API client to reuse). Confirm context + the AI-explains-never-invents boundary in 5-7 lines.
2. Build the ML foundation: features.py (justified feature set) + cluster.py (validated) + anomaly.py (principled). Run on the real ~700-session store. HOLD — show consultant the feature set, the clustering method + WHY, the validity results (silhouette/stability/cluster sizes), the named archetypes, and the anomaly threshold rationale. This is the "is the ML real, not noise" gate BEFORE the chat sits on it.
3. Build the chat explainer (chat.py): metrics-only context, constrained-to-measured-data prompt, local/API, the grounding + no-code-egress + consent tests. HOLD — show consultant a real Q&A transcript (real questions on the real store) + the out-of-scope "not measured" behavior.
4. Dashboard Patterns view (descriptive, honest framing).
5. Methodology doc (research/12) — the portfolio artifact.
6. Full suite + detectors frozen + clean-room. Bump 0.6.0 -> 0.7.0, CHANGELOG. ESCALATE the publish.
