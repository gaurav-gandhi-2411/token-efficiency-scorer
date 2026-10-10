from __future__ import annotations

"""tes/web/cost_format.py — Formatting helpers for cost annotation display.

Cost is an annotation on the token axis — not a score, not a composite.
These helpers format dollar amounts and baseline-relative framing for
both the CLI (format_human) and the web dashboard templates.
"""

from collections.abc import Iterable
from typing import Any


def format_cost_usd(usd: float | None) -> str:
    """Format dollar amount for display.

    Returns '$X.XX' for known values or '—' for None.
    """
    if usd is None:
        return "—"
    return f"${usd:.2f}"


def format_unpriced(models: Iterable[str]) -> str:
    """'unpriced (model-a, model-b)' -- the display form for models missing from the price table."""
    return f"unpriced ({', '.join(sorted(set(models)))})"


def cost_is_known(usd: float | None, unpriced_models: Iterable[str] = ()) -> bool:
    """False when a USD figure is a placeholder rather than a measurement.

    That is: no cost was computed (None), or models are unpriced and nothing at all was priced
    (the stored 0.0 means "unknown", not "free"). A partly priced figure is known-but-a-floor
    (`priced` is false for it); a genuinely priced $0.00 is known.
    """
    if usd is None:
        return False
    return not (list(unpriced_models) and not usd)


def format_cost_display(
    usd: float | None,
    unpriced_models: Iterable[str] = (),
    decimals: int = 2,
    approx: bool = False,
) -> str:
    """Format a $ cost, never showing a misleading '$0.00' for turns that could not be priced.

    * no unpriced models  -> '$X.XX' ('—' for None), '~' prefixed when ``approx``
    * only unpriced turns -> 'unpriced (model)'   (usd is 0 or None: nothing was priced)
    * partial pricing     -> '$X.XX + unpriced (model)'  (priced subtotal, not a total)
    """
    models = sorted(set(unpriced_models))
    if not models:
        if usd is None:
            return "—"
        return f"{'~' if approx else ''}${usd:.{decimals}f}"
    if not usd:
        return format_unpriced(models)
    return f"{'~' if approx else ''}${usd:.{decimals}f} + {format_unpriced(models)}"


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


def _fmt_mult(mult: float) -> str:
    """'0.10', '0.05', '0.025', '1.25', '2.00': two decimals, a third only when it is not zero."""
    text = f"{mult:.3f}"
    return text[:-1] if text.endswith("0") else text


def _model_read_multiplier(model_key: str, prices: dict[str, Any]) -> float:
    """Cache-read multiplier ``model_key`` is billed at (own override, else the table default)."""
    default = float(prices.get("cache_multipliers", {}).get("read", 0.1))
    entry = prices.get("models", {}).get(model_key)
    own = entry.get("cache_read_multiplier") if isinstance(entry, dict) else None
    if isinstance(own, int | float) and not isinstance(own, bool):
        return float(own)
    return default


def format_price_provenance(prices: dict, model_keys: Iterable[str] | None = None) -> str:
    """Return a one-line price provenance string for display.

    Format: 'Prices as of 2026-10-09 · cache read 0.05× (claude-opus-5-5) · cache writes
    1.25× (5m) / 2.00× (1h) by the transcript's tier, unspecified assumed 5m · output full rate'

    With ``model_keys`` (the resolved price-table keys of the priced turns) it names the read
    multiplier actually applied to each model. Without them (the dashboard list, which spans
    sessions) it states the table default and lists the models that override it.
    """
    as_of: str = prices.get("as_of", "unknown")
    cache_mults: dict = prices.get("cache_multipliers", {})
    default_read: float = cache_mults.get("read", 0.1)
    write_5m: float = cache_mults.get("write_5min", 1.25)
    write_1h: float = cache_mults.get("write_1hr", 2.0)
    if model_keys is not None:
        groups: dict[float, list[str]] = {}
        for key in sorted(set(model_keys)):
            groups.setdefault(_model_read_multiplier(key, prices), []).append(key)
        if len(groups) == 1:
            ((mult, keys),) = groups.items()
            read_part = f"cache read {_fmt_mult(mult)}× ({', '.join(keys)})"
        elif groups:
            read_part = "cache read " + "; ".join(
                f"{_fmt_mult(m)}× ({', '.join(k)})" for m, k in sorted(groups.items())
            )
        else:
            read_part = f"cache read {_fmt_mult(default_read)}×"
    else:
        overrides: dict[float, list[str]] = {}
        for key, entry in sorted(prices.get("models", {}).items()):
            mult = _model_read_multiplier(key, prices)
            if isinstance(entry, dict) and mult != default_read:
                overrides.setdefault(mult, []).append(key)
        read_part = f"cache read {_fmt_mult(default_read)}× by default"
        if overrides:
            read_part += (
                " ("
                + "; ".join(
                    f"{_fmt_mult(m)}× on {', '.join(k)}"
                    for m, k in sorted(overrides.items(), reverse=True)
                )
                + ")"
            )
    return (
        f"Prices as of {as_of} · "
        f"{read_part} · "
        f"cache writes {_fmt_mult(write_5m)}× (5m) / {_fmt_mult(write_1h)}× (1h) by the transcript's tier, "
        f"unspecified assumed 5m · "
        f"output full rate"
    )


__all__ = [
    "cost_is_known",
    "format_cost_usd",
    "format_unpriced",
    "format_cost_display",
    "format_cost_vs_baseline",
    "format_cost_pct_vs_baseline",
    "format_price_provenance",
]
