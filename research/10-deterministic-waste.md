# Report 10 — Deterministic Waste Detection: Shipped Detectors, Exploratory Findings, and the Boundary of What Is Measurable

**Author:** Gaurav Gandhi
**Date:** 2026-06-03
**Status:** FINAL

---

## 0. Purpose and Design Principle

This report documents the B4 deterministic waste detection layer: four candidate detectors built, run against the 181-session CC pool, and evaluated for launch readiness. The layer was motivated by report 09's finding that LLM waste judgments are model-dependent (Gemma reversed 94.4% of Qwen's WORSE verdicts, kappa=0.31). The engineering response: move waste detection from contested opinion to counted, trace-auditable events — behavior that is waste under **any** reasonable definition.

**The credibility rule:** A deterministic detector is only credible if it fires ONLY on behavior that is waste under any reasonable definition. Detectors that require domain-specific exclusions to stop firing on non-waste show that their concept is contested — the opinion has moved from the LLM into the code. The number of exclusions a detector needs is a proxy for how contestable its underlying concept is. This principle governs the launch-readiness decision for every detector.

**Pool:** 181 CC sessions for detector runs; 143 sessions have Qwen scores (38 unscored). Gemma 3 27B scored 141/143 (2 parse failures). Cross-checks reference Qwen throughout.

---

## 1. The Central Finding: What Is and Is Not Deterministically Detectable

B4's most important output is not the two shipped detectors — it is the MAP of where deterministic detection is and is not possible. This map tells the future human-labeling phase exactly what it is for.

### Observable-invariant waste — detectable

Some waste is observable as a FACT in the trace, independent of task intent or evaluator interpretation:

- **Same command → same error → no state change between = uncontestable retry waste.** The command failed. Nothing changed. The agent ran it again. This is not a judgment about the session's trajectory; it is a measurement of what happened.
- **Same file content → no edit between → read again = uncontestable redundant read.** The file was not modified. The agent fetched the same bytes again. No evaluator disagrees with this characterization.

These facts hold regardless of context, task type, or model architecture. That is why REPEATED-FAILED-RETRY and REDUNDANT-READ can be implemented deterministically: their firing condition is a structural invariant of the trace, not a judgment about whether the agent was making progress.

### Judgment-of-progress waste — not deterministically detectable

Other waste claims require evaluating whether an action advanced the session goal — which is not a fact in the trace but an interpretation that depends on task context and evaluator judgment:

- **"Did this loop represent a dead-end, or iterative debugging?"** The DEAD-END detector attempted to infer this from repeated tool-output headers. But whether the headers indicate cycling depends on whether the approaches between them differ — a question that requires knowing what progress looks like for the specific task, and that the 300-char content_snippet cannot fully answer.
- **"Was this turn a no-op, or legitimate planning?"** The EMPTY-TURN detector attempted to operationalize this from empty content_snippet values. But empty ai turns occur in excellent sessions at high rates, because the CC JSONL format records extended-thinking turns identically to genuine no-op turns. The discriminating signal — did the model reason here? — is not in the digest.

These claims cannot be resolved from the trace alone. An evaluator must apply judgment: what was this session trying to accomplish, and did this action advance it? That is the question LLMs disagree on (B3), and it is the question human labels are for.

**The boundary:** Observable-invariant waste (same-inputs, no-state-change) is measurable. Judgment-of-progress waste (was this cycle productive, was this turn purposeful) is not — it requires a human or a model to assess intent and progress, which reintroduces the model-dependency problem B4 was built to solve. Future human labeling should focus on the judgment-of-progress cases, because those are what the deterministic layer cannot and will not cover.

---

## 2. Status Summary

| Detector | Status | Sessions fired | Fired ∩ Qwen WORSE | Exclusions needed | Where it fails |
|---|---|---|---|---|---|
| REPEATED-FAILED-RETRY | **SHIPPED** | 12/181 (6.6%) | 0/18 | 1 principled (CI-polling) | — |
| REDUNDANT-READ | **SHIPPED** | 20/181 (11.0%) | 3/18 | None | — |
| DEAD-END/LOOP | **EXPLORATORY** | 1/181 (0.6%) | — | 3 domain-specific headers | Fails at implementation |
| EMPTY-TURN | **EXPLORATORY** | — | — | Signal ambiguous at definition | Fails at definition |

