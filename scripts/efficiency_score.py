from __future__ import annotations

"""Session-level efficiency scorer for the token-efficiency-scorer project.

Classifies a pool_adapted record, computes its real_tokens, and compares
against the per-type baseline band from cc_baselines.json.  Also accepts
an optional judge_entry to surface the trajectory-quality axis alongside
the token-economy axis.

Public API:
    score_session(record, baselines, judge_entry=None) -> EfficiencyResult
    load_baselines(path) -> dict

CLI (spot-check):
    python scripts/efficiency_score.py --session-id <uuid>
"""

import json
import sys
from dataclasses import dataclass
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
# Paths
# ---------------------------------------------------------------------------

POOL_ADAPTED_PATH = REPO_ROOT / "data" / "corpus_pool" / "pool_adapted.jsonl"
DEFAULT_BASELINES_PATH = REPO_ROOT / "data" / "cc_baselines.json"

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


# ---------------------------------------------------------------------------
# Convenience loader
# ---------------------------------------------------------------------------


def load_baselines(path: Path | str = DEFAULT_BASELINES_PATH) -> dict:
    """Load cc_baselines.json and return as a dict.

    Parameters
    ----------
    path:
        Path to cc_baselines.json. Defaults to data/cc_baselines.json.
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
) -> EfficiencyResult:
    """Score a single pool_adapted record against the baseline band.

    Parameters
    ----------
    record:
        A dict parsed from one line of pool_adapted.jsonl. Must contain
        'session_id', 'digest.turns', and 'digest.task_description'.
    baselines:
        Loaded cc_baselines.json dict (use load_baselines() to obtain).
    judge_entry:
        Optional dict from pool_judge_scores.jsonl for this session.
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
    )


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def _load_pool_index() -> dict[str, dict]:
    """Load pool_adapted.jsonl and return a dict keyed by session_id."""
    index: dict[str, dict] = {}
    with POOL_ADAPTED_PATH.open("r", encoding="utf-8") as fh:
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
        print(
            f"p25 / median / p75:  {result.p25:,} / {result.median:,} / {result.p75:,}"
        )
    print(f"interpretation:      {result.interpretation}")
    print()
    print("--- TRAJECTORY QUALITY ---")
    jv = result.judge_verdict if result.judge_verdict is not None else "not scored"
    print(f"judge_verdict:       {jv}")
    if result.judge_score is not None:
        print(f"judge_score:         {result.judge_score}")
    if result.judge_reasoning is not None:
        print(f"judge_reasoning:     {result.judge_reasoning}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for spot-checking individual sessions."""
    args = sys.argv[1:]

    if "--session-id" not in args:
        print("Usage:")
        print("  python scripts/efficiency_score.py --session-id <uuid>")
        sys.exit(0)

    idx = args.index("--session-id")
    if idx + 1 >= len(args):
        print("Error: --session-id requires a session ID argument", file=sys.stderr)
        sys.exit(1)

    target_id = args[idx + 1]

    pool_index = _load_pool_index()
    if target_id not in pool_index:
        print(f"Error: session_id {target_id!r} not found in pool", file=sys.stderr)
        sys.exit(1)

    baselines = load_baselines()

    # Load judge scores if available
    judge_scores_path = REPO_ROOT / "data" / "pool_judge_scores.jsonl"
    judge_index: dict[str, dict] = {}
    if judge_scores_path.exists():
        with judge_scores_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entry = json.loads(line)
                    judge_index[entry["session_id"]] = entry

    record = pool_index[target_id]
    judge_entry = judge_index.get(target_id)
    result = score_session(record, baselines, judge_entry=judge_entry)
    _print_result(result)


if __name__ == "__main__":
    main()
