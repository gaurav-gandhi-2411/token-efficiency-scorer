# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Session-level efficiency scorer for the tracegauge project.

Classifies an adapted-session record, computes its real_tokens, and compares
against the per-type baseline band from a baselines JSON.  Also accepts
an optional judge_entry to surface the trajectory-quality axis alongside
the token-economy axis.

Public API:
    score_session(record, baselines, judge_entry=None) -> EfficiencyResult
    load_baselines(path) -> dict

CLI (spot-check; every input is an explicit argument, the maintainer's pool is not in
the repository, the synthetic fixtures under tests/fixtures/ are):
    python scripts/efficiency_score.py --pool POOL.jsonl --baselines BASELINES.json \
        --session-id <uuid> [--judge-scores SCORES.jsonl] [--waste-signals SIGNALS.jsonl]
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Make sibling scripts importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from build_baselines import compute_real_tokens  # noqa: E402
from task_classifier import classify_session  # noqa: E402

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class EfficiencyResult:
    """Per-session efficiency assessment against type-specific baselines."""

    session_id: str
    task_type: str  # classified type
    real_tokens: int  # corrected token count
    scope_status: str  # "in_scope" | "out_of_scope" | "no_baseline"
    baseline_available: bool
    p25: int | None  # None when baseline unavailable
    p75: int | None
    median: int | None
    band_verdict: str  # "within_band" | "above_p75" | "below_p25" | "unavailable"
    interpretation: str  # human-readable explanation
    # Judge axis (populated when caller provides judge_entry)
    judge_verdict: str | None
    judge_score: float | None
    judge_reasoning: str | None
    # Deterministic waste axis (populated when caller provides waste_entry)
    waste_event_count: int = 0
    waste_events: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Convenience loader
# ---------------------------------------------------------------------------


def load_baselines(path: Path | str) -> dict:
    """Load a baselines JSON (the shape build_baselines.py writes) and return it as a dict.

    Parameters
    ----------
    path:
        Path to the baselines JSON, e.g. tests/fixtures/baselines_synthetic.json.
    """
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Judge-axis helper
# ---------------------------------------------------------------------------


def _lean_judgment(judge_verdict: str | None) -> str:
    """Return a one-sentence trajectory note to append to below_p25 interpretations."""
    if judge_verdict in ("MUCH_BETTER", "BETTER"):
        return f"Trajectory verdict {judge_verdict}: efficient execution at lean cost."
    elif judge_verdict in ("WORSE", "MUCH_WORSE"):
        return (
            f"Trajectory verdict {judge_verdict}: not efficient — "
            f"lean cost does not indicate quality."
        )
    else:
        return "Pair with the trajectory verdict to assess efficiency."


# ---------------------------------------------------------------------------
# Core scoring function
# ---------------------------------------------------------------------------


def score_session(
    record: dict,
    baselines: dict,
    judge_entry: dict | None = None,
    waste_entry: dict | None = None,
) -> EfficiencyResult:
    """Score a single adapted-session record against the baseline band.

    Parameters
    ----------
    record:
        A dict parsed from one line of an adapted-session pool. Must contain
        'session_id', 'digest.turns', and 'digest.task_description'.
    baselines:
        Loaded baselines dict (use load_baselines() to obtain).
    judge_entry:
        Optional judge-score dict (one line of a judge-scores JSONL) for this session.
        When provided, populates the judge_verdict / judge_score /
        judge_reasoning fields on the result.

    Returns
    -------
    EfficiencyResult
        Populated dataclass with band verdict, scope status, and
        human-readable interpretation across both scoring axes.
    """
    session_id: str = record.get("session_id", "")
    task_type: str = classify_session(record)
    real_tokens: int = compute_real_tokens(record)

    # Extract judge fields (may all be None when judge_entry is absent)
    je_verdict: str | None = judge_entry.get("verdict") if judge_entry else None
    je_score: float | None = judge_entry.get("judge_score") if judge_entry else None
    je_reasoning: str | None = judge_entry.get("reasoning") if judge_entry else None

    # Extract deterministic waste fields
    waste_events: list[dict] = waste_entry.get("waste_events", []) if waste_entry else []
    waste_event_count: int = len(waste_events)

    type_info: dict = baselines.get("types", {}).get(task_type, {})
    baseline_available: bool = type_info.get("available", False)

    # Scope gate: check turn count against p10 floor
    scope_gates = baselines.get("scope_gates", {})
    gate_info = scope_gates.get(task_type, {})
    p10_turns: int | None = gate_info.get("p10_turns")
    turn_count: int = record.get("turn_count", 0)

    if p10_turns is not None and turn_count < p10_turns:
        scope_status = "out_of_scope"
    else:
        scope_status = "in_scope"

    # Early return when baseline is unavailable OR session is out of scope
    if scope_status == "out_of_scope" or not baseline_available:
        if scope_status == "out_of_scope":
            interpretation = (
                f"Session scope too small for a token-economy reference "
                f"({turn_count} turns; {task_type} floor is {p10_turns} turns). "
                f"Trajectory verdict only."
            )
        else:
            interpretation = (
                f"Task type '{task_type}' has no baseline "
                f"(sparse type or not seen in reference corpus)"
            )
        return EfficiencyResult(
            session_id=session_id,
            task_type=task_type,
            real_tokens=real_tokens,
            scope_status=scope_status if baseline_available else "no_baseline",
            baseline_available=baseline_available,
            p25=None,
            p75=None,
            median=None,
            band_verdict="unavailable",
            interpretation=interpretation,
            judge_verdict=je_verdict,
            judge_score=je_score,
            judge_reasoning=je_reasoning,
            waste_event_count=waste_event_count,
            waste_events=waste_events,
        )

    p25: int = type_info["p25"]
    p75: int = type_info["p75"]
    median: int = type_info["median"]

    if real_tokens > p75:
        band_verdict = "above_p75"
        interpretation = (
            f"Session used {real_tokens:,} tokens, above the p75 reference ({p75:,}) "
            f"for {task_type} sessions — more tokens than typical good runs"
        )
    elif real_tokens < p25:
        band_verdict = "below_p25"
        interpretation = (
            f"Token cost is lean for {task_type} ({real_tokens:,} tokens, "
            f"below the {p25:,} reference floor for exemplary {task_type} sessions). "
            + _lean_judgment(je_verdict)
        )
    else:
        band_verdict = "within_band"
        interpretation = (
            f"Session used {real_tokens:,} tokens, within the "
            f"[{p25:,}–{p75:,}] reference band for {task_type} sessions"
        )

    return EfficiencyResult(
        session_id=session_id,
        task_type=task_type,
        real_tokens=real_tokens,
        scope_status=scope_status,
        baseline_available=True,
        p25=p25,
        p75=p75,
        median=median,
        band_verdict=band_verdict,
        interpretation=interpretation,
        judge_verdict=je_verdict,
        judge_score=je_score,
        judge_reasoning=je_reasoning,
        waste_event_count=waste_event_count,
        waste_events=waste_events,
    )


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def _load_jsonl_index(path: Path) -> dict[str, dict]:
    """Load a JSONL file and return a dict keyed by session_id."""
    index: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                record = json.loads(line)
                index[record["session_id"]] = record
    return index


