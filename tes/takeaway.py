from __future__ import annotations

"""tes/takeaway.py -- the deterministic, data-gated cost takeaway and lever hint.

One implementation shared by the dashboard (tes/web/server.py) and the CLI `score` command, so the
two surfaces can never disagree about when a lever fires (previously this lived in the web module
and the CLI printed nothing).
"""

from tes.attribution import AttributionResult
from tes.web.cost_format import format_unpriced


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
    waste_str = (
        f"; detectable waste ${waste_usd:.2f}" if waste_usd > 0.001 else "; no detectable waste"
    )

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
