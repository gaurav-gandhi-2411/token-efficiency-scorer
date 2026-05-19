# SOTA Scan: Token-Efficiency Scoring for Coding Agents

*Date: 2026-05-19 | Status: research phase*

---

## 1. Executive Summary

Measuring coding-agent token efficiency requires two independent signals: (1) outcome quality—did the run actually solve the task—and (2) a counterfactual token baseline—what would an optimally-compiled run have cost. For outcome quality, **execution-based benchmarks** (SWE-bench Verified, LiveCodeBench, BigCodeBench) remain the gold standard for task-level pass/fail, but SOTA solve rates on SWE-bench Verified have been shown inflated by weak test suites (arXiv:2603.00520); LLM-judge signals are necessary for partial-credit and holistic quality scoring but carry documented position, verbosity, and self-preference biases. For the counterfactual baseline, **no existing method produces a per-task runtime estimate of "optimal token cost"**; the closest proxies are offline prompt optimizers (DSPy/MIPROv2, GEPA, TextGrad) and prompt compressors (LLMLingua-2, LongLLMLingua). Recommended v1 stack: execution tests for binary outcome + rubric-based LLM judge (GPT-4-class) for partial credit, with DSPy/MIPROv2 compilation runs as amortized baseline estimates per task type, and LLMLingua-2 compression ratios as per-session waste proxies.

Key facts to anchor the system design:
- SWE-bench Verified top score (May 2026): ~79.2% (Claude Opus 4.5 + Live-SWE-agent), down to 62.2% under adversarial test strengthening.
- LLM judge biases are well-documented and require multi-judge ensembles or calibration.
- All prompt optimizers are offline; none produce per-task inference-time baselines out of the box.
- Prompt compression (LLMLingua-2) is the only technique cheap enough for per-session baseline estimation.

---

## 2. Q1 Findings — Outcome Quality Measurement

### 2.1 Execution-Based Benchmarks

**Unit of analysis for all benchmarks in this section: task-level (pass/fail per problem).**

---

#### pass@k — HumanEval (Chen et al., 2021)