def _print_result(result: EfficiencyResult) -> None:
    """Print EfficiencyResult to stdout in a two-axis readable format."""
    print(f"session_id:          {result.session_id}")
    print(f"task_type:           {result.task_type}")
    print(f"scope_status:        {result.scope_status}")
    print(f"real_tokens:         {result.real_tokens:,}")
    print()
    print("--- TOKEN ECONOMY ---")
    print(f"band_verdict:        {result.band_verdict}")
    if result.p25 is not None and result.median is not None and result.p75 is not None:
        print(f"p25 / median / p75:  {result.p25:,} / {result.median:,} / {result.p75:,}")
    print(f"interpretation:      {result.interpretation}")
    print()
    print("--- TRAJECTORY QUALITY ---")
    jv = result.judge_verdict if result.judge_verdict is not None else "not scored"
    print(f"judge_verdict:       {jv}")
    if result.judge_score is not None:
        print(f"judge_score:         {result.judge_score}")
    if result.judge_reasoning is not None:
        print(f"judge_reasoning:     {result.judge_reasoning}")
    print()
    print("--- DETERMINISTIC WASTE ---")
    if result.waste_event_count == 0:
        print("waste_events:        none detected")
    else:
        print(f"waste_events:        {result.waste_event_count} event(s)")
        for ev in result.waste_events:
            det = ev.get("detector", "?")
            turns = ev.get("turns", [])
            evi = ev.get("evidence", {})
            if det == "REPEATED-FAILED-RETRY":
                rc = ev.get("repeat_count", "?")
                snip = evi.get("error_snippet", "")[:80]
                print(f"  {det}: repeat_count={rc} turns={turns}")
                print(f"    error: {snip!r}")
            elif det == "REDUNDANT-READ":
                path = evi.get("path", "?")
                gap = evi.get("gap", "?")
                snip = evi.get("content_snippet", "")[:60]
                print(f"  {det} (PATH {path}): gap={gap} turns={turns}")
                print(f"    content: {snip!r}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for spot-checking individual sessions."""
    parser = argparse.ArgumentParser(description="Score one adapted session against a baseline.")
    parser.add_argument("--pool", type=Path, required=True, help="adapted-session pool (JSONL)")
    parser.add_argument("--baselines", type=Path, required=True, help="baselines JSON")
    parser.add_argument("--session-id", required=True, help="session to score")
    parser.add_argument("--judge-scores", type=Path, default=None, help="judge scores (JSONL)")
    parser.add_argument("--waste-signals", type=Path, default=None, help="waste signals (JSONL)")
    args = parser.parse_args(argv)

    pool_index = _load_jsonl_index(args.pool)
    if args.session_id not in pool_index:
        print(f"Error: session_id {args.session_id!r} not found in pool", file=sys.stderr)
        sys.exit(1)

    baselines = load_baselines(args.baselines)
    judge_index = _load_jsonl_index(args.judge_scores) if args.judge_scores else {}
    waste_index = _load_jsonl_index(args.waste_signals) if args.waste_signals else {}

    record = pool_index[args.session_id]
    result = score_session(
        record,
        baselines,
        judge_entry=judge_index.get(args.session_id),
        waste_entry=waste_index.get(args.session_id),
    )
    _print_result(result)


if __name__ == "__main__":
    main()
