# data/

Agent traces, corpus data, and derived research artifacts. This description was
stale (referenced a `raw/`/`processed/`/`benchmarks/` layout — SWE-bench/HumanEval/
LiveCodeBench task definitions — that was never actually populated; corrected AW1).

- `corpus_pool/` — the author's own self-collected Claude Code sessions (the B1-B4
  calibration corpus), gitignored raw, committed as `pool_adapted.jsonl`.
- `swechat_raw/`, `swechat_cc_adapted.jsonl`, `swechat_noncc_adapted.jsonl` — the
  SWE-chat (SALT-NLP/SWE-chat, ODC-BY licensed) generalization corpus and its
  adapted forms — see [`../DATA_SOURCES.md`](../DATA_SOURCES.md) for the license
  and required attribution. `swechat_raw/` and both adapted `.jsonl` files are
  gitignored (never committed — only derived aggregate statistics are published,
  per the attribution note).
- `cc_baselines.json` — the token-economy scope-gate floors and reference bands
  derived from `corpus_pool/` (self-collected, no third-party licensing question).
- Everything else (`*_scores.jsonl`, `*_signals.jsonl`, validation/gold/ood-corpus
  directories, run logs) is intermediate/derived research output from the B1-B5
  research arc — see `research/*.md` for what produced each.
