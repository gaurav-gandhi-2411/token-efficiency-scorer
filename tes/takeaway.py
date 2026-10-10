from __future__ import annotations

"""tes/takeaway.py -- the deterministic, data-gated cost takeaway and lever hint.

One implementation shared by the dashboard (tes/web/server.py) and the CLI `score` command, so the
two surfaces can never disagree about when a lever fires (previously this lived in the web module
and the CLI printed nothing).
"""

from typing import Any

from tes.attribution import AttributionResult
from tes.web.cost_format import format_unpriced

#: Stated on every surface that prints a breakdown, so it is never read as a finding.
BREAKDOWN_LABEL = "informational: where the priced cost went, not a finding"
#: Why the context share is not a lever (shown with the breakdown).
BREAKDOWN_NOTE = (
    "Every turn re-reads the session's context, so re-send plus growth is a large share of nearly "
    "every session's cost; it is shown for reference, not as something to fix."
)


def _waste_usd(attr: AttributionResult) -> float:
    return attr.rr_waste_usd + attr.rfr_waste_usd


def _takeaway_parts(
    attr: AttributionResult, unpriced_models: tuple[str, ...] | list[str] = ()
) -> tuple[str, list[str]]:
    """Return (description, hints): the cost description and the lever hints that fired.

    Deterministic takeaway with data-gated actionable hints (multiple can fire).

    Hint rules checked in order — each fires independently:
      1. Waste lever  : waste_usd >= 0.50  OR  (waste_pct >= 10 AND waste_usd >= 0.05)
                        → "$X.XX in detectable waste; see the waste events for exact proof turns."
                        Threshold keeps sub-$0.50 rounding-noise sessions silent.
      2. Context lever: context_pct (re-send + growth) >= 60% of total cost
                        → "a long context drove most of the cost; checkpointing or /compact reduces re-send."
      3. Output lever : output_pct >= 40% AND context_pct < 60%
                        → "output was a large cost share; shorter responses or fewer regenerations reduce this."
      No hint fires   → description only (no dominant lever — correct to stay quiet).
    """
    total_usd = attr.total_usd
    if total_usd == 0:
        if unpriced_models:
            return (
                f"Cost is {format_unpriced(unpriced_models)} — token bucket counts "
                "available in attribution table.",
                [],
            )
        return "No cost data — token bucket counts available in attribution table.", []

    def pct(v: float) -> int:
        return round(v / total_usd * 100)

    resend_pct = pct(attr.context_resend_usd)
    growth_pct = pct(attr.context_growth_usd)
    output_pct = pct(attr.output_usd)
    context_pct = resend_pct + growth_pct
    waste_usd = attr.rr_waste_usd + attr.rfr_waste_usd
    waste_pct = pct(waste_usd)

    parts: list[str] = []
    if context_pct > 0:
        parts.append(f"context ({resend_pct}% re-send + {growth_pct}% growth)")
    if output_pct > 0:
        parts.append(f"output ({output_pct}%)")

    cost_desc = "Cost: " + " and ".join(parts) if parts else "Cost: distributed across buckets"
    if waste_usd > 0.001:
        waste_str = f"; detectable waste ${waste_usd:.2f}"
        if unpriced_models:
            waste_str += f" + {format_unpriced(unpriced_models)}"
    elif unpriced_models:
        # Waste on the unpriced turns has no dollar value, so "no detectable waste" would overstate.
        waste_str = f"; waste cost on {format_unpriced(unpriced_models)} turns not priced"
    else:
        waste_str = "; no detectable waste"

    # Data-gated hints — each checked independently, waste first
    hints: list[str] = []

    # Waste lever: real waste, not rounding noise
    # Absolute: >= $0.50 catches large-session waste regardless of share
    # Relative: >= 10% share AND >= $0.05 catches small-session disproportionate waste
    if waste_usd >= 0.50 or (waste_pct >= 10 and waste_usd >= 0.05):
        hints.append(
            f"${waste_usd:.2f} in detectable waste; see the waste events for exact proof turns."
        )

    # Context lever: context is the majority of cost
    if context_pct >= 60:
        hints.append(
            "a long context drove most of the cost; checkpointing or /compact mid-session reduces re-send."
        )
    elif output_pct >= 40:
        # Output lever: only when context is not already dominant
        hints.append(
            "output was a large cost share; shorter responses or fewer regenerations reduce this."
        )

    return cost_desc + waste_str + ".", hints


