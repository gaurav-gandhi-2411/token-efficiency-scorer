from __future__ import annotations

"""tes/json_out.py -- the stable `--json` documents for cost, budget, impact.

Contract (documented in docs/JSON_OUTPUT.md, pinned by tests/test_cli_json.py):

* stdout carries exactly one JSON document and nothing else; warnings and errors go to stderr.
* every document starts with `schema_version` (int) and `command` (str).
* the key set of a document is fixed per command: a field that does not apply is `null` (or an
  empty list), never absent, so consumers can index without `.get()` guards.
* money fields that can be incomplete carry `priced` (bool) and `unpriced_models` (list): when
  `priced` is false the USD figure is a floor (priced part only), never a true total.
* adding a key is backwards compatible; renaming or removing one bumps SCHEMA_VERSION.

The builders are pure (data in, dict out) so the shapes can be tested without a CLI run.
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from tes.legacy import LEGACY_LABEL
from tes.web.cost_format import cost_is_known

if TYPE_CHECKING:
    from tes.alarm import AlarmResult
    from tes.alarm_baseline import AlarmThreshold
    from tes.budget import BudgetProjection
    from tes.cost_period import PeriodCostReport
    from tes.impact import ImpactReport
    from tes.live_monitor import LiveSessionState

SCHEMA_VERSION = 1


def envelope(command: str, **fields: Any) -> dict[str, Any]:
    """Start a document: schema_version and command first, then the command's own fields."""
    return {"schema_version": SCHEMA_VERSION, "command": command, **fields}


def emit(payload: dict[str, Any]) -> None:
    """Print one JSON document to stdout (the only thing a --json run writes there)."""
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _iso(value: datetime) -> str:
    return value.isoformat()


# ----------------------------------------------------------------------------- cost


def cost_roi_payload(report: PeriodCostReport, plan_config: str | None) -> dict[str, Any]:
    """ROI block for `cost --roi --json`. `status` says why numbers are null, if they are."""
    from tes.plan import compute_roi, load_plan_config  # noqa: PLC0415

    blank: dict[str, Any] = {
        "status": "ok",
        "plan_names": [],
        "plan_cost_usd": None,
        "api_equivalent_usd": None,
        "multiple": None,
        "is_floor": False,
        "error": None,
    }
    try:
        plans = load_plan_config(plan_config)
    except ValueError as exc:
        return {**blank, "status": "plan_config_error", "error": str(exc)}
    if not plans:
        return {**blank, "status": "no_plan"}
    result = compute_roi(
        report.total_usd, report.session_count, plans, report.period_start, report.period_end
    )
    if result is None:
        return {**blank, "status": "no_priced_sessions"}
    return {
        **blank,
        "plan_names": list(result.plan_names),
        "plan_cost_usd": result.plan_cost_usd,
        "api_equivalent_usd": result.api_equivalent_usd,
        "multiple": result.multiple,
        # Unpriced turns are excluded from the API-equivalent figure, so the multiple is a floor.
        "is_floor": bool(report.unpriced_models),
    }


def cost_payload(report: PeriodCostReport, roi: dict[str, Any] | None) -> dict[str, Any]:
    """`tes cost --json`. `roi` is null unless --roi was passed."""
    return envelope(
        "cost",
        period={
            "label": report.period_label,
            "start": _iso(report.period_start),
            "end": _iso(report.period_end),
        },
        total_usd=report.total_usd,
        cost_known=cost_is_known(report.total_usd, report.unpriced_models),
        priced=report.priced,
        unpriced_models=list(report.unpriced_models),
        unpriced_models_incomplete=report.unpriced_models_incomplete,
        session_count=report.session_count,
        sessions_missing_cost=report.sessions_missing_cost,
        session_coverage_pct=report.session_coverage_pct,
        token_coverage_pct=report.token_coverage_pct,
        token_total=report.token_total,
        token_priced=report.token_priced,
        tokens_unpriced=report.tokens_unpriced,
        sessions_unpriced=report.sessions_unpriced,
        legacy_rows_excluded=report.legacy_rows_excluded,
        legacy={
            "label": LEGACY_LABEL,
            "session_count": report.legacy_rows_excluded,
            "total_usd": report.legacy_total_usd,
            "included_in_total_usd": False,
        },
        by_project=[
            {
                "project": b.project_label,
                "total_usd": b.total_usd,
                "session_count": b.session_count,
                "cost_known": cost_is_known(b.total_usd, b.unpriced_models),
                "priced": not b.unpriced_models,
                "unpriced_models": list(b.unpriced_models),
            }
            for b in report.by_project
        ],
        roi=roi,
    )


# ----------------------------------------------------------------------------- budget


