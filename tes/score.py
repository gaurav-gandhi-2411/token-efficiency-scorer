from __future__ import annotations

"""tes/score.py — Three-axis efficiency scorer, SDK entry point.

Self-contained implementation (no scripts/ import) so the installed wheel
works without repo access.

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

from dataclasses import dataclass, field
from pathlib import Path

from tes.baselines import BUNDLED_BASELINES_PATH, compute_real_tokens, load_baselines
from tes.classify import classify_session

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
# Internal result dataclass (behaviour-preservation compatible with scripts/efficiency_score.py)
# ---------------------------------------------------------------------------


@dataclass
class EfficiencyResult:
    """Per-session efficiency assessment against type-specific baselines.

    Field layout is identical to scripts/efficiency_score.EfficiencyResult so that
    the behaviour-preservation golden tests can compare outputs without modification.
    """

    session_id: str
    task_type: str
    real_tokens: int
    scope_status: str           # "in_scope" | "out_of_scope" | "no_baseline"
    baseline_available: bool
    p25: int | None
    p75: int | None
    median: int | None
    band_verdict: str           # "within_band" | "above_p75" | "below_p25" | "unavailable"
    interpretation: str
    # Judge axis (populated when caller provides judge_entry)
    judge_verdict: str | None
    judge_score: float | None
    judge_reasoning: str | None
    # Deterministic waste axis (populated when caller provides waste_entry)
    waste_event_count: int = 0
    waste_events: list[dict] = field(default_factory=list)


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
# Internal helpers
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


def _score_session_impl(
    record: dict,
    baselines: dict,
    judge_entry: dict | None = None,
    waste_entry: dict | None = None,
) -> EfficiencyResult:
    """Core scoring implementation — identical logic to scripts/efficiency_score.score_session.

    Kept separate from score_session() so behaviour-preservation tests can call it
    directly if needed and to keep the public API thin.
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
# Public API
# ---------------------------------------------------------------------------


def score_session(
    record: dict,
    baselines: dict,
    judge_entry: dict | None = None,
    waste_entry: dict | None = None,
) -> ThreeAxisResult:
    """Score a single adapted session record against the three-axis scorer.

    Calls the validated internal implementation (behaviour-preservation guarantee:
    packaging calls the same logic, adds caveats).

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
    "EfficiencyResult",
    "score_session",
    "load_baselines",
    "TOKEN_DOMAIN_OF_VALIDITY",
    "TRAJECTORY_DOMAIN_OF_VALIDITY",
    "WASTE_DOMAIN_OF_VALIDITY",
]
