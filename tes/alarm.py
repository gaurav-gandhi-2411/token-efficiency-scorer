from __future__ import annotations

"""tes/alarm.py — Data-gated, flat-plan-aware live session alarm.

Design: research/13_coach_alarm_honesty_design.md (reviewed and approved before
this file was written — see the "Review decisions" section).

The alarm fires ONLY when BOTH measured gates pass (no cry-wolf, same shape as
tes/intelligence/anomaly.py's per-cluster Tukey fence — data-driven, no
arbitrary global threshold):

  1. Magnitude gate — the live session's accumulated token count already
     exceeds a threshold from the user's OWN recent, same-model-era sessions
     (tes.alarm_baseline: p85, 30-day window, minimum-n fallback chain, silent
     with a stated reason when nothing qualifies; method and numbers in
     docs/ALARM.md). Callers that pass no `threshold` to check_alarm keep the
     original comparison against the verdict self-baseline's p75
     (tes.self_baseline.TypeBaseline — silent when 'building').
  2. Cause gate — context re-send is the DOMINANT component of live cost, so
     the "/compact" suggestion is actually relevant (a large but genuinely
     fresh-work session should not get a compaction nudge that wouldn't help it).

CAVEAT on gate 2, found during real-data verification before publish (2026-07-04):
on a heavy-usage store (long, iterative sessions where context is resent almost
every turn), gate 2 can be near-universally true — checked 74 real above-p75
sessions on the verifying developer's own store and found ZERO that were NOT
resend-dominant. In that regime, gate 1 (the user's own p75) is doing essentially
all of the real gating; gate 2 rides along honestly (it is still a real, correct
check) but currently has little INDEPENDENT discriminating power for that kind of
user. It still matters for a different usage profile (e.g. a large one-shot
generation-heavy session, which SHOULD stay silent) — kept for that case, but
don't assume both gates are equally load-bearing for every user.

Flat-plan awareness: both dollar and token framings are ALWAYS present in the
fired message (approved option 2.3-c) — the dollar figure is never hidden from
a Max-plan user, only demoted to a parenthetical "API-equivalent" note when
plan_type="max" is explicitly configured. Nothing here inspects live billing
state (no such signal exists locally, no egress is allowed) — plan_type is a
user-set display preference, not a detected fact.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tes.alarm_baseline import (
    DEFAULT_MIN_N,
    DEFAULT_PERCENTILE,
    DEFAULT_WINDOW_DAYS,
    STATUS_ACTIVE,
    AlarmThreshold,
    compute_alarm_threshold,
)
from tes.live_monitor import LiveSessionState
from tes.self_baseline import SelfBaselineState, TypeBaseline
from tes.web.cost_format import format_cost_display

PLAN_USAGE_BASED: str = "usage_based"
PLAN_MAX: str = "max"


@dataclass
class AlarmConfig:
    enabled: bool = False  # OFF by default — opt-in, matches background_judge posture
    plan_type: str = PLAN_USAGE_BASED  # "usage_based" | "max" — display emphasis ONLY, never a gate
    # Comparison distribution (tes.alarm_baseline): used when check_alarm is given a `threshold`.
    # Defaults come from the study in docs/ALARM.md; all three are plain knobs, not gates.
    percentile: float = DEFAULT_PERCENTILE
    window_days: int = DEFAULT_WINDOW_DAYS
    min_n: int = DEFAULT_MIN_N


@dataclass
class AlarmResult:
    session_id: str
    task_type: str
    message: str
    live_cost_usd: float
    live_context_tokens: int
    resend_pct: int
    baseline_p75_tokens: int  # legacy name; the threshold the session exceeded (any percentile)
    plan_type: str
    # Provenance of the threshold (None/"" on the legacy self-baseline path).
    baseline_tier: str = ""
    baseline_n: int = 0
    baseline_percentile: float | None = None


def threshold_for_live(
    live: LiveSessionState,
    db_path: Path | str,
    baselines: Mapping[str, Any] | None,
    config: AlarmConfig,
) -> AlarmThreshold:
    """Resolve the comparison threshold for ``live`` from the user's store (read-only)."""
    return compute_alarm_threshold(
        db_path,
        task_type=live.task_type,
        era=live.dominant_model,
        session_id=live.session_id,
        baselines=baselines,
        percentile=config.percentile,
        window_days=config.window_days,
        min_n=config.min_n,
    )


