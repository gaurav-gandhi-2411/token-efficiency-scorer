from __future__ import annotations

"""tests/test_price_freshness.py — Tests for tes.cost.check_price_table_staleness.

Minimum-viable port of adk-tracegauge's staleness-guard concept (S1 audit
fix, 0.10.2): a price entry older than STALE_THRESHOLD_DAYS must be flagged,
never silently trusted. See scripts/check_price_freshness.py for the CI
entry point that calls this at the actual bundled table.
"""

from datetime import date, timedelta

from tes.cost import STALE_THRESHOLD_DAYS, check_price_table_staleness, load_price_table

_TODAY = date(2026, 8, 15)


def _prices(models: dict) -> dict:
    return {
        "as_of": "2026-08-15",
        "source_url": "https://example.invalid/pricing",
        "cache_multipliers": {"read": 0.1, "write_5min": 1.25, "write_1hr": 2.0},
        "models": models,
        "model_patterns": [],
        "default_model": "fresh-model",
        "approximate_threshold_pct": 25,
    }


# ---------------------------------------------------------------------------
# Test 1 — A fresh entry (as_of == today) is not flagged
# ---------------------------------------------------------------------------


def test_fresh_entry_not_flagged() -> None:
    prices = _prices(
        {
            "fresh-model": {
                "input_usd_per_mtok": 1.0,
                "output_usd_per_mtok": 5.0,
                "as_of": "2026-08-15",
            }
        }
    )
    stale = check_price_table_staleness(prices, today=_TODAY)
    assert stale == []


# ---------------------------------------------------------------------------
# Test 2 — An entry exactly STALE_THRESHOLD_DAYS old is NOT stale (strictly
# greater than, matching the same boundary convention used elsewhere in this
# codebase, e.g. compute_session_cost's approximate_threshold_pct check).
# ---------------------------------------------------------------------------


def test_entry_exactly_at_threshold_not_stale() -> None:
    boundary_date = (_TODAY - timedelta(days=STALE_THRESHOLD_DAYS)).isoformat()
    prices = _prices(
        {
            "boundary-model": {
                "input_usd_per_mtok": 1.0,
                "output_usd_per_mtok": 5.0,
                "as_of": boundary_date,
            }
        }
    )
    stale = check_price_table_staleness(prices, today=_TODAY)
    assert stale == []


# ---------------------------------------------------------------------------
# Test 3 — An entry one day past the threshold IS flagged
# ---------------------------------------------------------------------------


def test_entry_one_day_past_threshold_is_stale() -> None:
    stale_date = (_TODAY - timedelta(days=STALE_THRESHOLD_DAYS + 1)).isoformat()
    prices = _prices(
        {
            "stale-model": {
                "input_usd_per_mtok": 1.0,
                "output_usd_per_mtok": 5.0,
                "as_of": stale_date,
            }
        }
    )
    stale = check_price_table_staleness(prices, today=_TODAY)
    assert len(stale) == 1
    model_key, as_of, age_days, source_url = stale[0]
    assert model_key == "stale-model"
    assert as_of == stale_date
    assert age_days == STALE_THRESHOLD_DAYS + 1
    assert source_url == "https://example.invalid/pricing"


# ---------------------------------------------------------------------------
# Test 4 — retired: true entries are exempt regardless of age
# ---------------------------------------------------------------------------


def test_retired_entry_exempt_from_staleness() -> None:
    prices = _prices(
        {
            "ancient-retired-model": {
                "input_usd_per_mtok": 1.0,
                "output_usd_per_mtok": 5.0,
                "as_of": "2000-01-01",
                "retired": True,
            }
        }
    )
    stale = check_price_table_staleness(prices, today=_TODAY)
    assert stale == []


# ---------------------------------------------------------------------------
# Test 5 — an entry with no per-model as_of inherits the table-level as_of
# ---------------------------------------------------------------------------


def test_entry_without_own_as_of_inherits_table_level() -> None:
    prices = _prices({"no-own-date-model": {"input_usd_per_mtok": 1.0, "output_usd_per_mtok": 5.0}})
    prices["as_of"] = "2026-08-15"
    stale = check_price_table_staleness(prices, today=_TODAY)
    assert stale == []  # table-level as_of (today) is used, not stale

    prices["as_of"] = (_TODAY - timedelta(days=STALE_THRESHOLD_DAYS + 1)).isoformat()
    stale = check_price_table_staleness(prices, today=_TODAY)
    assert len(stale) == 1
    assert stale[0][0] == "no-own-date-model"


# ---------------------------------------------------------------------------
# Test 6 — unparseable/missing as_of fails closed (treated as stale)
# ---------------------------------------------------------------------------


def test_unparseable_as_of_fails_closed() -> None:
    prices = _prices(
        {
            "bad-date-model": {
                "input_usd_per_mtok": 1.0,
                "output_usd_per_mtok": 5.0,
                "as_of": "not-a-date",
            }
        }
    )
    stale = check_price_table_staleness(prices, today=_TODAY)
    assert len(stale) == 1
    assert stale[0][0] == "bad-date-model"
    assert stale[0][2] == -1  # sentinel age for unparseable dates


# ---------------------------------------------------------------------------
# Test 7 — the actual bundled table is fresh as of the date it was written
# (regression guard: this fails the day the bundled table genuinely goes
# stale, which is the intended, correct behavior -- see
# scripts/check_price_freshness.py, the CI job that catches this in
# production instead of a local pytest run).
# ---------------------------------------------------------------------------


def test_bundled_table_is_currently_fresh() -> None:
    bundled = load_price_table()
    stale = check_price_table_staleness(bundled, today=date.fromisoformat(bundled["as_of"]))
    assert stale == [], f"bundled table has stale entries as of its own as_of: {stale}"
