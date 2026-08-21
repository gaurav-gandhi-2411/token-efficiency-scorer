from __future__ import annotations

"""tests/test_cost_format.py — Tests for tes.web.cost_format helpers.

Cost is an annotation on the token axis — not a score, not a composite.
These helpers format dollar amounts, baseline-relative framing, and price provenance.
"""


from tes.web.cost_format import (
    format_cost_pct_vs_baseline,
    format_cost_usd,
    format_cost_vs_baseline,
    format_price_provenance,
)

# ---------------------------------------------------------------------------
# format_cost_usd
# ---------------------------------------------------------------------------


def test_format_cost_usd_none_returns_dash() -> None:
    assert format_cost_usd(None) == "—"


def test_format_cost_usd_zero() -> None:
    assert format_cost_usd(0.0) == "$0.00"


def test_format_cost_usd_typical() -> None:
    assert format_cost_usd(17.80) == "$17.80"


def test_format_cost_usd_small() -> None:
    assert format_cost_usd(0.001) == "$0.00"  # 2 decimal places


def test_format_cost_usd_large() -> None:
    assert format_cost_usd(1234.56) == "$1234.56"


# ---------------------------------------------------------------------------
# format_cost_vs_baseline
# ---------------------------------------------------------------------------


def test_format_vs_baseline_none_band() -> None:
    result = format_cost_vs_baseline(5.0, None)
    assert result == "no baseline comparison"


def test_format_vs_baseline_none_cost() -> None:
    result = format_cost_vs_baseline(None, (1.0, 3.0, 5.0))
    assert result == "no baseline comparison"


def test_format_vs_baseline_zero_median() -> None:
    result = format_cost_vs_baseline(5.0, (0.0, 0.0, 0.0))
    assert result == "no baseline comparison"


def test_format_vs_baseline_above() -> None:
    # cost=17.80, median=3.87 → pct ≈ 360%
    band = (2.0, 3.87, 6.0)
    result = format_cost_vs_baseline(17.80, band)
    assert "above" in result
    assert "$3.87" in result
    # pct = (17.80 - 3.87) / 3.87 * 100 ≈ 359.9... rounds to 360
    assert "360%" in result


def test_format_vs_baseline_below() -> None:
    band = (2.0, 5.0, 8.0)
    result = format_cost_vs_baseline(2.50, band)
    assert "below" in result
    assert "$5.00" in result
    # pct = (2.50 - 5.00) / 5.00 * 100 = -50.0%
    assert "50%" in result


def test_format_vs_baseline_at_median() -> None:
    band = (2.0, 5.0, 8.0)
    result = format_cost_vs_baseline(5.0, band)
    assert "above" in result  # 0% above
    assert "0%" in result


# ---------------------------------------------------------------------------
# format_cost_pct_vs_baseline
# ---------------------------------------------------------------------------


def test_pct_none_when_no_band() -> None:
    assert format_cost_pct_vs_baseline(5.0, None) is None


def test_pct_none_when_no_cost() -> None:
    assert format_cost_pct_vs_baseline(None, (1.0, 3.0, 5.0)) is None


def test_pct_none_when_zero_median() -> None:
    assert format_cost_pct_vs_baseline(5.0, (0.0, 0.0, 1.0)) is None


def test_pct_above() -> None:
    # 5.0 vs median 3.0 → +66.7 → rounds to 67
    result = format_cost_pct_vs_baseline(5.0, (1.0, 3.0, 6.0))
    assert result == 67


def test_pct_below() -> None:
    # 1.5 vs median 3.0 → -50.0 → rounds to -50
    result = format_cost_pct_vs_baseline(1.5, (1.0, 3.0, 6.0))
    assert result == -50


def test_pct_at_median() -> None:
    result = format_cost_pct_vs_baseline(3.0, (1.0, 3.0, 6.0))
    assert result == 0


# ---------------------------------------------------------------------------
# format_price_provenance
# ---------------------------------------------------------------------------


def test_price_provenance_contains_date() -> None:
    prices = {
        "as_of": "2026-06-09",
        "cache_multipliers": {"read": 0.1, "write_5min": 1.25},
    }
    result = format_price_provenance(prices)
    assert "2026-06-09" in result


def test_price_provenance_contains_multipliers() -> None:
    prices = {
        "as_of": "2026-06-09",
        "cache_multipliers": {"read": 0.1, "write_5min": 1.25},
    }
    result = format_price_provenance(prices)
    assert "0.10" in result
    assert "1.25" in result


def test_price_provenance_missing_multipliers_uses_defaults() -> None:
    prices = {"as_of": "2026-01-01", "cache_multipliers": {}}
    result = format_price_provenance(prices)
    assert "2026-01-01" in result
    assert "output full rate" in result


def test_price_provenance_unknown_date() -> None:
    result = format_price_provenance({})
    assert "unknown" in result