**Launch-1 selection:** REPEATED-FAILED-RETRY and REDUNDANT-READ ship. DEAD-END and EMPTY-TURN are documented as exploratory primitives; neither enters `waste_detectors.py` as a production detector.

---

## 3. REPEATED-FAILED-RETRY — Shipped

**Rule (conservative):** The same shell tool (Bash or PowerShell) produces an identical error snippet ≥2 times consecutively, with no intervening state-changing operation (Write/Edit/NotebookEdit call, Bash-driven state mutation, or user turn). One principled exclusion: transient errors (zone exhaustion, rate limits, quotas, CI-polling status codes) — these are not agent-fixable failures; retrying them is correct behavior.

**Fire rate:** 12/181 sessions (6.6%); of the 143 Qwen-scored sessions, 5 fired (all good-verdict, 0 in WORSE); the remaining 7 fired in the 38 unscored sessions.

**4-way Qwen cross-check:**

| Quadrant | Count |
|---|---|
| Fired ∩ WORSE/MUCH_WORSE | 0/18 |
| Fired ∩ MUCH_BETTER/BETTER/SIMILAR | 5/125 |
| Not-fired ∩ WORSE/MUCH_WORSE | 18/18 |
| Not-fired ∩ MUCH_BETTER/BETTER/SIMILAR | 120/125 |

The 0/18 overlap with Qwen's WORSE sessions is expected: REPEATED-FAILED-RETRY fires on micro-waste (exact-match retry loops) that Qwen's trajectory-level judgment does not separately flag. The 5/125 fires in good-verdict sessions are legitimate events — an otherwise well-executed session can contain one retry loop. This is intended: the deterministic layer is complementary to the judge, not a proxy for it.

**Exclusion audit:** The one exclusion (CI-polling status: `gh pr checks` pending/no-checks) is principled (transient availability, not an agent error), not domain-specific. The detector requires no framework-header awareness.

**Unit tests:** 25/25 pass. Implementation: `scripts/waste_detectors.py::detect_repeated_failed_retry`.

---

## 4. REDUNDANT-READ — Shipped

**Rule (conservative, two paths):**

- **PATH A (tool-authoritative):** The CC Read tool itself returns "File unchanged since last read" — the tool's own verdict. Maximally uncontestable.
- **PATH B (content-match):** Two Read results carry identical line-numbered file content (≥80 chars, `\d+\t` prefix) within a ≤5-turn gap, with no Write/Edit/NotebookEdit or user turn between. Gap ≤5 is the conservative cap: re-reads after more than 5 intervening turns are plausibly re-orientation reads, which are contestable.

**Fire rate:** 20/181 sessions (11.0%); PATH A: 4 sessions, PATH B: 18 sessions. Of the 143 Qwen-scored sessions, 8 fired (3 WORSE/MUCH_WORSE + 5 good-verdict); the remaining 12 fired in the 38 unscored sessions.

**4-way Qwen cross-check:**

| Quadrant | Count |
|---|---|
| Fired ∩ WORSE/MUCH_WORSE | 3/18 |
| Fired ∩ MUCH_BETTER/BETTER/SIMILAR | 5/125 |
| Not-fired ∩ WORSE/MUCH_WORSE | 15/18 |
| Not-fired ∩ MUCH_BETTER/BETTER/SIMILAR | 120/125 |

3/18 overlap with Qwen's WORSE sessions: partial corroboration. Where Qwen cited redundant reads, the deterministic detector agrees on 3 cases. Both paths carry trace-level evidence (turn indices, content snippet, gap).

**Exclusions:** None required. The gap window and barrier conditions exclude contestable cases by construction. Reads after an edit produce different content and naturally do not match; reads across user turns are excluded as context-reset boundaries.

**Unit tests:** 46/46 pass. Implementation: `scripts/waste_detectors.py::detect_redundant_read`.

---

## 5. DEAD-END/LOOP — Exploratory (Fails at Implementation)

### 5.1 Conceptual rule

Fire when: an agent returns to a previously-attempted approach (inferred from identical tool-output headers reappearing in content_snippet) without having applied a meaningful state change between occurrences. Intent: catch cycles where the agent tries approach A, fails, tries something else that also fails, and returns to approach A unchanged.

### 5.2 Existence-proof fire

Session 4353d619 produced the one clean fire across 181 sessions. The same error header appeared in the content_snippet of two non-adjacent tool results with no state-advancing call between — evidence the pattern exists and is real.

