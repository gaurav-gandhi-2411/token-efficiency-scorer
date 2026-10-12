# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""The scoring fixtures are synthetic: reproducible from the generator, covering every branch the
scorer has, and free of anything that could have come from a real session.

Three kinds of check:

* private markers: no Windows or home-directory path, no project or account name, no instruction
  file name, no email address, in any fixture file;
* provenance: the committed pool and judge scores are exactly what scripts/gen_synthetic_fixtures.py
  produces at seed 42, so every piece of text in them comes from the generator's phrase tables and
  cannot be pasted session text;
* coverage: the fixtures exercise all five task types, in-scope and out-of-scope sessions, all four
  band verdicts, judged and unjudged sessions, and each waste family the detectors have.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import gen_synthetic_fixtures as gen  # noqa: E402
from efficiency_score import load_baselines, score_session  # noqa: E402
from tes.waste import build_waste_entry  # noqa: E402

FIXTURE_FILES = (
    "synthetic_pool.jsonl",
    "synthetic_judge_scores.jsonl",
    "baselines_synthetic.json",
    "golden_scores.json",
)

# Built from pieces so this file does not trip a repo-wide grep for the very markers it forbids.
PRIVATE_MARKERS = {
    "windows path": re.compile(r"\b[A-Za-z]:\\+|\b[A-Za-z]:/(?:Users|ml-projects)", re.I),
    "unix home path": re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+|/c/Users"),
    "user or account name": re.compile(r"gaura|gandhi|gg5678", re.I),
    "project name": re.compile(
        "|".join(
            [
                "token" + "-efficiency",
                "aether" + "art",
                "review" + "-iq",
                "shelf" + "sense",
                "ml" + "-projects",
                "tes" + "-cc-validation",
                "style" + "maitri",
                "mind" + "meld",
            ]
        ),
        re.I,
    ),
    "instruction file": re.compile(r"CLAUDE" + r"\.md|\.claude\b|MEMORY" + r"\.md"),
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
    "secret-looking token": re.compile(r"\b(?:sk-|ghp_|AKIA|xox[bp]-)[A-Za-z0-9_-]{8,}"),
}


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in _read(name).splitlines() if line.strip()]


@pytest.mark.parametrize("name", FIXTURE_FILES)
def test_fixture_has_no_private_markers(name: str) -> None:
    text = _read(name)
    for label, pattern in PRIVATE_MARKERS.items():
        hit = pattern.search(text)
        assert hit is None, f"{name}: {label} marker {hit.group(0)!r}"


def test_marker_patterns_catch_what_they_claim_to() -> None:
    """A scan that matches nothing proves nothing: each pattern must fire on a known sample."""
    samples = {
        "windows path": "opened C:\\\\Users\\\\someone\\\\notes",
        "unix home path": "cat /home/someone/notes.txt",
        "user or account name": "owner gaura",
        "project name": "review" + "-iq/app",
        "instruction file": "see CLAUDE" + ".md",
        "email address": "write to someone@example.com",
        "secret-looking token": "key sk-abcdefgh12345678",
    }
    assert set(samples) == set(PRIVATE_MARKERS)
    for label, sample in samples.items():
        assert PRIVATE_MARKERS[label].search(sample), label


def test_text_in_the_pool_comes_only_from_the_generator() -> None:
    """Task descriptions and every turn snippet are phrases the generator itself defines."""
    allowed = set()
    for phrases in gen.DESCRIPTIONS.values():
        allowed.update(phrases)
        allowed.update(gen.CAVEAT_PREFIX + " " + p for p in phrases)
    allowed.update(gen.AI_TEXT + gen.GLOB_RESULTS + gen.GREP_RESULTS + gen.OK_BASH_RESULTS)
    allowed.update(gen.FILE_BODIES)
    allowed.update(
        [
            gen.EDIT_RESULT,
            gen.RFR_ERROR,
            gen.RFR_ERROR_B,
            gen.TRANSIENT_ERROR,
            gen.UNCHANGED_HINT,
            "Checking one more detail.",
            "Please look at that file once more.",
            "<command-name>/clear</command-name>",
            "",
        ]
    )
    filler_read = re.compile(
        r"     1→# unique filler file number \d+\n     2→value = \d+\n     3→x{90}"
    )
    for rec in _jsonl("synthetic_pool.jsonl"):
        assert rec["digest"]["task_description"] in allowed
        for turn in rec["digest"]["turns"]:
            snippet = turn["content_snippet"]
            assert snippet in allowed or filler_read.fullmatch(snippet), snippet[:60]
            assert len(snippet) <= gen.SNIPPET_MAX


def test_committed_fixtures_are_what_the_generator_produces() -> None:
    pool, judges = gen.generate()
    assert pool == _jsonl("synthetic_pool.jsonl"), "re-run scripts/gen_synthetic_fixtures.py"
    assert judges == _jsonl("synthetic_judge_scores.jsonl")
    assert gen.SEED == 42


def test_baselines_are_labelled_synthetic() -> None:
    baselines = json.loads(_read("baselines_synthetic.json"))
    assert baselines["label"] == "synthetic"


@pytest.fixture(scope="module")
def scored() -> list:
    baselines = load_baselines(FIXTURES / "baselines_synthetic.json")
    judges = {j["session_id"]: j for j in _jsonl("synthetic_judge_scores.jsonl")}
    out = []
    for rec in _jsonl("synthetic_pool.jsonl"):
        waste = build_waste_entry(rec["session_id"], rec["digest"]["turns"])
        out.append((score_session(rec, baselines, judges.get(rec["session_id"]), waste), waste))
    return out


def test_pool_size_and_judged_split(scored: list) -> None:
    assert len(scored) >= 20
    judged = sum(1 for res, _ in scored if res.judge_verdict is not None)
    assert 0 < judged < len(scored)


def test_every_task_type_and_band_verdict_is_present(scored: list) -> None:
    assert {res.task_type for res, _ in scored} == set(gen.DESCRIPTIONS)
    assert {res.band_verdict for res, _ in scored} == {
        "within_band",
        "above_p75",
        "below_p25",
        "unavailable",
    }
    assert {res.scope_status for res, _ in scored} == {"in_scope", "out_of_scope", "no_baseline"}


def test_every_waste_family_fires_and_the_controls_do_not(scored: list) -> None:
    fired = Counter(
        (e["detector"], e["evidence"].get("path")) for _, w in scored for e in w["waste_events"]
    )
    assert fired[("REPEATED-FAILED-RETRY", None)] >= 1
    assert fired[("REDUNDANT-READ", "A")] >= 1
    assert fired[("REDUNDANT-READ", "B")] >= 1
    # the near-miss sessions (transient errors repeated; reads separated by a user turn) are
    # planted in the plan and must stay silent
    near = {k for k, fams in gen.WASTE_PLAN.items() if fams == ["NEAR"]}
    assert near, "plan has no near-miss controls"
    sessions_with_events = sum(1 for _, w in scored if w["waste_events"])
    assert sessions_with_events == sum(1 for fams in gen.WASTE_PLAN.values() if fams != ["NEAR"])
