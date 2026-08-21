# Data sources

Every external (non-self-collected) dataset used anywhere in this project, its
license, and what was derived from it. Added AW1 in response to a real,
previously-uncredited attribution gap — see the entry below.

## SWE-chat (SALT-NLP/SWE-chat)

> Contains information from [SALT-NLP/SWE-chat](https://huggingface.co/datasets/SALT-NLP/SWE-chat),
> which is made available under the [Open Data Commons Attribution License (ODC-BY) 1.0](https://opendatacommons.org/licenses/by/1-0/).

- **What it is:** a public corpus of real coding-agent sessions collected from
  real developers by the Entire.io CLI checkpoint logger.
- **Citation:** Baumann et al., 2026, arXiv:2604.20779.
- **License:** ODC-BY 1.0. Under §4.3 ("Produced Works"), publishing aggregate
  statistics computed from the data — not just redistributing the data itself —
  requires this notice. Verified directly against the license text
  (opendatacommons.org/licenses/by/1-0/) rather than assumed.
- **What was derived from it:** the B5 generalization validation
  (`research/11-generalization.md`) — the "172 independent developers, 1,053
  SWE-chat CC sessions" figures and the 1.4% (SWE-chat) vs. 6.6% (calibration
  pool) REPEATED-FAILED-RETRY rate comparison reported in README.md and
  `research/09-cross-model.md`/`research/10-deterministic-waste.md`.
- **Redistribution:** none. The raw SWE-chat data (`data/swechat_raw/`) and both
  adapted forms (`data/swechat_cc_adapted.jsonl`, `data/swechat_noncc_adapted.jsonl`)
  are gitignored and never committed to this repo — only the derived aggregate
  statistics are published, which is why this is purely a §4.3 (Produced Works)
  attribution question, not a §4.2 (Derivative Databases) redistribution question.

## Open-weight models used for local/offline inference

Listed for transparency (rule 72: LLM usage is documented, not hidden) — none of
these carry an attribution or redistribution obligation for how this project
uses them (local inference only, publishing aggregate results, no weight
redistribution or fine-tuned-derivative distribution).

| Model | Role | License | Obligation for this project's usage |
|---|---|---|---|
| Qwen3-30B-A3B | Trajectory judge (`tes score --judge`, local Ollama) | Apache 2.0 | None — permissive, no redistribution here |
| Gemma 3 27B | Cross-model corroboration check (`research/09-cross-model.md`) | Gemma Terms of Use | None — §3.1's distribution clause only triggers on redistributing the weights or a derivative model; not triggered by local inference + publishing results |
| gpt-oss-120b | Groq free-tier judge path | Apache 2.0 | None — permissive, no redistribution here |

## Self-collected data (no third-party licensing question)

- `data/corpus_pool/` (`pool_adapted.jsonl`, 181 sessions) — the author's own
  Claude Code sessions, the B1-B4 calibration corpus. Not third-party data.
- Anthropic model pricing (`tes/data/prices.json`) — public list prices, not a
  licensed dataset; each active entry carries its own `source_url`/`as_of` for
  the staleness guard (see the file's own `note` field for the full convention,
  including why retired entries are exempt).