### 5.3 Why it does not ship — fails at implementation

Three domain-specific header exclusions were required before the detector stopped firing on non-waste:

1. **pytest headers** (`=== ... ===`): Standard test output format appears on every run regardless of whether tests improved or cycled.
2. **pre-commit hook headers**: Structured output appears on every pre-commit invocation; iterative hook fixing is not a dead-end.
3. **vite build headers**: Vite's preamble is identical across builds regardless of progress.

**The diagnostic:** REPEATED-FAILED-RETRY needed one principled exclusion (transient errors — conceptually distinct from fixable failures). REDUNDANT-READ needed zero exclusions. DEAD-END needed three domain-specific patches, each papering over a case where the detector fires on non-waste. The escalation from zero to three is the signal: each exclusion reveals a case where the question "was this a dead-end?" requires knowing what kind of tool produced the output — framework knowledge that cannot be derived from the trace alone.

**The 300-char truncation problem:** Even session 4353d619 cannot be fully verified from the digest. The header triggering the pattern is visible in 300 chars, but the content distinguishing "same dead-end failure" from "same header, different failure" falls after char 300. The one clean fire is an existence proof, not a verifiable uncontested event.

**The conceptual boundary:** "Did the fix attempt count as progress?" is a judgment. DEAD-END is resistant to deterministic detection in a way the first two detectors were not — not because it needs more data, but because the concept requires evaluating intent and outcome, not just observing repeated identical inputs.

### 5.4 Status

Not added to `waste_detectors.py`. Preserved here as the conceptual primitive + existence-proof fire (session 4353d619). Revisit if the digest schema is extended to capture full tool outputs or if framework-header classification becomes available without domain knowledge.

---

## 6. EMPTY-TURN — Exploratory (Fails at Definition)

This is the sharpest point of the iteration, and the distinction from DEAD-END matters.

**DEAD-END fails at implementation:** The concept is sound (approach cycles without progress are waste), but the available signal (tool-output headers at 300-char resolution) cannot implement it cleanly. Building it produced escalating exclusions.

**EMPTY-TURN fails at definition:** The concept cannot be stated without either (a) taking Qwen's side in a model-disputed call, or (b) firing on legitimate extended-thinking turns. The problem is in the data, not the rule's specificity. No tighter rule closes this.

### 6.1 The observable signal is ambiguous at the definition level

The digest offers two "empty turn" signals:

- **Category A** — `role='ai'`, `tool_names=[]`, `content_snippet=''`: **11,578 turns across all 181 sessions** (mean 64 per session). Sessions rated BETTER by Qwen include `5b65dd50` (271 empty turns) and `75b1d338` (269 empty turns).
- **Category B** — ultra-short text (≤5 chars): **0 sessions.** Does not exist in the pool.

Category A is ubiquitous because it describes two structurally different things that are **indistinguishable in the digest**:

1. **Extended-thinking turns:** When Claude Code uses extended reasoning, the thinking phase produces an ai turn with `content_snippet=''`. This is not a no-op — the model is reasoning. But it looks identical to a genuine empty turn.
2. **Genuine no-op turns:** A turn where the agent produced nothing useful.

The discriminating signal — did the model reason here? — is not captured in the digest. No rule operating on `content_snippet=''` can separate these two cases. This is not a threshold problem or an exclusion problem; it is a schema problem. The data does not contain the information needed to implement the concept.

### 6.2 The prototype cases are definitionally contested

The two sessions Qwen flagged as EMPTY-TURN waste — 5e416ec7 (MUCH_WORSE, 12 turns) and e0e44c19 (WORSE, 12 turns) — are the sessions Gemma 3 27B rates BETTER. Not SIMILAR. BETTER. Both models processed the same digest with the same rubric and reached opposite conclusions about whether the planning turns advanced the session.

This is not incidental. "Did these turns advance the session?" is the exact question EMPTY-TURN operationalizes, and it is the exact question B3 showed LLMs answer differently. Any rule that fires on 5e416ec7 and e0e44c19 is encoding Qwen's interpretation of that question, not measuring an uncontested fact.

**The contrast with DEAD-END:** DEAD-END's prototype fire (4353d619) was at least a potentially-clean event — the three exclusions were needed to prevent false positives, not to make the prototype itself uncontested. EMPTY-TURN's prototype cases ARE the B3 dispute. The detector cannot be made conservative: its target events are already known to be contested by cross-model evidence.

