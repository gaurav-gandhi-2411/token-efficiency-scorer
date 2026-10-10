from __future__ import annotations

"""tes/takeaway.py -- the deterministic cost breakdown and the (rare) absolute cost findings.

One implementation shared by the dashboard (tes/web/server.py) and the CLI `score` command, so the
two surfaces can never disagree.

Two different things, deliberately kept apart:

* the COST BREAKDOWN (:func:`build_cost_breakdown`) is informational: dollars and shares per
  bucket. It is not a finding. In Claude Code every turn re-reads the whole context, so re-send
  plus context growth is the bulk of the cost of nearly every session (69 to 99.5 percent of cost
  on each of one developer's 76 priced sessions, W1A diag 4a); "context dominates" therefore says
  nothing about any one session and is no longer offered as a lever.
* a FINDING (:func:`build_lever_hint`) is an absolute, deterministic rule that can be false for a
  session: detected waste above a dollar threshold (the proof turns are in the waste section) or
  output at 40 percent of cost or more. A quiet session gets no finding.
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

# Waste lever thresholds (absolute; unchanged since 0.10): >= $0.50 catches large-session waste
# regardless of share, >= 10 percent of cost AND >= $0.05 catches small-session waste. Below both
# is rounding noise.
_WASTE_ABS_USD = 0.50
_WASTE_REL_PCT = 10
_WASTE_REL_FLOOR_USD = 0.05
# Output lever threshold (absolute): output is at least 40 percent of the priced cost.
_OUTPUT_PCT = 40


def _waste_usd(attr: AttributionResult) -> float:
    return attr.rr_waste_usd + attr.rfr_waste_usd


def _hints(attr: AttributionResult) -> list[str]:
    """The findings that fire for ``attr`` (each rule independent; waste first). Empty = quiet.

    1. Waste : waste_usd >= 0.50  OR  (waste share >= 10% AND waste_usd >= 0.05)
    2. Output: output share of cost >= 40%
    """
    total = attr.total_usd
    if total == 0:
        return []
    waste_usd = _waste_usd(attr)
    waste_pct = round(waste_usd / total * 100)
    output_pct = round(attr.output_usd / total * 100)
    hints: list[str] = []
    if waste_usd >= _WASTE_ABS_USD or (
        waste_pct >= _WASTE_REL_PCT and waste_usd >= _WASTE_REL_FLOOR_USD
    ):
        hints.append(
            f"${waste_usd:.2f} ({waste_pct}% of the priced cost) in detectable waste; see the "
            "waste events for exact proof turns."
        )
    if output_pct >= _OUTPUT_PCT:
        hints.append(
            f"Output was {output_pct}% of the priced cost; shorter responses or fewer "
            "regenerations reduce this."
        )
    return hints


def build_lever_hint(
    attr: AttributionResult, unpriced_models: tuple[str, ...] | list[str] = ()
) -> str | None:
    """The finding sentence, or None when no absolute rule fires (the usual case).

    Never about context: see the module docstring. With pricing partial, the shares are of the
    priced part only and the sentence says so.
    """
    hints = _hints(attr)
    if not hints:
        return None
    text = " Also, ".join(hints)
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