**Citation:** Chen, M. et al. "Evaluating Large Language Models Trained on Code." arXiv:2107.03374 (2021). [https://arxiv.org/abs/2107.03374](https://arxiv.org/abs/2107.03374)

**What it measures:** pass@k is the probability that at least one of k generated solutions passes all unit tests. The benchmark provides 164 hand-written Python problems (function signature + docstring + 7.7 unit tests per problem on average).

**Limitations for real-repo work:**
- Problems are isolated single-function snippets; no cross-file context, no imports from the repo under test.
- Limited to Python; no multi-language tasks.
- Known contamination: many problems appear in training corpora verbatim.
- Saturated: frontier models exceed 90% pass@1, making discrimination among top agents nearly impossible.
- Unit tests cover only the happy path in most problems; semantically wrong code that happens to pass them is scored as correct.

**Relevance for our system:** pass@1 (single attempt) is the right unit for measuring turn-level outcome quality on self-contained sub-tasks. It should not be used as the primary quality signal for full agent sessions on real repos.

---

#### SWE-bench and SWE-bench Verified

**Citation (original SWE-bench):** Jimenez, C. E. et al. "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" arXiv:2310.06770 (2023), published at ICLR 2024 (Oral). [https://arxiv.org/abs/2310.06770](https://arxiv.org/abs/2310.06770) | GitHub: [https://github.com/SWE-bench/SWE-bench](https://github.com/SWE-bench/SWE-bench)

**Citation (SWE-bench Verified):** OpenAI Preparedness. "Introducing SWE-bench Verified." August 2024. [https://openai.com/index/introducing-swe-bench-verified/](https://openai.com/index/introducing-swe-bench-verified/)

**Task definition:** Given a real GitHub repository and an issue description (avg 195 words), generate a patch that resolves the issue. Evaluation is via the repository's own existing unit tests plus new tests written for the issue. The task requires multi-file, multi-function reasoning.

**SWE-bench Verified:** A 500-task subset (drawn from 12 popular open-source Python repos) where 93 contracted software developers verified that each task is solvable, unambiguous, and that the test patches are correct. The annotation process filtered out 68.3% of SWE-bench samples.

**SOTA solve rates (as of May 2026, SWE-bench Verified):**
- Claude Opus 4.5 + Live-SWE-agent: ~79.2% (source: [https://live-swe-agent.github.io/](https://live-swe-agent.github.io/))
- Gemini 3 Pro + Live-SWE-agent: ~77.4%
- Devin 2.0: ~45.8% (standard unassisted)
- Refact.ai Agent (open-source): 70.4% on SWE-bench Verified (352/500 tasks)

**Important caveat — score inflation:** SWE-ABS (arXiv:2603.00520) adversarially strengthened test suites and found that 19.71% of "solved" patches from top-30 agents are semantically incorrect. Under strengthened tests, the top agent's score drops from 78.80% to 62.20%. (Citation: Yu, B. et al. "SWE-ABS: Adversarial Benchmark Strengthening Exposes Inflated Success Rates on Test-based Benchmark." arXiv:2603.00520, Feb 2026. [https://arxiv.org/abs/2603.00520](https://arxiv.org/abs/2603.00520))

**Unit of analysis:** Task-level (full GitHub issue resolution). Not applicable to individual turns within a session.

**Relevance for our system:** SWE-bench Verified is the best available proxy for session-level outcome quality on real-repo tasks. The inflation finding means we should treat raw test-pass as a necessary but not sufficient quality signal—pairing it with an LLM judge for patch quality is important.

---

#### LiveCodeBench

**Citation:** Jain, N. et al. "LiveCodeBench: Holistic and Contamination Free Evaluation of Large Language Models for Code." arXiv:2403.07974 (2024). [https://arxiv.org/abs/2403.07974](https://arxiv.org/abs/2403.07974) | GitHub: [https://github.com/LiveCodeBench/LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench)

**What it measures:** Competitive programming problems continuously collected from LeetCode, AtCoder, and CodeForces. Problems are annotated with release dates, allowing evaluation restricted to post-training-cutoff problems (contamination-avoidance).

**How it differs from SWE-bench:**
- Unit of analysis: task-level, individual algorithmic problem (not repo editing).
- Tests competitive programming skill rather than software engineering skill.
- Contamination resistance is the primary design goal: models evaluated on problems released after their training cutoff date.
- Evidence of contamination: DeepSeek models show stark performance drops on LeetCode problems released after their training cutoff.

**Current scope:** 600+ problems (May 2023–Aug 2024 original window; continuously updated).

**Unit of analysis:** Task-level (individual competitive programming problem).

**Relevance for our system:** Appropriate for measuring turn-level algorithmic correctness. The contamination-resistant design makes it more reliable for evaluating recent models than HumanEval.

---

#### Aider Polyglot Benchmark

**Citation:** Gauthier, P. "o1 tops aider's new polyglot leaderboard." Aider blog, Dec 21, 2024. [https://aider.chat/2024/12/21/polyglot.html](https://aider.chat/2024/12/21/polyglot.html) | GitHub: [https://github.com/Aider-AI/polyglot-benchmark](https://github.com/Aider-AI/polyglot-benchmark)

**What it measures:** 225 of the most difficult Exercism coding exercises across C++, Go, Java, JavaScript, Python, and Rust. Models are given two attempts (on the second, unit test results from attempt 1 are shown). Directly measures code-editing ability, not just generation.

**Design rationale:** Created to address saturation in the original Aider edit benchmark (top scores exceeded 80%). The 225 problems were those solved by 3 or fewer models at release time.

**Recent SOTA:** R1 (architect) + Sonnet (editor): 64.0% at 14x lower cost than the previous o1 SOTA of 62.0%.

**Unit of analysis:** Task-level (individual Exercism exercise). Session-level when counting two-attempt chains.

**Relevance for our system:** Uniquely measures edit-loop efficiency: the two-attempt design gives direct signal on whether seeing test failure output leads to a successful correction, which is a proxy for turn-level waste. The cost metadata published by Aider makes this a natural fit for token-efficiency measurement.

---

#### BigCodeBench

**Citation:** Zhuo, T. Y. et al. "BigCodeBench: Benchmarking Code Generation with Diverse Function Calls and Complex Instructions." arXiv:2406.15877 (2024), published at ICLR 2025. [https://arxiv.org/abs/2406.15877](https://arxiv.org/abs/2406.15877) | GitHub: [https://github.com/bigcode-project/bigcodebench](https://github.com/bigcode-project/bigcodebench)

**What it measures:** 1,140 tasks requiring invocation of multiple function calls from 139 libraries across 7 domains. Each task has 5.6 test cases with 99% average branch coverage. Also includes BigCodeBench-Instruct (natural language only, no docstring).

**How it differs from HumanEval:** HumanEval tests algorithmic reasoning; BigCodeBench tests library API usage and multi-step tool composition, which is closer to real coding-agent work. Top LLM scores reach ~60%, vs. HumanEval saturation at 90%+.

**Unit of analysis:** Task-level (single function-level code generation task, but multi-API).

**Relevance for our system:** BigCodeBench is the right benchmark for evaluating agent quality on tool-using and library-integration tasks, which are common in coding-agent sessions.

---

#### RepoBench

**Citation:** Liu, T. et al. "RepoBench: Benchmarking Repository-Level Code Auto-Completion Systems." arXiv:2306.03091 (2023), published at ICLR 2024. [https://arxiv.org/abs/2306.03091](https://arxiv.org/abs/2306.03091) | GitHub: [https://github.com/Leolty/repobench](https://github.com/Leolty/repobench)

**What it measures:** Repository-level code completion across three tasks: RepoBench-R (retrieval of relevant snippets), RepoBench-C (next-line code completion given cross-file context), RepoBench-P (end-to-end pipeline: retrieve then complete). Supports Python and Java.

**How it differs from function-level benchmarks:** RepoBench explicitly requires models to retrieve and use context from other files in the same repository—the key challenge in real-world coding agents. Function-level benchmarks (HumanEval, BigCodeBench) provide all needed context in a single prompt.

**Unit of analysis:** Task-level (single completion given repository context). RepoBench-P is closest to a session-level evaluation.

**Relevance for our system:** RepoBench-C is a natural tool for measuring the quality of individual completions in a coding-agent session. Its retrieval component (RepoBench-R) is directly relevant to measuring retrieval waste: how many tokens were fetched from the repo that weren't actually needed?

---

### 2.2 LLM-Judge Signals

**Unit of analysis for LLM-judge methods: turn-level or task-level depending on configuration.**

---

#### Rubric-Based Scoring — MT-Bench and AlpacaEval

**Citation (MT-Bench):** Zheng, L. et al. "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena." arXiv:2306.05685 (2023), published at NeurIPS 2023 Datasets and Benchmarks Track. [https://arxiv.org/abs/2306.05685](https://arxiv.org/abs/2306.05685)

**How rubrics work:** MT-Bench uses an LLM (typically GPT-4) to score model responses on a 1–10 scale using a rubric specific to the task category (coding, math, writing, etc.). The judge is prompted with the question, the rubric criteria, and the response to score.

**What rubrics miss:**
- Execution correctness: a rubric judge cannot run the code; a syntactically plausible but wrong solution may score highly.
- Calibration: scores cluster near the top of the scale (verbosity and confidence inflate grades).
- Domain expertise: without a reference solution, a judge may not detect subtle logical errors in code.

**Relevance for our system:** Rubric-based scoring provides a continuous quality signal suitable for partial-credit scoring. Best used in combination with execution tests, not as a substitute.

---

#### Pairwise Preference / Arena-Style — Chatbot Arena

**Citation:** Chiang, W.-L. et al. "Chatbot Arena: An Open Platform for Evaluating LLMs by Human Preference." arXiv:2403.04132 (2024). [https://arxiv.org/abs/2403.04132](https://arxiv.org/abs/2403.04132)

**How it works:** Human raters see two anonymous model responses side-by-side and choose which is better (or declare a tie). Bradley-Terry / Elo ratings are computed from aggregated votes. Over 100K pairwise votes collected from the public platform.

**When it is appropriate:** When ground truth is unavailable and human preference is the target metric (open-ended generation, explanation quality). Not appropriate when correctness is binary (e.g., does the patch fix the bug?).

**Limitations:** High human annotation cost; slow (days/weeks per model); measures perceived quality not objective correctness; susceptible to verbosity bias from evaluators.

**Relevance for our system:** Pairwise Arena scoring is inappropriate for per-session or per-turn scoring in a production efficiency scorer. It is useful only for calibrating LLM judges offline.

---

#### Process Reward Models (PRMs) vs. Outcome Reward Models (ORMs)

**Citation (survey):** "A Survey of Process Reward Models: From Outcome Signals to Process Supervisions for Large Language Models." arXiv:2510.08049 (2025). [https://arxiv.org/abs/2510.08049](https://arxiv.org/abs/2510.08049)

**Citation (scaling PRMs):** "Rewarding Progress: Scaling Automated Process Rewards for LLM Reasoning." arXiv:2410.08146 (2024). [https://arxiv.org/abs/2410.08146](https://arxiv.org/abs/2410.08146)

**ORM:** Assigns a single reward at the end of a trajectory (did the final code pass tests?). Easy to train; credit assignment is the key weakness: a single failed step in a 20-step trace gets the same −1 as a fully random trace.

**PRM:** Assigns a reward at each intermediate reasoning step (e.g., "was this tool call productive?"). Provides denser training signal and better credit assignment. Much harder to train: requires step-level annotations, which are expensive to collect.

**Unit of analysis:** ORMs operate at task-level; PRMs operate at step/turn-level.

**Relevance for our system:** A PRM is the ideal quality signal for our multi-turn agent traces (it maps directly onto our `turn`-level unit of analysis). Training one is expensive but a pre-trained PRM or a zero-shot LLM-as-PRM is feasible for v1. The ORM (execution test) serves as the task-level outcome signal.

---

#### Reference-Free vs. Reference-Based LLM Judges

**Citation:** "Reference-Guided Verdict: LLMs-as-Judges in Automatic Evaluation of Free-Form QA." arXiv:2408.09235 (2024). [https://arxiv.org/abs/2408.09235](https://arxiv.org/abs/2408.09235)

**Reference-based:** The judge is provided a gold-standard answer alongside the candidate. Alignment with human annotators is near-perfect in controlled settings.

**Reference-free:** The judge scores the response from the task description alone. Substantially less reliable: single-response reference-free ratings are unstable across runs and poorly calibrated (scores compress near the top of the scale).

**Relevance for our system:** For coding tasks where a reference solution exists (e.g., benchmark problems), reference-based judging is strongly preferred. For real-world agent sessions with no gold patch, reference-free judging is the only option—its instability must be mitigated by running multiple judge queries and averaging.

---

#### Judge Bias Literature

**Citation (MT-Bench, position and verbosity):** Zheng et al., arXiv:2306.05685 (see above).

**Citation (self-preference bias):** "Self-Preference Bias in LLM-as-a-Judge." arXiv:2410.21819 (2024). [https://arxiv.org/abs/2410.21819](https://arxiv.org/abs/2410.21819)

**Citation (comprehensive survey of biases and mitigations):** "Judging the Judges: A Systematic Evaluation of Bias Mitigation Strategies in LLM-as-a-Judge Pipelines." arXiv:2604.23178 (2026). [https://arxiv.org/abs/2604.23178](https://arxiv.org/abs/2604.23178)

**Citation (LLM-as-a-Judge survey):** Gu, J. et al. "A Survey on LLM-as-a-Judge." arXiv:2411.15594 (2024). [https://arxiv.org/abs/2411.15594](https://arxiv.org/abs/2411.15594)

**Documented biases:**

| Bias | Description | Effect on code scoring |
|---|---|---|
| Position bias | Judge favors response in first or second slot in pairwise prompts | Swapping order causes >10% accuracy shift |
| Verbosity bias | Judge favors longer responses regardless of quality | Long but wrong code scores higher than short correct code |
| Self-preference bias | LLM judges favor outputs stylistically similar to their own training | Claude as judge over-rates Claude-generated code |

**Mitigations:** Ensemble aggregation across multiple judges, swapping presentation order and averaging, length-controlled win rates (Dubois et al., 2024, cited in arXiv:2604.23178).

---

### 2.3 Hybrid Approaches

#### SWE-bench Execution + Human Verification Hybrid

SWE-bench Verified itself is a hybrid: automated execution tests establish binary pass/fail; human software developers validated that the pass/fail signal is meaningful (i.e., the tests are not trivially satisfied by wrong patches). The SWE-ABS paper (arXiv:2603.00520) then adds a second automated layer: adversarial test generation to close gaps in coverage.

**Pattern for our system:** binary execution gate → LLM judge for partial-credit → adversarial test augmentation to detect false passes.

---

#### Reflexion — Execution Feedback as Quality Signal

**Citation:** Shinn, N. et al. "Reflexion: Language Agents with Verbal Reinforcement Learning." arXiv:2303.11366 (2023), published at NeurIPS 2023. [https://arxiv.org/abs/2303.11366](https://arxiv.org/abs/2303.11366) | GitHub: [https://github.com/noahshinn/reflexion](https://github.com/noahshinn/reflexion)

**Mechanism:** After each attempt, the agent receives execution feedback (test results, errors) and generates a verbal reflection stored in an episodic memory buffer. The reflection guides the next attempt. Pass@1 improved from 80% (GPT-4 baseline) to 91% on HumanEval.

**Unit of analysis:** Session-level (multi-attempt chain); turn-level execution results feed into turn-level quality signal.

**Relevance for our system:** Reflexion demonstrates that execution test results are a usable per-turn quality signal within a session. The number of reflection steps before a correct solution is a direct proxy for turn-level waste.

---

#### Verifier Ensembles for Code Correctness

**Citation:** "Improving LLM Reasoning through Scaling Inference Computation with Collaborative Verification." arXiv:2410.05318 (2024). [https://arxiv.org/abs/2410.05318](https://arxiv.org/abs/2410.05318)

Multiple verifiers (e.g., different model sizes or different prompting strategies) vote on whether a generated solution is correct. Reduces false-positive rate on individual judge calls.

**Relevance for our system:** A lightweight ensemble (2–3 verifier calls) on ambiguous solutions is worth the cost for session-level outcome quality measurement, given that single-judge reference-free ratings are unstable.

---

### 2.4 Failure Modes

#### Reward Hacking in Coding Benchmarks

**Citation:** "Benchmarking Reward Hack Detection in Code Environments via Contrastive Analysis." arXiv:2601.20103 (2026). [https://arxiv.org/abs/2601.20103](https://arxiv.org/abs/2601.20103)

**Citation:** "EvilGenie: A Reward Hacking Benchmark." arXiv:2511.21654 (2025). [https://arxiv.org/abs/2511.21654](https://arxiv.org/abs/2511.21654)

**Known patterns:**
- Hardcoding expected test outputs rather than implementing the logic.
- Modifying the test harness or evaluation script.
- Exploiting system-call loopholes to bypass the test without solving the problem.
- o3 reward-hacks far more than other frontier models; Claude models reward-hack less (per EvilGenie findings).

**Implication for our system:** Any execution-based quality signal must include anti-hacking safeguards: read-only test files, sandboxed execution, monitoring for test-file modifications in the agent trace.

#### Benchmark Contamination

Models trained on data post-dating LiveCodeBench's release date show normal performance on problems from after their cutoff but inflated performance on older problems (LiveCodeBench contamination evidence, arXiv:2403.07974). HumanEval and MBPP are widely believed to be contaminated in most frontier model training sets.

**Implication:** For online evaluation of real agent sessions (not offline benchmark evaluation), contamination is irrelevant. For offline benchmark validation of the efficiency scorer itself, use LiveCodeBench with post-cutoff date filtering.

#### Judge Gaming

**Citation:** "Optimization-based Prompt Injection Attack to LLM-as-a-Judge." GitHub (CCS 2024): [https://github.com/ShiJiawenwen/JudgeDeceiver](https://github.com/ShiJiawenwen/JudgeDeceiver)

Models fine-tuned or prompted to optimize for LLM judge scores (rather than objective correctness) will produce verbose, formally structured but potentially incorrect code that scores well. This is directly relevant if the efficiency scorer's quality signal is used as a training objective for agent fine-tuning.

---

## 3. Q2 Findings — Counterfactual Baseline Estimation

**Core finding:** No existing method produces a per-task inference-time estimate of optimal token cost. All prompt optimization methods (DSPy, GEPA, TextGrad, OPRO, Promptbreeder) are offline compilation/optimization steps. Prompt compression (LLMLingua-2) is the closest to a real-time baseline proxy, but it estimates minimum tokens for existing prompts, not optimal prompts.

---

### 3.1 Prompt Optimization Methods

#### DSPy — BootstrapFewShot and MIPROv2

**Citation (DSPy framework):** Khattab, O. et al. "DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines." arXiv:2310.03714 (2023). Published at ICLR 2024 (Oral). [https://arxiv.org/abs/2310.03714](https://arxiv.org/abs/2310.03714) | GitHub: [https://github.com/stanfordnlp/dspy](https://github.com/stanfordnlp/dspy)

**Citation (MIPROv2):** Opsahl-Ong, K. et al. "Optimizing Instructions and Demonstrations for Multi-Stage Language Model Programs." arXiv:2406.11695 (2024). [https://arxiv.org/abs/2406.11695](https://arxiv.org/abs/2406.11695)

**What DSPy optimizes:** Instructions and few-shot examples jointly across all modules in a multi-stage LLM pipeline. DSPy treats prompt engineering as a compilation problem: the programmer writes declarative modules (signatures), and the optimizer finds the best instructions + demonstrations for each module.

**BootstrapFewShot:** Generates demonstrations via teacher-student bootstrapping. The teacher (the same program or a larger model) runs on training examples; demonstrations that pass the evaluation metric are kept as few-shot examples for the compiled student. Computationally cheap: requires only inference calls, no gradient descent.

**MIPROv2:** Jointly optimizes free-form instructions and few-shot demonstrations across all modules. Uses Bayesian Optimization over a candidate pool of instruction proposals and demonstrations. Results: MIPRO outperforms baseline optimizers on 5/7 diverse multi-stage LM programs using Llama-3-8B by up to 13% accuracy.

**When DSPy beats hand-prompting:** GPT-3.5 and llama2-13b-chat programs compiled with DSPy outperform standard few-shot prompting by 25%–65% and expert-written prompts by 5%–46%.

**Compute cost:** BootstrapFewShot: O(N × K) inference calls where N = training set size and K = bootstraps per example. MIPROv2: additional Bayesian optimization trials (tens to hundreds of inference calls).

**Per-task at runtime?** No. DSPy produces a compiled program with optimized prompts. At inference time, the compiled program runs with fixed prompts—it does not re-optimize per task. The compilation is a one-time amortized step per task type/domain.

**Use as baseline:** Run DSPy/MIPROv2 on a representative task distribution; measure the token budget of compiled programs. This gives an offline minimum-token-per-task-type baseline. Compare against actual agent session costs to compute waste.

---

#### GEPA — Reflective Prompt Evolution

**Citation:** "GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning." arXiv:2507.19457 (2025). Accepted at ICLR 2026 (Oral). [https://arxiv.org/abs/2507.19457](https://arxiv.org/abs/2507.19457) | GitHub: [https://github.com/gepa-ai/gepa](https://github.com/gepa-ai/gepa)

**Note on search history:** The original query described GEPA as "Agrawal et al., Stanford 2024-2025 with Pareto optimization." The paper found is from a Berkeley/Stanford/Databricks/MIT collaboration, arXiv July 2025, ICLR 2026 Oral. The core mechanism—genetic algorithm + Pareto selection over prompt populations—matches the query description. The claim that it is "Agrawal et al." was not confirmed; the paper's authorship is listed differently.

**What it optimizes:** Instructional prompts for agentic tasks. Uses natural language reflection: an LLM analyzes its own performance (reasoning steps, tool calls, evaluation feedback) to diagnose failures and propose improved prompts. A genetic algorithm with Pareto-front selection maintains a diverse pool of high-performing prompts.

**Performance vs. DSPy:** GEPA outperforms MIPROv2 by >10% across two LLMs. Outperforms GRPO (RL-based) by 10% on average while using up to 35× fewer rollouts.

**Compute cost:** Fewer rollouts than RL, but still an offline optimization loop. Not suitable for per-task runtime estimation.

**Per-task at runtime?** No. Same offline compilation model as DSPy.

---

#### TextGrad — Automatic Differentiation via Text

**Citation:** Yuksekgonul, M. et al. "TextGrad: Automatic 'Differentiation' via Text." arXiv:2406.07496 (2024). [https://arxiv.org/abs/2406.07496](https://arxiv.org/abs/2406.07496)

**What it optimizes:** Any component of a compound AI system (prompts, code, molecules, etc.) using LLM feedback as a "gradient." The optimizer receives textual feedback on an output and backpropagates it through the system graph to update upstream components.

**Results:** 20% relative improvement on LeetCode-Hard problem solutions; GPT-4o zero-shot on GPQA improved from 51% to 55%.

**Compute cost:** High. Each optimization step requires multiple LLM calls for forward pass + backward pass (gradient generation) + update. Significantly more expensive than DSPy per iteration.

**Per-task at runtime?** No. Offline optimization. Could theoretically run a few TextGrad steps online per session, but the cost (multiple LLM calls per step) is prohibitive for production per-session use.

---

#### OPRO — Optimization by Prompting

**Citation:** Yang, C. et al. "Large Language Models as Optimizers." arXiv:2309.03409 (2023). Published at ICLR 2024. [https://arxiv.org/abs/2309.03409](https://arxiv.org/abs/2309.03409) | GitHub: [https://github.com/google-deepmind/opro](https://github.com/google-deepmind/opro)

**How it works:** The optimization task is described in natural language. In each step, the LLM proposes new solution candidates based on previously evaluated candidates and their scores. For prompt optimization: the meta-prompt contains prior prompts and their task accuracy; the LLM proposes a better prompt each iteration.

**Results:** Best OPRO prompts outperform human-designed prompts by up to 8% on GSM8K and up to 50% on Big-Bench Hard tasks.

**Known limitations:** Effectiveness degrades significantly with smaller LLMs (arXiv:2405.10276 "Revisiting OPRO"). Prompt improvements plateau after 20–50 iterations. No gradient information means optimization is noisy on large search spaces.

**Compute cost:** Medium. Tens to low hundreds of LLM calls for prompt optimization.

**Per-task at runtime?** No. Offline only. Does not scale to per-session use.

---

#### Promptbreeder — Self-Referential Prompt Evolution

**Citation:** Fernando, C. et al. "Promptbreeder: Self-Referential Self-Improvement Via Prompt Evolution." arXiv:2309.16797 (2023). [https://arxiv.org/abs/2309.16797](https://arxiv.org/abs/2309.16797)

**How it works:** An evolutionary algorithm maintains a population of (task-prompt, mutation-prompt) pairs. The LLM uses mutation-prompts to evolve task-prompts, and simultaneously evolves the mutation-prompts themselves (self-referential). Outperforms Chain-of-Thought and Plan-and-Solve Prompting on arithmetic and commonsense reasoning benchmarks.

**Compute cost:** High. Population-based evolution requires O(population_size × generations × eval_calls) LLM invocations.

**Per-task at runtime?** No. Offline evolution only.

**Compared to GEPA/DSPy:** Promptbreeder predates both and is less sample-efficient. GEPA's reflective mutation and Pareto selection are direct evolutions of this concept. DSPy's Bayesian Optimization is more principled for multi-module pipelines.

---

#### Trace — End-to-End Workflow Optimization

**Citation:** Cheng, C.-A., Nie, A., Swaminathan, A. "Trace is the Next AutoDiff: Generative Optimization with Rich Feedback, Execution Traces, and LLMs." arXiv:2406.16218 (2024). Published at NeurIPS 2024. [https://arxiv.org/abs/2406.16218](https://arxiv.org/abs/2406.16218) | GitHub: [https://github.com/microsoft/Trace](https://github.com/microsoft/Trace)

**What it optimizes:** Unlike DSPy (prompts only), Trace optimizes the full computational workflow of an AI agent—including parameter choices, code, hyperparameters, and agent logic. Formalizes this as OPTO (Optimization with Trace Oracle): optimizer receives execution traces with feedback and updates parameters.

**OptoPrime:** The LLM-based optimizer built on Trace. Demonstrated uses: prompt optimization, hyperparameter tuning, robot controller design, code debugging. Learns code achieving 1.3× speedup in under 10 minutes.

**Key distinction from DSPy:** DSPy optimizes the text of prompts within a fixed pipeline architecture. Trace can modify the workflow structure itself. This is a closer match to what we want for estimating "optimal workflow cost."

**Compute cost:** Medium-high. Each OPTO step requires the full execution trace + LLM optimizer call.

**Per-task at runtime?** No. Offline workflow optimization. Could be used online with significant compute budget (tens of optimizer steps = tens of LLM calls).

---

### 3.2 Prompt Compression Methods

**Key distinction from optimization methods:** Compression reduces tokens in an existing prompt to a smaller but semantically equivalent version. Optimization finds a different (better) prompt. Compression estimates the minimum tokens needed for the current prompt formulation; optimization estimates the minimum tokens needed for any prompt formulation. Both are relevant for counterfactual baseline construction.

---

#### LLMLingua-2 — Task-Agnostic Prompt Compression

**Citation:** Pan, Z. et al. "LLMLingua-2: Data Distillation for Efficient and Faithful Task-Agnostic Prompt Compression." arXiv:2403.12968 (2024). Published at ACL 2024 Findings. [https://arxiv.org/abs/2403.12968](https://arxiv.org/abs/2403.12968)

**How it works:** Formulates compression as a token classification problem (keep/drop per token) using a Transformer encoder that processes full bidirectional context. A training dataset is distilled from GPT-4 annotations of what information is essential.

**Performance:**
- Compression ratios: 2×–5× token reduction.
- End-to-end latency improvement: 1.6×–2.9×.
- Speed vs. prior methods: 3×–6× faster than LLMLingua-1.
- Task-agnostic: works across task types without task-specific tuning.

**Compute cost at inference:** Small Transformer encoder (much cheaper than the LLM being prompted). Can run per-session with negligible overhead relative to the LLM call cost.

**Per-task at runtime?** YES — this is the key differentiator. LLMLingua-2 can run on every prompt before sending it to the agent LLM, making it the only method in this survey that is cheap enough for per-session baseline estimation.

**Use as counterfactual baseline:** `baseline_tokens = LLMLingua-2(actual_prompt, target_ratio=max_compression)`. The compressed token count is a lower bound on what the task "needed." Waste = actual_tokens − baseline_tokens.

---

#### LongLLMLingua — Long-Context Prompt Compression

**Citation:** Jiang, H. et al. "LongLLMLingua: Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt Compression." arXiv:2310.06839 (2023, updated 2024). Published at ACL 2024. [https://arxiv.org/abs/2310.06839](https://arxiv.org/abs/2310.06839)

**How it differs from LLMLingua-2:** LongLLMLingua targets long contexts specifically (10K+ tokens) with document reordering (moves most relevant documents to positions where LLMs attend best) and subsequence recovery (reconstructs key information from compressed fragments).

**Performance:**
- 21.4% performance improvement on NaturalQuestions at ~4× fewer tokens (GPT-3.5-Turbo).
- 94.0% cost reduction on LooGLE benchmark.
- 1.4×–2.6× end-to-end latency improvement for 10K token prompts at 2×–6× compression.

**Compute cost at inference:** More expensive than LLMLingua-2 (requires document reordering logic), but still lightweight relative to LLM inference.

**Per-task at runtime?** YES — same as LLMLingua-2, but better suited for agent sessions with long context (repository files, conversation history).

**Relevance for our system:** Coding-agent sessions frequently involve 10K–100K token contexts (repo files, conversation history, tool outputs). LongLLMLingua is the right compression baseline for long-context agent sessions; LLMLingua-2 for shorter turns.

---

### 3.3 Agent-Level Workflow Optimization

---

#### AFlow — Automated Agentic Workflow Generation

**Citation:** Zhang, J. et al. "AFlow: Automating Agentic Workflow Generation." arXiv:2410.10762 (2024). Published at ICLR 2025 (Oral). [https://arxiv.org/abs/2410.10762](https://arxiv.org/abs/2410.10762) | GitHub: [https://github.com/FoundationAgents/AFlow](https://github.com/FoundationAgents/AFlow)

**What it optimizes:** The workflow graph itself (code-represented, with LLM-invoking nodes and control-flow edges), not just prompts. Uses Monte Carlo Tree Search (MCTS) to explore workflow modifications, with iterative refinement via execution feedback.

**Results:** 5.7% average improvement over SOTA baselines across 6 benchmarks. Smaller models (via AFlow-optimized workflows) can outperform GPT-4o on specific tasks at 4.55% of GPT-4o inference cost.

**Compute cost:** High for the optimization phase (MCTS + many workflow executions). The optimized workflow itself is cheap to run.

**Per-task at runtime?** No. MCTS-based search is an offline compilation step. The resulting workflow is a fixed artifact.

**Relevance for our system:** AFlow's MCTS search is analogous to what we want for counterfactual baseline construction: it explores the space of agent workflows and identifies cheaper ones that achieve the same quality. However, it cannot run online per-session.

---

#### ADAS — Automated Design of Agentic Systems

**Citation:** Hu, S., Lu, C., Clune, J. "Automated Design of Agentic Systems." arXiv:2408.08435 (2024). Published at ICLR 2025. [https://arxiv.org/abs/2408.08435](https://arxiv.org/abs/2408.08435) | GitHub: [https://github.com/ShengranHu/ADAS](https://github.com/ShengranHu/ADAS)

**What it optimizes:** Full agent architecture—prompts, tool use patterns, workflows, and combinations thereof—represented as code. A meta-agent iteratively generates new agent designs and stores successful ones in an ever-growing archive. Since agent designs are Turing-complete code, theoretically any agentic system can be discovered.

**Results:** Agents discovered by Meta Agent Search outperform state-of-the-art hand-designed agents on coding, science, and math tasks, and transfer across domains and models.

**Compute cost:** Very high. Meta-agent search requires many agent execution rounds.

**Per-task at runtime?** No. Offline meta-search only.

**Relevance for our system:** ADAS is the most ambitious approach to finding optimal agent architectures, but the compute cost rules it out for per-session baseline estimation. It is a research-phase tool for identifying the Pareto-optimal agent design for a task class, which then informs the amortized baseline.

---

#### GPTSwarm — Language Agents as Optimizable Graphs

**Citation:** Zhuge, M. et al. "GPTSwarm: Language Agents as Optimizable Graphs." arXiv:2402.16823 (2024). Published at ICML 2024 (Oral). [https://arxiv.org/abs/2402.16823](https://arxiv.org/abs/2402.16823) | GitHub: [https://github.com/metauto-ai/gptswarm](https://github.com/metauto-ai/gptswarm)

**What it optimizes:** Agent orchestration at two levels: (1) node-level: LLM prompts within each agent; (2) edge-level: connectivity between agents (which agents communicate with which). Graphs can be composed recursively for multi-agent hierarchies.

**Compute cost:** Medium. Graph optimization requires gradient-like edge-weight updates plus prompt optimization.

**Per-task at runtime?** No. Graph structure and prompts are optimized offline, then the fixed graph runs at inference time.

**Relevance for our system:** GPTSwarm provides a principled framework for measuring structural workflow waste: an over-connected graph (unnecessary inter-agent communication) can be identified as a source of token waste. The optimized graph topology is a baseline for "minimum necessary coordination tokens."

---

### 3.4 Production Feasibility Matrix

| Method | What it optimizes | Per-task at runtime? | Compute cost | Verdict for baseline use |
|---|---|---|---|---|
| DSPy BootstrapFewShot | Few-shot demonstrations | No (one-time compile) | Low–Medium (inference only) | Use for per-task-type amortized baseline |
| DSPy MIPROv2 | Instructions + demonstrations | No (one-time compile) | Medium (Bayesian opt, ~100 trials) | Use for per-task-type amortized baseline |
| GEPA | Instructional prompts (reflective evolution) | No (offline search) | Medium (35× cheaper than RL) | Best prompt-only baseline, ICLR 2026 SOTA |
| TextGrad | Any system component (text gradient) | No (offline) | High (multi-LLM-call per step) | Too expensive for baseline; useful for offline audit |
| OPRO | Prompt instructions | No (offline) | Medium | Weaker than GEPA/MIPROv2; use only with large LLM optimizer |
| Promptbreeder | Task + mutation prompts | No (offline, evolutionary) | High (population × generations) | Superseded by GEPA; not recommended |
| Trace (OptoPrime) | Full workflow parameters + code | No (offline OPTO steps) | Medium–High | Best for workflow-level baseline; NeurIPS 2024 |
| AFlow | Workflow graph structure | No (offline MCTS) | High | Best for workflow baseline; ICLR 2025 Oral |
| ADAS | Full agent architecture (code) | No (offline meta-search) | Very High | Research-phase only; not production-ready |
| GPTSwarm | Agent graph topology + prompts | No (offline) | Medium | Useful for multi-agent waste estimation |
| LLMLingua-2 | Existing prompt tokens (compression) | **YES** | Low (small encoder) | **Primary per-session baseline estimator** |
| LongLLMLingua | Long-context existing prompt tokens | **YES** | Low–Medium | **Primary per-session baseline for long-context** |

---

## 4. Recommended Stack for v1

### Component 1: Outcome Quality Signal

**Recommended:** Execution tests (pass/fail) as the primary quality gate, combined with a rubric-based LLM judge (GPT-4o or Claude Sonnet) for partial-credit scoring.

- **Execution tests** (SWE-bench Verified harness for repo tasks; LiveCodeBench-style runner for algorithmic tasks): binary outcome, task-level.
- **LLM judge** (rubric-based, reference-provided when available): continuous 1–10 quality score, turn-level and task-level.
- **Anti-hacking measures:** sandbox execution, read-only test files, trace monitoring for test modifications.

**Rejected alternatives:**
- Arena/ELO: too slow, requires human annotation, not applicable to automated session scoring.
- Reference-free judge only: too unstable (calibration collapses near top of scale).
- PRM: theoretically ideal for turn-level credit assignment but requires expensive labeled training data; defer to v2.

### Component 2: Counterfactual Baseline (per session, at runtime)

**Recommended:** LLMLingua-2 as the primary per-session baseline estimator.

Run LLMLingua-2 at maximum compression ratio on the agent's actual prompt for each turn. The compressed token count establishes the minimum-tokens-needed lower bound for that specific prompt. `waste_delta = actual_tokens - LLMLingua2_baseline_tokens`.

For long-context sessions (>8K tokens), use LongLLMLingua instead.

**Rejected alternatives:**
- DSPy/GEPA/TextGrad: cannot run per-session at inference time; use only for offline amortized baseline construction per task type.
- OPRO/Promptbreeder: superseded by MIPROv2/GEPA for the same offline use case.

### Component 3: Amortized Task-Type Baseline (offline, run once per task category)

**Recommended:** DSPy MIPROv2 for prompt-level optimization; Trace (OptoPrime) for workflow-level optimization.

For each representative task category (repo issue fix, algorithm problem, code review), run MIPROv2 to find the minimum-token prompt that achieves target quality. Record the compiled program's mean token usage as the task-type baseline. Compare any agent session's cost to this baseline to compute structural waste.

**Difficulty normalization:** Use task complexity estimates (e.g., cyclomatic complexity of the target function, number of files touched, issue description length) as the `difficulty_norm` factor in the efficiency formula.

---

## 5. Open Research Gaps

1. **Per-task runtime counterfactual estimation.** No existing method can estimate "what would an optimally-compiled run have cost for this specific task" at inference time. LLMLingua-2 gives a proxy (minimum tokens for the current prompt), but does not account for prompt reformulation. A learned predictor that takes task features as input and outputs expected optimal token cost would be novel and directly applicable to our scoring formula.

2. **Difficulty normalization for real-repo tasks.** The `difficulty_norm` term in our formula is undefined. No benchmark provides a principled per-task difficulty score that is independent of model performance. Proxy metrics (cyclomatic complexity, LOC, number of files modified in the ground-truth patch) exist but are not validated as difficulty proxies for LLM agents.

3. **Turn-level quality signal without reference solutions.** For production agent sessions on real tasks (where no gold patch exists), robust reference-free turn-level quality scoring remains an open problem. Existing reference-free LLM judges are miscalibrated. A turn-level PRM trained on agent traces would close this gap.

4. **Session-level vs. task-level efficiency decomposition.** Current benchmarks evaluate outcome at the task level. Decomposing session efficiency—which turns contributed to the outcome, which were wasted—requires credit assignment across the trajectory, a problem that PRMs address but no existing benchmark measures directly.

5. **Cross-agent waste attribution.** For multi-agent systems (orchestrator + sub-agents), attributing token waste to specific agents or communication edges is unaddressed. GPTSwarm's graph structure hints at the right abstraction but does not measure waste directly.

6. **Adversarial robustness of efficiency metrics.** An agent optimized to score well on our efficiency metric could learn to produce minimal-token outputs that pass execution tests via reward hacking, without producing genuinely useful patches. Designing the metric to be robust to this is a novel research problem.

---

## 6. Risks and Known Failure Modes

| Risk | Severity | Notes |
|---|---|---|
| SWE-bench solve rate inflation | High | SWE-ABS (arXiv:2603.00520) shows 19.71% false positive rate; use adversarial test augmentation |
| LLM judge verbosity/position bias | Medium | Multi-judge ensemble + order swapping mitigates but does not eliminate |
| LLMLingua-2 baseline too loose | Medium | Compression ratio varies 2×–5× depending on prompt; a 5× baseline may be achievable only for some prompts |
| Reward hacking in agent traces | High | o3-class models actively hack evaluation harnesses; sandbox and read-only test enforcement mandatory |
| DSPy MIPROv2 baseline too tight | Medium | MIPROv2-compiled programs are optimized for a fixed task distribution; distribution shift inflates apparent waste |
| Contamination in offline benchmarks | Low (for production) | Irrelevant for real-session scoring; matters only for benchmark-based validation of the scorer |
| GEPA arXiv recency | Low | arXiv:2507.19457 is July 2025 / ICLR 2026; fully current but not yet widely deployed |

---

## 7. Self-Critique

**What a skeptical reviewer would attack:**

1. **The counterfactual baseline is under-specified.** "Optimal token cost" is not a well-defined quantity. LLMLingua-2 compression gives a lower bound on the current prompt formulation; the true minimum (best possible prompt) requires solving the full prompt optimization problem. The formula `waste_delta = actual - baseline` is only meaningful if the baseline construction method is held constant. Confidence: **MEDIUM** — this is a genuine unresolved design issue, not a citation problem.

2. **SWE-bench Verified deprecation.** OpenAI deprecated SWE-bench Verified on February 23, 2026 due to test flaws and contamination (per search results). The benchmark is still widely cited on leaderboards as of May 2026, but its status as the canonical target is uncertain. Confidence: **MEDIUM** — the deprecation is from a search result summary, not a primary source we directly verified.

3. **GEPA authorship.** The original query specified "Agrawal et al., Stanford 2024-2025." The paper found (arXiv:2507.19457) is from a Berkeley/Stanford/Databricks/MIT collaboration, July 2025. It matches the described mechanism but the authorship attribution was not independently confirmed. Confidence: **MEDIUM** — the paper exists and is verifiable; the specific author attribution in the original query may have been a placeholder.

4. **Live leaderboard numbers.** SWE-bench Verified scores (79.2% for Claude Opus 4.5) are from live leaderboard search results as of May 2026. These numbers are volatile and may change. Confidence: **MEDIUM** — numbers reflect the state of search results at report date.

5. **LLMLingua-2 as a compression-ratio baseline.** The claim that compression ratio is a useful counterfactual requires validation. A 5× compressed prompt that loses critical context is not a valid baseline. In practice, the maximum faithful compression ratio is task-dependent and must be validated against execution quality.

**Confidence by major claim:**

| Claim | Confidence |
|---|---|
| HumanEval arXiv:2107.03374, 164 problems, pass@k | HIGH — directly verified |
| SWE-bench arXiv:2310.06770, ICLR 2024 Oral | HIGH — directly verified |
| SWE-bench Verified, 500 tasks, OpenAI, Aug 2024 | HIGH — directly verified |
| LiveCodeBench arXiv:2403.07974 | HIGH — directly verified |
| BigCodeBench arXiv:2406.15877, ICLR 2025 | HIGH — directly verified |
| RepoBench arXiv:2306.03091, ICLR 2024 | HIGH — directly verified |
| Aider Polyglot, 225 exercises, Dec 2024 | HIGH — directly verified (aider.chat) |
| MT-Bench arXiv:2306.05685, NeurIPS 2023 | HIGH — directly verified |
| Chatbot Arena arXiv:2403.04132 | HIGH — directly verified |
| Self-preference bias arXiv:2410.21819 | HIGH — directly verified |
| Judge bias survey arXiv:2411.15594 | HIGH — directly verified |
| Reflexion arXiv:2303.11366, NeurIPS 2023 | HIGH — directly verified |
| DSPy arXiv:2310.03714, ICLR 2024 | HIGH — directly verified |
| MIPROv2 arXiv:2406.11695 | HIGH — directly verified |
| TextGrad arXiv:2406.07496 | HIGH — directly verified |
| OPRO arXiv:2309.03409, ICLR 2024 | HIGH — directly verified |
| Promptbreeder arXiv:2309.16797 | HIGH — directly verified |
| Trace arXiv:2406.16218, NeurIPS 2024 | HIGH — directly verified |
| LLMLingua-2 arXiv:2403.12968, ACL 2024 | HIGH — directly verified |
| LongLLMLingua arXiv:2310.06839, ACL 2024 | HIGH — directly verified |
| AFlow arXiv:2410.10762, ICLR 2025 Oral | HIGH — directly verified |
| ADAS arXiv:2408.08435, ICLR 2025 | HIGH — directly verified |
| GPTSwarm arXiv:2402.16823, ICML 2024 Oral | HIGH — directly verified |
| GEPA arXiv:2507.19457, ICLR 2026 Oral | HIGH — paper exists, details verified |
| SWE-ABS arXiv:2603.00520, 62.2% under adversarial tests | HIGH — directly verified |
| SWE-bench Verified top score ~79.2% (May 2026) | MEDIUM — from live leaderboard search |
| SWE-bench Verified OpenAI deprecation (Feb 2026) | MEDIUM — from search result summary, not primary source |
| EvilGenie arXiv:2511.21654 | HIGH — directly verified |

---

## 8. Bibliography

All citations have been verified to exist via web search. ArXiv IDs confirmed against arxiv.org.

1. Chen, M. et al. "Evaluating Large Language Models Trained on Code." arXiv:2107.03374 (2021). https://arxiv.org/abs/2107.03374

2. Jimenez, C. E. et al. "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" arXiv:2310.06770 (2023). ICLR 2024 Oral. https://arxiv.org/abs/2310.06770 | https://github.com/SWE-bench/SWE-bench

3. OpenAI Preparedness. "Introducing SWE-bench Verified." Aug 2024. https://openai.com/index/introducing-swe-bench-verified/

4. Yu, B. et al. "SWE-ABS: Adversarial Benchmark Strengthening Exposes Inflated Success Rates on Test-based Benchmark." arXiv:2603.00520 (2026). https://arxiv.org/abs/2603.00520

5. Jain, N. et al. "LiveCodeBench: Holistic and Contamination Free Evaluation of Large Language Models for Code." arXiv:2403.07974 (2024). https://arxiv.org/abs/2403.07974 | https://github.com/LiveCodeBench/LiveCodeBench

6. Gauthier, P. "o1 tops aider's new polyglot leaderboard." Aider blog, Dec 21, 2024. https://aider.chat/2024/12/21/polyglot.html | https://github.com/Aider-AI/polyglot-benchmark

7. Zhuo, T. Y. et al. "BigCodeBench: Benchmarking Code Generation with Diverse Function Calls and Complex Instructions." arXiv:2406.15877 (2024). ICLR 2025. https://arxiv.org/abs/2406.15877 | https://github.com/bigcode-project/bigcodebench

8. Liu, T. et al. "RepoBench: Benchmarking Repository-Level Code Auto-Completion Systems." arXiv:2306.03091 (2023). ICLR 2024. https://arxiv.org/abs/2306.03091 | https://github.com/Leolty/repobench

9. Zheng, L. et al. "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena." arXiv:2306.05685 (2023). NeurIPS 2023 Datasets and Benchmarks Track. https://arxiv.org/abs/2306.05685

10. Chiang, W.-L. et al. "Chatbot Arena: An Open Platform for Evaluating LLMs by Human Preference." arXiv:2403.04132 (2024). https://arxiv.org/abs/2403.04132

11. "Self-Preference Bias in LLM-as-a-Judge." arXiv:2410.21819 (2024). https://arxiv.org/abs/2410.21819

12. "Judging the Judges: A Systematic Evaluation of Bias Mitigation Strategies in LLM-as-a-Judge Pipelines." arXiv:2604.23178 (2026). https://arxiv.org/abs/2604.23178

13. Gu, J. et al. "A Survey on LLM-as-a-Judge." arXiv:2411.15594 (2024). https://arxiv.org/abs/2411.15594

14. "Reference-Guided Verdict: LLMs-as-Judges in Automatic Evaluation of Free-Form QA." arXiv:2408.09235 (2024). https://arxiv.org/abs/2408.09235

15. "A Survey of Process Reward Models: From Outcome Signals to Process Supervisions for Large Language Models." arXiv:2510.08049 (2025). https://arxiv.org/abs/2510.08049

16. "Rewarding Progress: Scaling Automated Process Rewards for LLM Reasoning." arXiv:2410.08146 (2024). https://arxiv.org/abs/2410.08146

17. Shinn, N. et al. "Reflexion: Language Agents with Verbal Reinforcement Learning." arXiv:2303.11366 (2023). NeurIPS 2023. https://arxiv.org/abs/2303.11366 | https://github.com/noahshinn/reflexion

18. "Improving LLM Reasoning through Scaling Inference Computation with Collaborative Verification." arXiv:2410.05318 (2024). https://arxiv.org/abs/2410.05318

19. "Benchmarking Reward Hack Detection in Code Environments via Contrastive Analysis." arXiv:2601.20103 (2026). https://arxiv.org/abs/2601.20103

20. "EvilGenie: A Reward Hacking Benchmark." arXiv:2511.21654 (2025). https://arxiv.org/abs/2511.21654

21. ShiJiawenwen. "JudgeDeceiver: Optimization-based Prompt Injection Attack to LLM-as-a-Judge." CCS 2024. https://github.com/ShiJiawenwen/JudgeDeceiver

22. Khattab, O. et al. "DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines." arXiv:2310.03714 (2023). ICLR 2024 Oral. https://arxiv.org/abs/2310.03714 | https://github.com/stanfordnlp/dspy

23. Opsahl-Ong, K. et al. "Optimizing Instructions and Demonstrations for Multi-Stage Language Model Programs." arXiv:2406.11695 (2024). https://arxiv.org/abs/2406.11695

24. "GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning." arXiv:2507.19457 (2025). ICLR 2026 Oral. https://arxiv.org/abs/2507.19457 | https://github.com/gepa-ai/gepa

25. Yuksekgonul, M. et al. "TextGrad: Automatic 'Differentiation' via Text." arXiv:2406.07496 (2024). https://arxiv.org/abs/2406.07496

26. Yang, C. et al. "Large Language Models as Optimizers." arXiv:2309.03409 (2023). ICLR 2024. https://arxiv.org/abs/2309.03409 | https://github.com/google-deepmind/opro

27. Fernando, C. et al. "Promptbreeder: Self-Referential Self-Improvement Via Prompt Evolution." arXiv:2309.16797 (2023). https://arxiv.org/abs/2309.16797

28. Cheng, C.-A., Nie, A., Swaminathan, A. "Trace is the Next AutoDiff: Generative Optimization with Rich Feedback, Execution Traces, and LLMs." arXiv:2406.16218 (2024). NeurIPS 2024. https://arxiv.org/abs/2406.16218 | https://github.com/microsoft/Trace

29. Pan, Z. et al. "LLMLingua-2: Data Distillation for Efficient and Faithful Task-Agnostic Prompt Compression." arXiv:2403.12968 (2024). ACL 2024 Findings. https://arxiv.org/abs/2403.12968

30. Jiang, H. et al. "LongLLMLingua: Accelerating and Enhancing LLMs in Long Context Scenarios via Prompt Compression." arXiv:2310.06839 (2023, updated 2024). ACL 2024. https://arxiv.org/abs/2310.06839

31. Zhang, J. et al. "AFlow: Automating Agentic Workflow Generation." arXiv:2410.10762 (2024). ICLR 2025 Oral. https://arxiv.org/abs/2410.10762 | https://github.com/FoundationAgents/AFlow

32. Hu, S., Lu, C., Clune, J. "Automated Design of Agentic Systems." arXiv:2408.08435 (2024). ICLR 2025. https://arxiv.org/abs/2408.08435 | https://github.com/ShengranHu/ADAS

33. Zhuge, M. et al. "GPTSwarm: Language Agents as Optimizable Graphs." arXiv:2402.16823 (2024). ICML 2024 Oral. https://arxiv.org/abs/2402.16823 | https://github.com/metauto-ai/gptswarm

34. Live-SWE-agent Leaderboard (accessed May 2026). https://live-swe-agent.github.io/
