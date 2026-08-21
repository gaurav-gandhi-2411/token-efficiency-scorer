from __future__ import annotations

"""tests/test_plan_roi.py — XX1: plan-cost config loading and ROI proration.

Covers: no config (not an error), malformed config (fails loud), a single
plan spanning the whole window, a plan CHANGE landing inside the window
(day-by-day proration, not a flat monthly figure), a window predating the
first plan entry, and the two "refuse to print a ratio" edge cases --
no plan configured, and a window with zero priced sessions.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tes.plan import (
    PlanPeriod,
    compute_roi,
    load_plan_config,
    prorated_plan_cost,
    resolve_plan_config_path,
)


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_missing_config_returns_empty_list_not_an_error(tmp_path: Path):
    assert load_plan_config(tmp_path / "does-not-exist.json") == []


def test_malformed_json_raises_value_error(tmp_path: Path):
    p = tmp_path / "plan.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_plan_config(p)


def test_missing_plans_key_raises_value_error(tmp_path: Path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"something_else": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing top-level 'plans' key"):
        load_plan_config(p)


def test_bad_entry_names_its_index(tmp_path: Path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"plans": [{"name": "X"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"plans\[0\]"):
        load_plan_config(p)


def test_valid_config_parses_and_sorts_by_effective_from(tmp_path: Path):
    p = tmp_path / "plan.json"
    p.write_text(
        json.dumps(
            {
                "plans": [
                    {"name": "Max", "monthly_cost_usd": 200, "effective_from": "2026-07-01"},
                    {"name": "Pro", "monthly_cost_usd": 20, "effective_from": "2026-01-01"},
                ]
            }
        ),
        encoding="utf-8",
    )

    plans = load_plan_config(p)

    assert [pl.name for pl in plans] == ["Pro", "Max"]  # sorted ascending


def test_resolve_plan_config_path_env_var(tmp_path: Path, monkeypatch):
    custom = tmp_path / "custom.json"
    monkeypatch.setenv("TES_PLAN_PATH", str(custom))
    assert resolve_plan_config_path(None) == custom


def test_resolve_plan_config_path_explicit_arg_wins_over_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TES_PLAN_PATH", str(tmp_path / "env.json"))
    explicit = tmp_path / "explicit.json"
    assert resolve_plan_config_path(explicit) == explicit


# ---------------------------------------------------------------------------
# Proration
# ---------------------------------------------------------------------------


def test_single_plan_spanning_whole_window_prorates_by_day():
    plans = [PlanPeriod(name="Max", monthly_cost_usd=300, effective_from=_dt(2026, 1, 1).date())]
    cost, names = prorated_plan_cost(plans, _dt(2026, 8, 1), _dt(2026, 8, 8))  # 7 days

    assert cost == pytest.approx(300 / 30 * 7)
    assert names == ["Max"]


def test_plan_change_mid_window_prices_each_day_at_its_own_plan():
    """4 days at $20/mo, 3 days at $200/mo -- NOT a flat monthly figure for
    the whole 7-day window, and NOT an average of the two rates either."""
    plans = [
        PlanPeriod(name="Pro", monthly_cost_usd=20, effective_from=_dt(2026, 1, 1).date()),
        PlanPeriod(name="Max", monthly_cost_usd=200, effective_from=_dt(2026, 8, 5).date()),
    ]
    cost, names = prorated_plan_cost(plans, _dt(2026, 8, 1), _dt(2026, 8, 8))  # Aug 1-7

    expected = (20 / 30 * 4) + (200 / 30 * 3)  # Aug 1-4 Pro, Aug 5-7 Max
    assert cost == pytest.approx(expected)
    assert names == ["Max", "Pro"]  # sorted


def test_window_entirely_before_first_plan_entry_prices_as_zero():
    plans = [PlanPeriod(name="Max", monthly_cost_usd=200, effective_from=_dt(2026, 9, 1).date())]
    cost, names = prorated_plan_cost(plans, _dt(2026, 8, 1), _dt(2026, 8, 8))

    assert cost == 0.0
    assert names == []


def test_no_plans_configured_prorates_to_zero():
    assert prorated_plan_cost([], _dt(2026, 8, 1), _dt(2026, 8, 8)) == (0.0, [])


def test_non_midnight_aligned_window_prorates_to_exact_elapsed_days_not_calendar_dates():
    """Regression: a real `--week` window is `now - 7 days` to `now`, and
    `now` almost never lands exactly on midnight -- naive calendar-date
    counting (window_start.date() through window_end.date()) inflates a
    7.0-elapsed-day window to 8 calendar dates' worth of plan cost whenever
    the window doesn't start and end at midnight, which is every real
    invocation. Must price exactly 7.0 days' worth, not 8."""
    plans = [PlanPeriod(name="Max", monthly_cost_usd=300, effective_from=_dt(2026, 1, 1).date())]
    window_start = datetime(2026, 8, 1, 14, 30, tzinfo=UTC)
    window_end = datetime(2026, 8, 8, 14, 30, tzinfo=UTC)  # exactly 7.0 days later

    cost, _names = prorated_plan_cost(plans, window_start, window_end)

    assert cost == pytest.approx(300 / 30 * 7.0)  # NOT 300 / 30 * 8


# ---------------------------------------------------------------------------
# ROI -- must refuse a ratio the data can't support (XX1.2)
# ---------------------------------------------------------------------------


def test_roi_refuses_when_no_plan_configured():
    assert compute_roi(100.0, 5, [], _dt(2026, 8, 1), _dt(2026, 8, 8)) is None


def test_roi_refuses_when_zero_priced_sessions():
    plans = [PlanPeriod(name="Max", monthly_cost_usd=200, effective_from=_dt(2026, 1, 1).date())]
    assert compute_roi(0.0, 0, plans, _dt(2026, 8, 1), _dt(2026, 8, 8)) is None


def test_roi_refuses_when_window_predates_plan_history():
    plans = [PlanPeriod(name="Max", monthly_cost_usd=200, effective_from=_dt(2026, 9, 1).date())]
    # priced_session_count > 0 but every day in the window has no plan --
    # plan_cost prorates to 0, which would divide-by-zero into a fake ratio.
    assert compute_roi(50.0, 3, plans, _dt(2026, 8, 1), _dt(2026, 8, 8)) is None


def test_roi_computes_the_multiple_correctly():
    plans = [PlanPeriod(name="Max", monthly_cost_usd=300, effective_from=_dt(2026, 1, 1).date())]
    result = compute_roi(700.0, 5, plans, _dt(2026, 8, 1), _dt(2026, 8, 8))  # 7 days

    assert result is not None
    plan_cost = 300 / 30 * 7
    assert result.plan_cost_usd == pytest.approx(plan_cost)
    assert result.api_equivalent_usd == pytest.approx(700.0)
    assert result.multiple == pytest.approx(700.0 / plan_cost)
    assert result.plan_names == ["Max"]
