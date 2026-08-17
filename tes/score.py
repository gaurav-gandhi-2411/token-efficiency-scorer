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
from typing import TYPE_CHECKING

from tes.baselines import BUNDLED_BASELINES_PATH, compute_real_tokens, load_baselines
from tes.classify import classify_session

if TYPE_CHECKING:
    from tes.attribution import AttributionResult
    from tes.cost import SessionCost
    from tes.self_baseline import SelfBaselineState

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


def build_api_trajectory_dov(api_model: str) -> str:
    """DOV for API-judge verdicts: same B3 caveats + model-not-validated extra.

    The B3 cross-model corroboration (84-96%) was measured on Qwen3 30B (local)
    and Gemma 3 27B — NOT on any API model. An API judge uses the validated
    v3 rubric on an unvalidated model. The extra caveat is non-negotiable.
    """
    return (
        "Positive signal (MUCH_BETTER/BETTER) is cross-model corroborated (84-96%; B3 "
        "report, validated on Qwen3 30B + Gemma 3 27B). Negative signal (WORSE/MUCH_WORSE) "
        "is model-dependent; do not treat as fact. No human accuracy calibration. "
        f"API judge ({api_model}): rubric is the validated v3 prompt; {api_model} was NOT "
        "part of the B3 cross-model corroboration — treat verdict as indicative, not "
        "equivalent to the validated local judge. "
        "UNAVAILABLE when no judge result is provided."
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
    baseline_source: str        # "self" | "building" | "corpus" | "b2_corpus"

    # --- trajectory axis ---
    judge_verdict: str | None
    judge_score: float | None
    judge_reasoning: str | None
    trajectory_domain_of_validity: str

    # --- waste axis ---
    waste_event_count: int
    waste_events: list[dict]
    waste_domain_of_validity: str

    # --- cost annotation (P5: annotation on token axis, not a score, not a composite) ---
    session_cost_usd: float | None = None
    cost_approximate: bool = False
    cost_domain_of_validity: str = ""
    # Added 0.10.2 (S1 fix): distinct warnings for server-side tool usage
    # (e.g. web search) detected but not reflected in session_cost_usd. Empty
    # list when none detected. See tes.cost.SessionCost.server_tool_warnings.
    cost_server_tool_warnings: list[str] = field(default_factory=list)

    # --- attribution fractions (RR1: persisted at score time so tes.intelligence
    # can cluster ANY scored session regardless of whether its source JSONL is
    # still reachable on disk) ---
    context_resend_pct: float | None = None
    context_growth_pct: float | None = None
    output_pct: float | None = None
    waste_pct: float | None = None


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
    self_baseline: SelfBaselineState | None = None,
    session_cost: SessionCost | None = None,
    baseline_cost_band: tuple[float, float, float] | None = None,
    attribution: AttributionResult | None = None,
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
    self_baseline:
        Optional per-user self-baseline state from tes.self_baseline.load_or_compute().
        When provided the token axis scores against the user's own lean reference
        (source='self') or shows the cold-start building state.
        Falls back to the B2 corpus baseline when not provided or when the type is
        still building and corpus_fallback was not enabled.
    session_cost:
        Optional pre-computed session cost from tes.cost.compute_session_cost.
        When provided, session_cost_usd and cost_approximate are populated on the result.
        Cost is an annotation only — not a score, not part of a composite.
    baseline_cost_band:
        Optional (p25_usd, median_usd, p75_usd) from self_baseline.compute_baseline_cost_band.
        Stored for downstream framing; not used in scoring.
    attribution:
        Optional pre-computed six-bucket attribution from tes.attribution.compute_attribution
        (same digest/waste_entry/prices the caller already has for session_cost). When
        provided, the four clustering-feature fractions (context_resend_pct,
        context_growth_pct, output_pct, waste_pct) are populated on the result and
        persisted to the store — see tes.attribution.attribution_fractions. RR1: this is
        what lets tes.intelligence cluster a session without re-reading its source JSONL.

    Returns
    -------
    ThreeAxisResult
        All three scoring axes with domain-of-validity per axis.
        baseline_source indicates which reference was used for the token axis.
    """
    impl_result: EfficiencyResult = _score_session_impl(
        record, baselines, judge_entry=judge_entry, waste_entry=waste_entry
    )

    task_type = impl_result.task_type
    turn_count: int = record.get("turn_count", 0)
    real_tokens = impl_result.real_tokens

    # Resolve which TypeBaseline to use for the token axis.
    type_bl = (
        self_baseline.by_type.get(task_type)
        if self_baseline is not None
        else None
    )

    if type_bl is not None and type_bl.source == "self":
        # Self-baseline active: score against user's own lean reference.
        scope_floor = type_bl.scope_floor
        if turn_count < scope_floor:
            tok_scope = "out_of_scope"
            tok_baseline_avail = False
            tok_p25: int | None = None
            tok_median: int | None = None
            tok_p75: int | None = None
            tok_band = "unavailable"
            tok_interp = (
                f"Session scope too small for a token-economy reference "
                f"({turn_count} turns; {task_type} self-derived floor is {scope_floor} turns). "
                "Trajectory verdict only."
            )
        else:
            tok_scope = "in_scope"
            tok_baseline_avail = True
            tok_p25, tok_median, tok_p75 = type_bl.p25, type_bl.median, type_bl.p75
            # Non-None for source='self' — TypeBaseline invariant.
            p25_v = tok_p25 if tok_p25 is not None else 0
            p75_v = tok_p75 if tok_p75 is not None else 0
            if real_tokens > p75_v:
                tok_band = "above_p75"
                tok_interp = (
                    f"Session used {real_tokens:,} tokens — above your lean p75 "
                    f"({p75_v:,}) for {task_type}. "
                    "Heavier than your typical efficient run. "
                    "(Relative to YOUR OWN lean waste-free sessions — not an absolute verdict.)"
                )
            elif real_tokens < p25_v:
                tok_band = "below_p25"
                tok_interp = (
                    f"Token cost is lean for {task_type} ({real_tokens:,} tokens, "
                    f"below your lean p25 reference ({p25_v:,})). "
                    + _lean_judgment(impl_result.judge_verdict)
                )
            else:
                tok_band = "within_band"
                tok_interp = (
                    f"Session used {real_tokens:,} tokens — within your lean reference band "
                    f"[{p25_v:,}–{p75_v:,}] for {task_type}. "
                    "(Relative to YOUR OWN lean waste-free sessions — not an absolute verdict.)"
                )
        tok_dov = type_bl.domain_of_validity
        tok_source = "self"

    elif type_bl is not None and type_bl.source == "building":
        # Self-baseline not yet ready: apply self-derived scope floor, band unavailable.
        scope_floor = type_bl.scope_floor
        tok_scope = "out_of_scope" if turn_count < scope_floor else "in_scope"
        tok_baseline_avail = False
        tok_p25, tok_median, tok_p75 = None, None, None
        tok_band = "unavailable"
        tok_interp = (
            f"Building your {task_type} self-baseline: need {type_bl.sessions_needed} more "
            "waste-free sessions. Trajectory verdict available in the meantime."
        )
        tok_dov = type_bl.domain_of_validity
        tok_source = "building"

    else:
        # Corpus fallback (type_bl.source == 'corpus') or no self-baseline → B2 result.
        tok_scope = impl_result.scope_status
        tok_baseline_avail = impl_result.baseline_available
        tok_p25, tok_median, tok_p75 = impl_result.p25, impl_result.median, impl_result.p75
        tok_band = impl_result.band_verdict
        tok_interp = impl_result.interpretation
        tok_dov = TOKEN_DOMAIN_OF_VALIDITY
        tok_source = type_bl.source if type_bl is not None else "b2_corpus"

    cost_usd = session_cost.total_usd if session_cost else None
    cost_approx = session_cost.approximate if session_cost else False
    cost_dov = session_cost.domain_of_validity if session_cost else ""
    cost_server_tool_warnings = session_cost.server_tool_warnings if session_cost else []

    _resend_pct: float | None
    _growth_pct: float | None
    _out_pct: float | None
    _waste_pct: float | None
    if attribution is not None:
        from tes.attribution import attribution_fractions

        _resend_pct, _growth_pct, _out_pct, _waste_pct = attribution_fractions(attribution)
    else:
        _resend_pct = _growth_pct = _out_pct = _waste_pct = None

    # Use API-specific DOV when the judge_entry came from the API path.
    # The API judge uses the validated rubric but on a model NOT validated in B3.
    _traj_dov: str
    if judge_entry is not None and judge_entry.get("judge_path") == "api":
        _traj_dov = build_api_trajectory_dov(
            judge_entry.get("api_model", "api-model")
        )
    else:
        _traj_dov = TRAJECTORY_DOMAIN_OF_VALIDITY

    return ThreeAxisResult(
        # --- identity ---
        session_id=impl_result.session_id,
        task_type=task_type,
        # --- token axis ---
        real_tokens=real_tokens,
        scope_status=tok_scope,
        baseline_available=tok_baseline_avail,
        p25=tok_p25,
        p75=tok_p75,
        median=tok_median,
        band_verdict=tok_band,
        interpretation=tok_interp,
        token_domain_of_validity=tok_dov,
        baseline_source=tok_source,
        # --- trajectory axis ---
        judge_verdict=impl_result.judge_verdict,
        judge_score=impl_result.judge_score,
        judge_reasoning=impl_result.judge_reasoning,
        trajectory_domain_of_validity=_traj_dov,
        # --- waste axis ---
        waste_event_count=impl_result.waste_event_count,
        waste_events=impl_result.waste_events,
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
        # --- cost annotation ---
        session_cost_usd=cost_usd,
        cost_approximate=cost_approx,
        cost_domain_of_validity=cost_dov,
        cost_server_tool_warnings=cost_server_tool_warnings,
        # --- attribution fractions (RR1) ---
        context_resend_pct=_resend_pct,
        context_growth_pct=_growth_pct,
        output_pct=_out_pct,
        waste_pct=_waste_pct,
    )


__all__ = [
    "ThreeAxisResult",
    "EfficiencyResult",
    "score_session",
    "load_baselines",
    "TOKEN_DOMAIN_OF_VALIDITY",
    "TRAJECTORY_DOMAIN_OF_VALIDITY",
    "WASTE_DOMAIN_OF_VALIDITY",
    "build_api_trajectory_dov",
]
