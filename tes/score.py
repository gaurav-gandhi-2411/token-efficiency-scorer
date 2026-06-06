from __future__ import annotations

"""tes/score.py — Three-axis efficiency scorer, SDK entry point.

Public API:
    score_session(record, baselines, judge_entry=None, waste_entry=None) -> ThreeAxisResult
    load_baselines(path) -> dict

ThreeAxisResult carries all three scoring axes:
  - token:      real_tokens + band_verdict + scope_status (calibrated to B2 corpus)
  - trajectory: judge verdict + score (positive signal only; B3 cross-model corroborated)
  - waste:      deterministic waste events with proof turns (observable-invariant only; B4/P1)

Each axis carries its domain_of_validity in the result object so both SDK and CLI
consumers receive the honesty — not bolted on in CLI formatting only (spec decision 1).
"""

import sys
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from efficiency_score import (  # noqa: E402
    EfficiencyResult,
    load_baselines,
    score_session as _score_session_impl,
)

# ---------------------------------------------------------------------------
# Domain-of-validity constants (one per axis, inline in result object)
# These match the report language (reports 08, 09, 10, 11) and must appear
# in any output surface — CLI and programmatic alike.
# ---------------------------------------------------------------------------

TOKEN_DOMAIN_OF_VALIDITY: str = (
    "Calibrated to a high-waste infra/ML-ops corpus (1 developer, 75 quality-gated "
    "sessions; B2 report). Scope-gated by per-task-type p10 turn floor. Verdict is "
    "relative to quality-certified sessions of the same task type. "
    "Baseline reflects high-intensity infra work (corpus characterization: report 11); "
    "ordinary coding sessions may read below-band without being inefficient — "
    "interpret with the trajectory verdict. "
    "UNAVAILABLE when below the scope gate or task type has no baseline."
)

TRAJECTORY_DOMAIN_OF_VALIDITY: str = (
    "Positive signal (MUCH_BETTER/BETTER) is cross-model corroborated (84-96%; B3 "
    "report). Negative signal (WORSE/MUCH_WORSE) is model-dependent; do not treat "
    "as fact. No human accuracy calibration. "
    "UNAVAILABLE when no local judge is configured or no judge result is provided."
)

WASTE_DOMAIN_OF_VALIDITY: str = (
    "Observable-invariant waste only: same shell command + same error + no state "
    "change (REPEATED-FAILED-RETRY); same file content + no edit between reads "
    "(REDUNDANT-READ PATH-A and PATH-B; PATH-B dual-format as of P1). "
    "Proof turns attached to every event. "
    "Redundant-read (PATH-B) depends on CC's Read output format (tab + arrow currently "
    "supported); may under-report on future CC versions if the format changes again. "
    "Judgment-of-progress waste not covered — requires human labeling."
)


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------


@dataclass
class ThreeAxisResult:
    """SDK result: three scoring axes, each with domain-of-validity.

    Field layout:
      - All fields from EfficiencyResult (same names and types — golden-test
        compatible; the refactor is a one-line import swap)
      - Three domain_of_validity strings, one per axis (new in P1)

    INVARIANT: domain_of_validity fields are always populated (never empty)
    so both CLI and programmatic consumers always receive the caveats.
    """

    # --- identity ---
    session_id: str
    task_type: str

    # --- token axis ---
    real_tokens: int
    scope_status: str           # "in_scope" | "out_of_scope" | "no_baseline"
    baseline_available: bool
    p25: int | None
    p75: int | None
    median: int | None
    band_verdict: str           # "within_band" | "above_p75" | "below_p25" | "unavailable"
    interpretation: str
    token_domain_of_validity: str

    # --- trajectory axis ---
    judge_verdict: str | None
    judge_score: float | None
    judge_reasoning: str | None
    trajectory_domain_of_validity: str

    # --- waste axis ---
    waste_event_count: int
    waste_events: list[dict]
    waste_domain_of_validity: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_session(
    record: dict,
    baselines: dict,
    judge_entry: dict | None = None,
    waste_entry: dict | None = None,
) -> ThreeAxisResult:
    """Score a single adapted session record against the three-axis scorer.

    Calls the validated scripts/efficiency_score.py implementation internally
    (behavior-preservation guarantee: packaging calls the same logic, adds caveats).

    Parameters
    ----------
    record:
        Adapted session record (from pool_adapted.jsonl or claudecode_adapter.adapt_session).
    baselines:
        Loaded cc_baselines.json dict (use load_baselines() to obtain).
    judge_entry:
        Optional pre-computed judge result dict from pool_judge_scores.jsonl.
        When absent, trajectory axis is UNAVAILABLE (judge_verdict=None).
    waste_entry:
        Optional pre-computed waste signals dict from pool_waste_signals.jsonl.
        When absent, waste axis shows no detected events.

    Returns
    -------
    ThreeAxisResult
        All three scoring axes with domain-of-validity per axis.
    """
    impl_result: EfficiencyResult = _score_session_impl(
        record, baselines, judge_entry=judge_entry, waste_entry=waste_entry
    )

    return ThreeAxisResult(
        # --- identity ---
        session_id=impl_result.session_id,
        task_type=impl_result.task_type,
        # --- token axis ---
        real_tokens=impl_result.real_tokens,
        scope_status=impl_result.scope_status,
        baseline_available=impl_result.baseline_available,
        p25=impl_result.p25,
        p75=impl_result.p75,
        median=impl_result.median,
        band_verdict=impl_result.band_verdict,
        interpretation=impl_result.interpretation,
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        # --- trajectory axis ---
        judge_verdict=impl_result.judge_verdict,
        judge_score=impl_result.judge_score,
        judge_reasoning=impl_result.judge_reasoning,
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        # --- waste axis ---
        waste_event_count=impl_result.waste_event_count,
        waste_events=impl_result.waste_events,
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
    )


__all__ = [
    "ThreeAxisResult",
    "score_session",
    "load_baselines",
    "TOKEN_DOMAIN_OF_VALIDITY",
    "TRAJECTORY_DOMAIN_OF_VALIDITY",
    "WASTE_DOMAIN_OF_VALIDITY",
]