### 6.3 Status

Not added to `waste_detectors.py`. Documented here as the diagnostic finding: the "empty turn" concept is not reducible to a deterministic observable in the current digest schema. The relevant signal — did this turn advance the session? — requires task-context judgment, which is what the judge is for. Future work: if the CC adapter is extended to flag extended-thinking turns separately (via a `has_thinking` boolean or thinking-token count), EMPTY-TURN could be revisited for the residual category.

---

## 7. Score Re-Architecture — Three Reported Axes

B4 confirms the score structure. Waste is a REPORTED, EVIDENCED axis — not folded into a composite number. B3+B4 proved the three axes measure genuinely different things; collapsing them into one number re-hides what the work separated.

**Per-session output:**

1. **Token-economy axis** (from B2 baselines): scope status + band verdict (`above_p75` / `within_band` / `below_p25` / `unavailable`). Domain of validity: CC-caching-native sessions within the p10 scope gate.

2. **Trajectory-quality axis** (judge, positive-signal scoped): verdict + score + reasoning. Domain of validity: the MUCH_BETTER reference population (B2 baselines, B3-corroborated at 84-96%). The judge is NOT used for waste detection.

3. **Deterministic waste axis** (shipped detectors): count + proof-turn list per detector (REPEATED-FAILED-RETRY, REDUNDANT-READ). Domain of validity: observable-invariant waste only. Each event carries the specific turns that prove it. DEAD-END and EMPTY-TURN are not part of this axis.

Each axis carries its own stated domain of validity. No composite score. The three signals are independent by design: a session can have waste events and a MUCH_BETTER trajectory verdict (legitimate iteration that includes a retry loop). Collapsing them would lose this distinction.

**Implementation:** `scripts/efficiency_score.py` — the `score_session` function accepts an optional `waste_entry` (from `data/pool_waste_signals.jsonl`) and populates `waste_event_count` and `waste_events` on the `EfficiencyResult` dataclass. The CLI prints a third section (`--- DETERMINISTIC WASTE ---`) alongside the existing token-economy and trajectory-quality sections.

---

## 8. Judge Cross-Check Interpretation

Combined fire rate: REPEATED-FAILED-RETRY or REDUNDANT-READ fired on 27 sessions, of which 3 overlap with Qwen's 18 WORSE sessions (3/18, 16.7%). The deterministic layer does not strongly predict Qwen's waste verdicts.

This is the expected result of the design. The deterministic layer fires on observable-invariant waste. Qwen's WORSE verdicts are driven primarily by `failed_retry` and trajectory-drift signals integrated across the whole session — a different scope than per-event detection. The 3/18 overlap confirms the layers are not redundant; the 15/18 gap confirms the judge catches something the deterministic layer is not designed to catch (judgment-of-progress waste).

Cross-model note: the single session that both Qwen AND Gemma rate WORSE (the one B3 overlap case) is not in the 3 deterministic-fired WORSE sessions. The convergent LLM waste signal does not, in this pool, correspond to a deterministic-detectable event. This reinforces the MAP in section 1: the waste LLMs agree on may itself be judgment-of-progress waste, not observable-invariant waste.

---

## 9. Cumulative Limitations

**Carried from B1-B3:**
- No human gold set. rho≈0.79 vs the provisional LLM reference on the B1 calibration set (report 06) — establishes instrument coherence, not human accuracy.
- Token axis covers 50% of held-out sessions; the other 50% are UNAVAILABLE (scope gate).
- Waste detection was model-dependent (report 09). B4 addresses this for observable-invariant waste only.

**New limitations from B4:**
- DEAD-END is unimplementable without domain-specific exclusions at 300-char content_snippet resolution. Approach cycles involving standard framework output (pytest, pre-commit, vite) cannot be distinguished from genuine dead-ends.
- EMPTY-TURN is unimplementable because the observable signal (empty ai turns) is ambiguous in the CC JSONL format and the prototype waste cases are the B3 model-dispute itself.
- Deterministic waste fires on 3/18 of Qwen's WORSE sessions, not 18/18. This gap is a design feature (conservative by construction), not a coverage failure.
- No OPTIMAL-PATH-RATIO detector (deferred; requires a reference path unavailable without human annotation).
- The boundary established in section 1 (observable-invariant vs judgment-of-progress) is derived from one 181-session CC pool. Generalization to other agent tools or task domains is unvalidated.