def check_alarm(
    live: LiveSessionState,
    self_bl: SelfBaselineState,
    config: AlarmConfig,
    threshold: AlarmThreshold | None = None,
) -> AlarmResult | None:
    """Return an AlarmResult only if both measured gates pass; otherwise None (silent).

    With ``threshold`` (tes.alarm_baseline.compute_alarm_threshold: recent, same-era percentile
    of the user's own sessions) the magnitude gate compares against it; a disabled threshold keeps
    the alarm silent. Without it the legacy self-baseline p75 is used (kept for old callers).
    """
    if not config.enabled:
        return None

    if threshold is not None:
        return _check_with_threshold(live, config, threshold)

    type_bl: TypeBaseline | None = self_bl.by_type.get(live.task_type)
    if type_bl is None or type_bl.source != "self" or type_bl.p75 is None:
        return None  # data-gated: no active self-baseline for this type yet

    if live.live_context_tokens <= type_bl.p75:
        return None  # magnitude gate not tripped

    if not live.context_resend_dominant:
        return None  # cause gate not tripped — /compact would not help this session

    resend_pct = round(live.live_resend_ratio * 100)
    message = format_alarm_message(live, type_bl, config, resend_pct)

    return AlarmResult(
        session_id=live.session_id,
        task_type=live.task_type,
        message=message,
        live_cost_usd=live.live_cost_usd,
        live_context_tokens=live.live_context_tokens,
        resend_pct=resend_pct,
        baseline_p75_tokens=type_bl.p75,
        plan_type=config.plan_type,
    )


def _check_with_threshold(
    live: LiveSessionState, config: AlarmConfig, threshold: AlarmThreshold
) -> AlarmResult | None:
    if threshold.status != STATUS_ACTIVE or threshold.threshold_tokens is None:
        return None  # data-gated: the reason is on the threshold, monitor shows it
    if live.live_context_tokens <= threshold.threshold_tokens:
        return None  # magnitude gate not tripped
    if not live.context_resend_dominant:
        return None  # cause gate not tripped — /compact would not help this session

    resend_pct = round(live.live_resend_ratio * 100)
    message = format_alarm_message(live, threshold, config, resend_pct)
    return AlarmResult(
        session_id=live.session_id,
        task_type=live.task_type,
        message=message,
        live_cost_usd=live.live_cost_usd,
        live_context_tokens=live.live_context_tokens,
        resend_pct=resend_pct,
        baseline_p75_tokens=threshold.threshold_tokens,
        plan_type=config.plan_type,
        baseline_tier=threshold.tier,
        baseline_n=threshold.n,
        baseline_percentile=threshold.percentile,
    )


def format_alarm_message(
    live: LiveSessionState,
    type_bl: TypeBaseline | AlarmThreshold,
    config: AlarmConfig,
    resend_pct: int,
) -> str:
    """Render the alarm text. Both $ and token framings are always present.

    plan_type="max" reorders the emphasis (tokens lead, dollars become a
    parenthetical) but never removes the dollar figure outright — see the
    module docstring and the approved design decision 2.3-c.
    """
    cost_str = (
        format_cost_display(live.live_cost_usd, live.live_unpriced_models, approx=True)
        + " (estimated, in progress)"
    )
    tokens_str = f"~{live.live_context_tokens:,} context tokens (estimated, in progress)"
    if isinstance(type_bl, AlarmThreshold):
        baseline_str = f"your own recent sessions ({type_bl.reason})"
    else:
        baseline_str = f"your own typical {live.task_type} session (p75: {type_bl.p75:,} tokens)"

    if config.plan_type == PLAN_MAX:
        body = (
            f"{tokens_str}, {resend_pct}% of which is re-sent context (measured) — "
            f"well above {baseline_str}. (API-equivalent: {cost_str}, not necessarily "
            "what you're billed on a flat plan.)"
        )
    else:
        body = (
            f"This session is at {cost_str} and {tokens_str}, {resend_pct}% of which is "
            f"re-sent context (measured) — well above {baseline_str}."
        )

    return body + " Consider `/compact`."


__all__ = [
    "PLAN_USAGE_BASED",
    "PLAN_MAX",
    "AlarmConfig",
    "AlarmResult",
    "check_alarm",
    "format_alarm_message",
    "threshold_for_live",
]
