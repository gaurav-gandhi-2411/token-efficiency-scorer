# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Generate the synthetic scoring fixtures the test suite runs against (seed 42).

Everything this writes is invented. No line of it comes from a real coding session: task
descriptions are assembled from the phrase tables below, tool results are fixed strings, token
counts are drawn from a seeded RNG. The point is to give the scorer, the baseline builder and the
waste detectors a pool that exercises every branch they have (all five task types, in-scope and
out-of-scope sessions, all four band verdicts, judged and unjudged sessions, each waste family)
without shipping anybody's session text.

Outputs (committed, deterministic: re-running must leave `git diff` empty):

    tests/fixtures/synthetic_pool.jsonl           adapted records, same schema as an adapted session
    tests/fixtures/synthetic_judge_scores.jsonl   invented judge verdicts for a subset of the pool

Then build the synthetic baselines and the golden file from them:

    python scripts/build_baselines.py --pool tests/fixtures/synthetic_pool.jsonl \
        --judge-scores tests/fixtures/synthetic_judge_scores.jsonl \
        --output tests/fixtures/baselines_synthetic.json --min-n 4 --label synthetic
    python tests/capture_golden.py

Waste entries are not stored anywhere: capture_golden.py runs the real detectors over the
fixture digests, so the fixtures cannot drift from the detector behaviour they exercise.
"""

import argparse
import json
import random
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tes.classify import CAVEAT_PREFIX, classify_session  # noqa: E402

SEED = 42  # fixed: the committed fixtures must be reproducible byte for byte
FIXTURES = REPO_ROOT / "tests" / "fixtures"
POOL_OUT = FIXTURES / "synthetic_pool.jsonl"
JUDGE_OUT = FIXTURES / "synthetic_judge_scores.jsonl"

SNIPPET_MAX = 300  # the adapter truncates content_snippet to this length

# ---------------------------------------------------------------------------
# Phrase tables. Each description must classify (tes.classify) as its task type; the
# generator asserts that, so a keyword added to the classifier later fails here, loudly.
# ---------------------------------------------------------------------------

DESCRIPTIONS: dict[str, list[str]] = {
    "ml-eval": [
        "Run the hyperparameter sweep for the toy image classifier and report accuracy per epoch.",
        "Compare two toy models on the held-out dataset and tabulate the metric for each run.",
        "Fine-tune the small demo model for three epochs and record the validation accuracy.",
        "Set up an experiment that varies the learning rate and log every run to the tracker.",
        "Benchmark the sample ranking model against the baseline and summarise the metrics.",
    ],
    "debug-fix": [
        "Fix the crash when the sample parser receives an empty input file.",
        "The unit test for the demo tokenizer is failing on unicode input; find and fix the bug.",
        "Users report a wrong total on the sample invoice page; correct the rounding bug.",
        "Debug why the demo cache returns stale entries after the second update.",
        "The sample command line tool raises an exception when the output folder is missing.",
    ],
    "infra-deploy": [
        "Deploy the demo service to the staging cluster and update its Dockerfile.",
        "Provision a small virtual machine for the sample worker and wire up its startup script.",
        "Add a GitHub Action that builds the sample image and pushes it to the registry.",
        "Set up the terraform module for the demo bucket and the service account it needs.",
        "Move the sample job to Cloud Run and configure its secret and service permissions.",
    ],
    "research-recon": [
        "Write a short summary of the module layout and list the public functions.",
        "Survey the sample repository and produce a report of its main components.",
        "Take an inventory of the example scripts and note which ones are unused.",
        "Review the demo library at a high level and give me a status overview.",
        "Scan the sample project folders and summarize what each one is for.",
    ],
    "feature-build": [
        "Add a dark-mode toggle to the settings page of the sample web app.",
        "Build a small command that prints the sample project's version and licence.",
        "Create a form component for the demo app that collects a name and a colour.",
        "Implement pagination on the sample list view with a page size selector.",
        "Add a keyboard shortcut that opens the search box in the demo editor.",
    ],
}

AI_TEXT = [
    "Looking at the layout first.",
    "That gives me what I need; moving on to the next step.",
    "Checking the result before continuing.",
    "Updating the plan based on what I found.",
    "Running the next command.",
    "Reading the relevant module now.",
]
GLOB_RESULTS = [
    "src/demo/app.py\nsrc/demo/util.py\ntests/test_app.py",
    "docs/overview.md\ndocs/usage.md",
    "scripts/run_demo.sh\nscripts/setup_demo.sh",
]
GREP_RESULTS = [
    "src/demo/app.py:12:def run(config):\nsrc/demo/util.py:40:def load(path):",
    "tests/test_app.py:8:def test_run():",
]
EDIT_RESULT = "The file src/demo/app.py has been updated successfully."
OK_BASH_RESULTS = ["done", "3 passed in 0.41s", "ok", "Everything up to date"]

# Waste scripts. Their wording is chosen so the real detectors (tes/_waste_detectors.py)
# fire, or deliberately do not fire for the near-miss controls.
RFR_ERROR = (
    "Exit code 2\npython: can't open file 'sample_job.py': [Errno 2] No such file or directory"
)
RFR_ERROR_B = "Exit code 1\nfatal: not a git repository (or any of the parent directories): .git"
TRANSIENT_ERROR = "Exit code 1\nrequest failed: 429 Too Many Requests, retry after 30 seconds"
UNCHANGED_HINT = "File unchanged since last read: /srv/demo/app/config.yaml"
FILE_BODIES = [
    "     1→import os\n     2→import sys\n     3→\n     4→def main():\n     5→    print('demo')\n"
    "     6→\n     7→if __name__ == '__main__':\n     8→    main()",
    "     1→name: demo-service\n     2→port: 8080\n     3→replicas: 2\n     4→image: demo:latest\n"
    "     5→env: staging\n     6→timeout_seconds: 30",
    "     1→# Demo notes\n     2→\n     3→This file exists only in the synthetic fixtures.\n"
    "     4→It has no connection to any real project.",
]

# ---------------------------------------------------------------------------
# Token plan per task type: the five reference sessions (judged MUCH_BETTER) span these ranges.
# ---------------------------------------------------------------------------

REF_TOKEN_RANGE: dict[str, tuple[int, int]] = {
    "ml-eval": (260_000, 640_000),
    "debug-fix": (180_000, 460_000),
    "infra-deploy": (220_000, 560_000),
    "feature-build": (240_000, 600_000),
}
AVAILABLE_TYPES = list(REF_TOKEN_RANGE)
REF_PER_TYPE = 5
REF_TURNS = [34, 40, 46, 52, 58]  # in-scope floor is min(): the p10 of five sessions is the minimum
SHORT_TURNS = 12  # below every reference turn count: out_of_scope

# waste plan: (type, slot) -> list of families injected. Slots 0-4 are the reference sessions.
# RFR = repeated failed retry, RRA = redundant read (tool hint), RRB = redundant read (same
# content twice), NEAR = near-miss controls that must not fire.
WASTE_PLAN: dict[tuple[str, str], list[str]] = {
    ("ml-eval", "ref1"): ["RFR"],
    ("ml-eval", "ref3"): ["RRA"],
    ("ml-eval", "above"): ["RFR", "RRB"],
    ("debug-fix", "ref0"): ["RRB"],
    ("debug-fix", "ref2"): ["RFR"],
    ("debug-fix", "ref4"): ["RRA", "RRB"],
    ("infra-deploy", "ref1"): ["RRA"],
    ("infra-deploy", "ref3"): ["NEAR"],
    ("infra-deploy", "within"): ["RFR"],
    ("feature-build", "ref0"): ["NEAR"],
    ("feature-build", "ref2"): ["RRB"],
    ("feature-build", "above"): ["RFR", "RRA"],
    ("research-recon", "a"): ["RRA"],
    ("research-recon", "b"): ["RRB"],
}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

Block = list[tuple[str, list[str], str]]  # (role, tool_names, snippet) turns that stay together


def _uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _filler_block(rng: random.Random, serial: int) -> Block:
    kind = rng.choice(["text", "glob", "grep", "edit", "bash", "read"])
    if kind == "text":
        return [("ai", [], rng.choice(AI_TEXT))]
    if kind == "glob":
        return [("ai", ["Glob"], ""), ("tool", [], rng.choice(GLOB_RESULTS))]
    if kind == "grep":
        return [("ai", ["Grep"], ""), ("tool", [], rng.choice(GREP_RESULTS))]
    if kind == "edit":
        return [("ai", ["Edit"], ""), ("tool", [], EDIT_RESULT)]
    if kind == "bash":
        return [("ai", ["Bash"], ""), ("tool", [], rng.choice(OK_BASH_RESULTS))]
    # a read whose content is unique per call, so it can never pair into a redundant read
    body = (
        f"     1→# unique filler file number {serial}\n     2→value = {serial}\n"
        + "     3→"
        + ("x" * 90)
    )
    return [("ai", ["Read"], ""), ("tool", [], body)]


def _waste_block(rng: random.Random, family: str) -> Block:
    if family == "RFR":
        err = rng.choice([RFR_ERROR, RFR_ERROR_B])
        repeats = rng.choice([2, 3, 3])
        block: Block = []
        for _ in range(repeats):
            block += [("ai", ["Bash"], ""), ("tool", [], err)]
        return block
    if family == "RRA":
        return [("ai", ["Read"], ""), ("tool", [], UNCHANGED_HINT)]
    if family == "RRB":
        body = rng.choice(FILE_BODIES)
        mid: Block = [("ai", [], "Checking one more detail.")] if rng.random() < 0.5 else []
        return [
            ("ai", ["Read"], ""),
            ("tool", [], body),
            *mid,
            ("ai", ["Read"], ""),
            ("tool", [], body),
        ]
    if family == "NEAR":
        # transient errors repeat but are excluded by design; two reads separated by a user
        # turn are a context reset, not a redundant read. Neither may fire.
        body = rng.choice(FILE_BODIES)
        block = []
        for _ in range(3):
            block += [("ai", ["Bash"], ""), ("tool", [], TRANSIENT_ERROR)]
        block += [
            ("ai", ["Read"], ""),
            ("tool", [], body),
            ("user", [], "Please look at that file once more."),
            ("ai", ["Read"], ""),
            ("tool", [], body),
        ]
        return block
    raise ValueError(family)


def _build_turns(
    rng: random.Random,
    task: str,
    n_turns: int,
    real_tokens: int,
    waste: list[str],
    caveat: bool,
) -> list[dict]:
    """Assemble exactly n_turns turns whose real_tokens (AI turns only) equal real_tokens."""
    first_user = task[:SNIPPET_MAX]
    head: Block = [("user", [], first_user)]
    if caveat:
        head += [
            ("user", [], "<command-name>/clear</command-name>"),
            ("user", [], task[len(CAVEAT_PREFIX) :].strip()[:SNIPPET_MAX] or task),
        ]
    blocks: list[Block] = [_waste_block(rng, fam) for fam in waste]
    used = len(head) + sum(len(b) for b in blocks)
    serial = 0
    body: list[Block] = []
    while used < n_turns:
        blk = _filler_block(rng, serial)
        serial += 1
        if used + len(blk) > n_turns:
            blk = (
                blk[:1]
                if blk[0][0] == "ai" and not blk[0][1]
                else [("ai", [], rng.choice(AI_TEXT))]
            )
        body.append(blk)
        used += len(blk)
    # interleave waste blocks at random positions among the filler blocks (never first/last)
    order = body[:]
    for blk in blocks:
        order.insert(rng.randint(1, max(1, len(order))), blk)
    flat = head + [t for blk in order for t in blk]
    assert len(flat) == n_turns, (len(flat), n_turns)

    ai_pos = [i for i, t in enumerate(flat) if t[0] == "ai"]
    out_total = 0
    outs: dict[int, int] = {}
    for i in ai_pos:
        outs[i] = rng.randint(150, 900)
        out_total += outs[i]
    net_target = real_tokens - out_total
    assert net_target > 0
    weights = [rng.uniform(0.5, 1.5) for _ in ai_pos]
    wsum = sum(weights)
    nets = [int(net_target * w / wsum) for w in weights]
    nets[-1] += net_target - sum(nets)  # exact total

    turns: list[dict] = []
    ai_i = 0
    for idx, (role, tools, snippet) in enumerate(flat):
        if role == "ai":
            net = nets[ai_i]
            ai_i += 1
            cache = int(net * rng.uniform(2.0, 9.0))
            turns.append(
                {
                    "turn_index": idx,
                    "role": "ai",
                    "tool_names": tools,
                    "content_snippet": snippet[:SNIPPET_MAX],
                    "token_count_input": net + cache,
                    "token_count_output": outs[idx],
                    "cache_read": cache,
                    "h2_duplicate": False,
                }
            )
        else:
            turns.append(
                {
                    "turn_index": idx,
                    "role": role,
                    "tool_names": tools,
                    "content_snippet": snippet[:SNIPPET_MAX],
                    "token_count_input": 0,
                    "token_count_output": 0,
                    "cache_read": 0,
                    "h2_duplicate": False,
                }
            )
    return turns


def _record(rng: random.Random, task: str, turns: list[dict]) -> dict:
    total_in = sum(t["token_count_input"] for t in turns)
    total_cache = sum(t["cache_read"] for t in turns)
    total_tokens = total_in + sum(t["token_count_output"] for t in turns)
    sid = _uuid(rng)
    cache_hit = total_cache / total_in if total_in else 0.0
    digest = {
        "session_id": sid,
        "domain": "unknown",
        "resolved": False,
        "total_tokens": total_tokens,
        "turn_count": len(turns),
        "h2_duplicate_count": 0,
        "cache_hit_rate": cache_hit,
        "p25_token_ratio": 1.0,
        "output_tokens_available": True,
        "task_description": task[:SNIPPET_MAX],
        "turns": turns,
    }
    return {
        "session_id": sid,
        "domain_id": "unknown",
        "test_outcome": False,
        "total_tokens": total_tokens,
        "turn_count": len(turns),
        "h2_duplicate_count": 0,
        "cache_hit_rate": cache_hit,
        "p25_token_ratio": 1.0,
        "labeler_model": "not_applicable",
        "scaffold": "claude_code",
        "output_tokens_available": True,
        "digest": digest,
        "token_economy_available": False,
        "domain_inferred": "fallback_unknown",
    }


VERDICT_SCORE = {
    "MUCH_BETTER": 1.0,
    "BETTER": 0.75,
    "SIMILAR": 0.5,
    "WORSE": 0.25,
    "MUCH_WORSE": 0.0,
}


def _judge(record: dict, verdict: str) -> dict:
    return {
        "session_id": record["session_id"],
        "judge_score": VERDICT_SCORE[verdict],
        "verdict": verdict,
        "waste_categories": [],
        "confidence": 0.9,
        "reasoning": f"Synthetic fixture verdict {verdict}; invented, not a model judgment.",
        "scaffold": "unknown",
        "domain_id": "unknown",
        "model": "synthetic-fixture",
    }


def generate() -> tuple[list[dict], list[dict]]:
    """Return (pool records, judge entries). Pure function of SEED."""
    rng = random.Random(SEED)
    pool: list[dict] = []
    judges: list[dict] = []
    expected: dict[str, str] = {}

    def add(
        ttype: str,
        slot: str,
        n_turns: int,
        tokens: int,
        verdict: str | None,
        caveat: bool = False,
    ) -> None:
        task = rng.choice(DESCRIPTIONS[ttype])
        if caveat:
            task = CAVEAT_PREFIX + " " + task
        turns = _build_turns(rng, task, n_turns, tokens, WASTE_PLAN.get((ttype, slot), []), caveat)
        rec = _record(rng, task, turns)
        pool.append(rec)
        expected[rec["session_id"]] = ttype
        if verdict:
            judges.append(_judge(rec, verdict))

    for ttype in AVAILABLE_TYPES:
        lo, hi = REF_TOKEN_RANGE[ttype]
        step = (hi - lo) // (REF_PER_TYPE - 1)
        for k in range(REF_PER_TYPE):
            jitter = rng.randint(-5_000, 5_000)
            add(ttype, f"ref{k}", REF_TURNS[k], lo + k * step + jitter, "MUCH_BETTER")
        mid = lo + 2 * step
        add(ttype, "within", 44, mid + rng.randint(-8_000, 8_000), None)
        add(ttype, "above", 50, hi + 3 * step + rng.randint(0, 20_000), "WORSE")
        add(ttype, "short", SHORT_TURNS, rng.randint(40_000, 90_000), "BETTER")
        if ttype == "feature-build":
            add(ttype, "caveat", 40, mid, None, caveat=True)
        else:
            add(ttype, "extra", 42, hi + rng.randint(0, 30_000), None)

    # research-recon: only two MUCH_BETTER sessions, below --min-n, so the type has no baseline
    for slot, verdict, turns, tokens in [
        ("a", "MUCH_BETTER", 36, 210_000),
        ("b", "MUCH_BETTER", 48, 480_000),
        ("c", "SIMILAR", 30, 330_000),
        ("d", "MUCH_WORSE", 56, 900_000),
        ("e", None, 28, 150_000),
        ("f", None, 38, 400_000),
    ]:
        add("research-recon", slot, turns, tokens + rng.randint(-5_000, 5_000), verdict)

    for rec in pool:
        got = classify_session(rec)
        want = expected[rec["session_id"]]
        assert got == want, f"synthetic session classifies as {got!r}, intended {want!r}"
    return pool, judges


def _dump(path: Path, rows: list[dict]) -> None:
    text = "".join(json.dumps(r, ensure_ascii=True) + "\n" for r in rows)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main() -> None:
    """Write the two fixture files."""
    parser = argparse.ArgumentParser(
        description="Generate the synthetic scoring fixtures (seed 42)."
    )
    parser.add_argument("--pool-out", type=Path, default=POOL_OUT)
    parser.add_argument("--judge-out", type=Path, default=JUDGE_OUT)
    args = parser.parse_args()
    pool, judges = generate()
    args.pool_out.parent.mkdir(parents=True, exist_ok=True)
    _dump(args.pool_out, pool)
    _dump(args.judge_out, judges)
    print(f"wrote {len(pool)} synthetic sessions ({len(judges)} judged) to {args.pool_out}")


if __name__ == "__main__":
    main()
