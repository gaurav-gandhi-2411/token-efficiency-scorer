from __future__ import annotations

"""tes/alarm.py — Data-gated, flat-plan-aware live session alarm.

Design: research/13_coach_alarm_honesty_design.md (reviewed and approved before
this file was written — see the "Review decisions" section).

The alarm fires ONLY when BOTH measured gates pass (no cry-wolf, same shape as
tes/intelligence/anomaly.py's per-cluster Tukey fence — data-driven, no
arbitrary global threshold):

  1. Magnitude gate — the live session's accumulated token count already
     exceeds the user's OWN p75 token band for this task_type
     (tes.self_baseline.TypeBaseline — silent when that type's baseline is
     still 'building', exactly like every other self-baseline consumer).
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

from dataclasses import dataclass

from tes.live_monitor import LiveSessionState
from tes.self_baseline import SelfBaselineState, TypeBaseline

PLAN_USAGE_BASED: str = "usage_based"
PLAN_MAX: str = "max"


@dataclass
class AlarmConfig:
    enabled: bool = False               # OFF by default — opt-in, matches background_judge posture
    plan_type: str = PLAN_USAGE_BASED   # "usage_based" | "max" — display emphasis ONLY, never a gate


@dataclass
class AlarmResult:
    session_id: str
    task_type: str
    message: str
    live_cost_usd: float
    live_context_tokens: int
    resend_pct: int
    baseline_p75_tokens: int
    plan_type: str


def check_alarm(
    live: LiveSessionState,
    self_bl: SelfBaselineState,
    config: AlarmConfig,
) -> AlarmResult | None:
    """Return an AlarmResult only if both measured gates pass; otherwise None (silent)."""
    if not config.enabled:
        return None

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


def format_alarm_message(
    live: LiveSessionState,
    type_bl: TypeBaseline,
    config: AlarmConfig,
    resend_pct: int,
) -> str:
    """Render the alarm text. Both $ and token framings are always present.

    plan_type="max" reorders the emphasis (tokens lead, dollars become a
    parenthetical) but never removes the dollar figure outright — see the
    module docstring and the approved design decision 2.3-c.
    """
    cost_str = f"~${live.live_cost_usd:.2f} (estimated, in progress)"
    tokens_str = f"~{live.live_context_tokens:,} context tokens (estimated, in progress)"
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
]