def budget_payload(
    projection: BudgetProjection | None, window_days: int, legacy_rows_excluded: int = 0
) -> dict[str, Any]:
    """`tes budget --json`. `available` is false (fields null) when the window has no cost data."""
    if projection is None:
        return envelope(
            "budget",
            available=False,
            window_days=window_days,
            session_count=0,
            days_observed=None,
            total_usd_so_far=None,
            projected_usd_for_window=None,
            cost_known=None,
            priced=None,
            unpriced_models=[],
            legacy_rows_excluded=legacy_rows_excluded,
            message=f"No sessions with cost data in the last {window_days} days.",
        )
    return envelope(
        "budget",
        available=True,
        window_days=projection.window_days,
        session_count=projection.session_count,
        days_observed=projection.days_observed,
        total_usd_so_far=projection.total_usd_so_far,
        projected_usd_for_window=projection.projected_usd_for_window,
        cost_known=cost_is_known(projection.total_usd_so_far, projection.unpriced_models),
        priced=projection.priced,
        unpriced_models=list(projection.unpriced_models),
        legacy_rows_excluded=legacy_rows_excluded,
        message=projection.message,
    )


# ----------------------------------------------------------------------------- rescore


def rescore_payload(summary: dict[str, int], *, dry_run: bool, limit: int | None) -> dict[str, Any]:
    """`tes rescore --json`. On a dry run `rescored` is what WOULD be rescored (nothing written)."""
    rescored = summary["refreshed"]
    return envelope(
        "rescore",
        dry_run=dry_run,
        limit=limit,
        legacy_rows=summary["legacy_rows"],
        rescored=rescored,
        skipped_source_missing=summary["missing_source"],
        failed=summary["errors"],
        not_attempted=summary["not_attempted"],
        remaining_legacy=summary["legacy_rows"] - (0 if dry_run else rescored),
    )


# ----------------------------------------------------------------------------- impact


def impact_payload(report: ImpactReport, top_n: int) -> dict[str, Any]:
    """`tes impact --json`: the counts, plus the two fractions that qualify them."""

    def churn(items: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "path": c.path,
                "edits": c.edits,
                "additions": c.additions,
                "deletions": c.deletions,
                "sessions_touched": c.sessions_touched,
            }
            for c in items
        ]

    return envelope(
        "impact",
        top_n=top_n,
        sessions_with_data=report.sessions_with_data,
        sessions_legacy=report.sessions_legacy,
        total_operations=report.total_operations,
        total_additions=report.total_additions,
        total_deletions=report.total_deletions,
        prior_content_unknown_additions=report.prior_content_unknown_additions,
        prior_content_unknown_pct=report.prior_content_unknown_pct,
        untested_tool_shape_operations=report.untested_tool_shape_operations,
        untested_tool_shape_pct=report.untested_tool_shape_pct,
        top_files=churn(report.top_files),
        top_directories=churn(report.top_directories),
    )


# ----------------------------------------------------------------------------- monitor


def monitor_payload(
    status: str,
    cc_path: str,
    live: LiveSessionState | None = None,
    alarm: AlarmResult | None = None,
    source_path: str | None = None,
    threshold: AlarmThreshold | None = None,
) -> dict[str, Any]:
    """`tes monitor --json`.

    status: "no_active_session" | "insufficient_data" | "ok". Session fields are null unless
    status is "ok". `alarm` is null when none fired (exit code 0) and an object when it did
    (exit code 3). `alarm_baseline` (status "ok" only) always says what the alarm compared
    against, or why it is disabled: status, tier, threshold_tokens, n, percentile, window_days,
    era, task_type, reason.
    """
    return envelope(
        "monitor",
        status=status,
        active=status != "no_active_session",
        cc_path=cc_path,
        source_path=live.source_path if live is not None else source_path,
        session_id=live.session_id if live else None,
        task_type=live.task_type if live else None,
        live_cost_usd=live.live_cost_usd if live else None,
        cost_known=(cost_is_known(live.live_cost_usd, live.live_unpriced_models) if live else None),
        priced=live.live_priced if live else None,
        unpriced_models=list(live.live_unpriced_models) if live else [],
        live_context_tokens=live.live_context_tokens if live else None,
        live_resend_ratio=live.live_resend_ratio if live else None,
        context_resend_dominant=live.context_resend_dominant if live else None,
        ai_turn_count=live.ai_turn_count if live else None,
        domain_of_validity=live.domain_of_validity if live else None,
        alarm=(
            {
                "message": alarm.message,
                "resend_pct": alarm.resend_pct,
                "baseline_p75_tokens": alarm.baseline_p75_tokens,
                "plan_type": alarm.plan_type,
                "threshold_tokens": alarm.baseline_p75_tokens,
                "baseline_tier": alarm.baseline_tier,
                "baseline_n": alarm.baseline_n,
                "baseline_percentile": alarm.baseline_percentile,
            }
            if alarm is not None
            else None
        ),
        alarm_baseline=threshold.to_dict() if threshold is not None else None,
    )


# ----------------------------------------------------------------------------- patterns


def patterns_payload(cache: dict[str, Any]) -> dict[str, Any]:
    """`tes patterns --json`.

    The fixed envelope keys are `valid`, `status`, `n_sessions`, `domain_of_validity` and
    `analysis`; `analysis` is the intelligence cache exactly as `tes patterns` reads it
    (archetypes, k, silhouette, anomaly_count ...) when valid, else null.
    """
    valid = bool(cache.get("valid"))
    return envelope(
        "patterns",
        valid=valid,
        status=cache.get("status", "Pattern analysis unavailable."),
        n_sessions=cache.get("n_sessions"),
        domain_of_validity=cache.get("domain_of_validity"),
        analysis=cache if valid else None,
    )