def build_attribution_takeaway(
    attr: AttributionResult, unpriced_models: tuple[str, ...] | list[str] = ()
) -> str:
    """The dashboard's takeaway sentence: description plus any lever hints (see _takeaway_parts)."""
    description, hints = _takeaway_parts(attr, unpriced_models)
    if not hints:
        return description

    # First hint: " — "; subsequent: " Also, "
    hint_text = " — " + hints[0]
    for h in hints[1:]:
        hint_text += " Also, " + h

    return description + hint_text


def build_lever_hint(
    attr: AttributionResult, unpriced_models: tuple[str, ...] | list[str] = ()
) -> str | None:
    """The CLI's lever hint: the takeaway sentence, but only when a lever actually fires.

    Same thresholds as the dashboard (one implementation). Returns None when no lever fires, so
    a quiet session prints nothing. When nothing could be priced, the $-based levers cannot be
    evaluated; saying nothing would read as "no lever", so say that they are unavailable instead.
    When pricing is partial, the shares are of the priced subtotal; the hint says so.
    """
    if attr.total_usd == 0 and unpriced_models:
        return (
            f"Cost levers unavailable: cost is {format_unpriced(unpriced_models)}, so waste, "
            "context and output shares cannot be computed in $. Add the model(s) to "
            "TES_PRICE_TABLE or ~/.tes/prices.json."
        )
    _, hints = _takeaway_parts(attr, unpriced_models)
    if not hints:
        return None
    text = build_attribution_takeaway(attr, unpriced_models)
    if unpriced_models:
        text += f" (shares are of the priced part only; {format_unpriced(unpriced_models)})"
    return text


def build_cost_breakdown(
    attr: AttributionResult, unpriced_models: tuple[str, ...] | list[str] = ()
) -> dict[str, Any]:
    """Dollars, tokens and share of priced cost per bucket. Informational; never a finding.

    ``buckets`` partition ``total_usd`` (the redundant-read and retry-loop waste buckets are
    merged into ``waste``). ``share_pct`` is None when nothing was priced. Only priced turns are in
    the dollars; ``unpriced_models`` names the models whose turns are not.
    """
    total = attr.total_usd
    rows = [
        (
            "context_resend",
            "Context re-send (cache reads)",
            attr.context_resend_usd,
            attr.context_resend_tokens,
        ),
        (
            "context_growth",
            "Context growth (cache writes)",
            attr.context_growth_usd,
            attr.context_growth_tokens,
        ),
        ("output", "Output", attr.output_usd, attr.output_tokens),
        ("fresh_input", "Fresh input", attr.fresh_input_usd, attr.fresh_input_tokens),
        (
            "waste",
            "Detected waste (redundant reads, retry loops)",
            _waste_usd(attr),
            attr.rr_waste_tokens + attr.rfr_waste_tokens,
        ),
    ]
    unpriced = sorted(set(unpriced_models))
    note = ""
    if unpriced and total > 0:
        note = (
            f"Priced part only: {format_unpriced(unpriced)} turns are not in these dollars, "
            "so the true cost is higher."
        )
    elif unpriced:
        note = (
            f"Not computed: cost is {format_unpriced(unpriced)}. Add the model(s) to "
            "TES_PRICE_TABLE or ~/.tes/prices.json."
        )
    return {
        "label": BREAKDOWN_LABEL,
        "total_usd": round(total, 6),
        "priced": not unpriced,
        "unpriced_models": unpriced,
        "buckets": [
            {
                "key": key,
                "label": label,
                "usd": round(usd, 6),
                "share_pct": round(usd / total * 100, 1) if total > 0 else None,
                "tokens": tokens,
            }
            for key, label, usd, tokens in rows
        ],
        "note": note,
    }


def format_cost_breakdown_lines(breakdown: dict[str, Any]) -> list[str]:
    """Plain-text rows for the CLI (the caller wraps the notes). Empty when nothing was priced."""
    if not breakdown["total_usd"]:
        return []
    lines = []
    for b in breakdown["buckets"]:
        share = f"{b['share_pct']:5.1f}%" if b["share_pct"] is not None else "   n/a"
        lines.append(f"  {b['label']:<46} {'$' + format(b['usd'], '.2f'):>10}  {share}")
    lines.append(f"  {'Total (priced)':<46} {'$' + format(breakdown['total_usd'], '.2f'):>10}")
    return lines
