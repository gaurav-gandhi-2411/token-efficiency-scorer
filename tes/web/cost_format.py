from __future__ import annotations

"""tes/web/cost_format.py — Formatting helpers for cost annotation display.

Cost is an annotation on the token axis — not a score, not a composite.
These helpers format dollar amounts and baseline-relative framing for
both the CLI (format_human) and the web dashboard templates.
"""


def format_cost_usd(usd: float | None) -> str:
    """Format dollar amount for display.

    Returns '$X.XX' for known values or '—' for None.
    """
    if usd is None:
        return "—"
    return f"${usd:.2f}"


def format_cost_vs_baseline(
    cost_usd: float | None,
    band: tuple[float, float, float] | None,
) -> str:
    """Return framing string relative to the user's lean-subset cost baseline.

    Examples:
        '360% above your typical efficient run (~$3.87)'
        '12% below your typical efficient run (~$5.20)'
        'no baseline comparison'

    Parameters
    ----------
    cost_usd:
        Session cost in USD. None → 'no baseline comparison'.
    band:
        (p25_usd, median_usd, p75_usd) from compute_baseline_cost_band.
        None → 'no baseline comparison'.
    """
    if band is None or cost_usd is None:
        return "no baseline comparison"
    _, med, _ = band
    if med <= 0:
        return "no baseline comparison"
    pct = (cost_usd - med) / med * 100
    direction = "above" if pct >= 0 else "below"
    return f"{abs(pct):.0f}% {direction} your typical efficient run (~${med:.2f})"


def format_cost_pct_vs_baseline(
    cost_usd: float | None,
    band: tuple[float, float, float] | None,
) -> int | None:
    """Return integer percent vs baseline median, or None if unavailable.

    Positive = above median (more expensive), negative = below (cheaper).
    """
    if band is None or cost_usd is None:
        return None
    _, med, _ = band
    if med <= 0:
        return None
    return round((cost_usd - med) / med * 100)


def format_price_provenance(prices: dict) -> str:
    """Return a one-line price provenance string for display.

    Format: 'Prices as of 2026-06-09 · cache read 0.10× · creation 1.25× · output full rate'
    """
    as_of: str = prices.get("as_of", "unknown")
    cache_mults: dict = prices.get("cache_multipliers", {})
    read_mult: float = cache_mults.get("read", 0.1)
    write_mult: float = cache_mults.get("write_5min", 1.25)
    return (
        f"Prices as of {as_of} · "
        f"cache read {read_mult:.2f}× · "
        f"creation {write_mult:.2f}× · "
        f"output full rate"
    )


__all__ = [
    "format_cost_usd",
    "format_cost_vs_baseline",
    "format_cost_pct_vs_baseline",
    "format_price_provenance",
]
