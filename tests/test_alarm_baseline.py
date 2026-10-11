from __future__ import annotations

"""The alarm's comparison distribution: recent, same-era, with a fallback chain.
"""

from pathlib import Path
from typing import Any

import pytest
from tes.alarm_baseline import (
    DEFAULT_MIN_N,
    DEFAULT_PERCENTILE,
    DEFAULT_WINDOW_DAYS,
    TIER_DISABLED,
    TIER_RECENT,
    TIER_RECENT_ERA,
    TIER_RECENT_ERA_TYPE,
    TIER_RECENT_TYPE,
    TIER_SHIPPED,
    PoolRow,
    dominant_model,
    normalize_model,
    percentile_at_index,
    resolve_threshold,
)
from tes.store import open_db

DAY = 86400.0
NOW = 1_800_000_000.0
S5, S55 = "claude-sonnet-5", "claude-sonnet-5-5"


def _rows(
    n: int,
    *,
    task_type: str = "infra-deploy",
    era: str = S55,
    base: int = 100_000,
    age_days: float = 1.0,
    prefix: str = "s",
) -> list[PoolRow]:
    """n sessions with tokens base, base+1000, ... all `age_days` old (so spread is explicit)."""
    return [
        PoolRow(
            f"{prefix}-{era}-{task_type}-{i}", task_type, era, base + 1000 * i, NOW - age_days * DAY
        )
        for i in range(n)
    ]


def _resolve(rows: list[PoolRow], **kw: Any) -> Any:
    args: dict[str, Any] = {"task_type": "infra-deploy", "era": S55, "now": NOW}
    args.update(kw)
    return resolve_threshold(rows, **args)


def test_normalize_model_strips_date_and_context_suffix() -> None:
    assert normalize_model("claude-sonnet-5-5-20260901[1m]") == S55
    assert normalize_model("claude-sonnet-5") == S5
    assert normalize_model("<synthetic>") == ""
    assert normalize_model(None) == ""


def test_dominant_model_is_token_weighted_not_turn_counted() -> None:
    turns = [
        {"model": S5, "token_count_input": 1000, "cache_read": 900, "token_count_output": 50},
        {"model": S5, "token_count_input": 1000, "cache_read": 900, "token_count_output": 50},
        # one big turn on the other model outweighs two small ones
        {"model": S55, "token_count_input": 90_000, "cache_read": 0, "token_count_output": 1000},
    ]
    assert dominant_model(turns) == S55
    assert dominant_model([]) == ""
    assert dominant_model([{"model": "", "token_count_input": 5}]) == ""


def test_percentile_at_index_matches_the_baseline_builder_rule() -> None:
    values = list(range(1, 11))
    assert percentile_at_index(values, 0.8) == 9  # floor(10 * 0.8) = index 8
    assert percentile_at_index(values, 0.95) == 10
    with pytest.raises(ValueError):
        percentile_at_index([], 0.8)


def test_defaults_are_the_documented_ones() -> None:
    assert (DEFAULT_PERCENTILE, DEFAULT_WINDOW_DAYS, DEFAULT_MIN_N) == (0.85, 30, 10)


def test_same_era_and_type_is_the_first_tier() -> None:
    t = _resolve(_rows(12))
    assert t.status == "active"
    assert t.tier == TIER_RECENT_ERA_TYPE
    assert t.n == 12
    assert t.threshold_tokens == 100_000 + 1000 * int(12 * DEFAULT_PERCENTILE)
    assert "p85" in t.reason and S55 in t.reason


def test_eras_do_not_contaminate_each_other() -> None:
    old_era = _rows(15, era=S5, base=2_000_000)  # an earlier generation with 20x the scale
    new_era = _rows(15, era=S55, base=100_000)
    mixed = old_era + new_era
    t55 = _resolve(mixed, era=S55)
    t5 = _resolve(mixed, era=S5)
    assert t55.threshold_tokens is not None and t55.threshold_tokens < 200_000
    assert t5.threshold_tokens is not None and t5.threshold_tokens > 2_000_000
    assert t55.tier == t5.tier == TIER_RECENT_ERA_TYPE


def test_era_tier_pools_task_types_when_the_type_is_thin() -> None:
    rows = _rows(4, task_type="infra-deploy") + _rows(8, task_type="debug-fix")
    t = _resolve(rows)
    assert t.tier == TIER_RECENT_ERA
    assert t.n == 12


def test_falls_back_to_any_era_when_the_era_is_thin() -> None:
    rows = _rows(3, era=S55) + _rows(12, era=S5)
    t = _resolve(rows, era=S55)
    assert t.tier == TIER_RECENT_TYPE  # 15 infra-deploy sessions over both eras
    assert t.n == 15


def test_falls_back_to_all_types_and_eras() -> None:
    rows = _rows(3, task_type="debug-fix", era=S5) + _rows(9, task_type="ml-eval", era=S55)
    t = _resolve(rows)
    assert t.tier == TIER_RECENT
    assert t.n == 12


def test_unknown_era_skips_the_era_tiers() -> None:
    t = _resolve(_rows(12), era="")
    assert t.tier == TIER_RECENT_TYPE


def test_recency_window_drops_old_sessions() -> None:
    fresh = _rows(10, age_days=2, base=100_000)
    stale = _rows(10, age_days=DEFAULT_WINDOW_DAYS + 5, base=9_000_000, prefix="old")
    t = _resolve(fresh + stale)
    assert t.n == 10
    assert t.threshold_tokens is not None and t.threshold_tokens < 200_000
    # a longer window brings the old sessions (and their scale) back
    wide = _resolve(fresh + stale, window_days=90)
    assert wide.n == 20 and wide.threshold_tokens is not None and wide.threshold_tokens > 1_000_000


def test_sessions_after_now_and_the_live_session_itself_are_ignored() -> None:
    rows = _rows(10)
    future = [PoolRow("f", "infra-deploy", S55, 50_000_000, NOW + DAY)]
    own = [PoolRow("live-1", "infra-deploy", S55, 50_000_000, NOW - 60)]
    t = _resolve(rows + future + own, exclude_session_id="live-1")
    assert t.n == 10
    assert t.threshold_tokens is not None and t.threshold_tokens < 200_000


def test_min_n_is_a_hard_floor() -> None:
    assert _resolve(_rows(DEFAULT_MIN_N - 1)).status == "disabled"
    assert _resolve(_rows(DEFAULT_MIN_N)).status == "active"
    assert _resolve(_rows(5), min_n=5).status == "active"


SHIPPED = {
    "infra-deploy": {
        "available": True,
        "n": 15,
        "p75": 3_000_000,
        "ci95": {"p75": [1_000_000, 4_000_000]},
    },
    "ml-eval": {"available": False, "n": 3, "p75": None},
}


def test_shipped_band_is_used_with_its_upper_confidence_bound() -> None:
    t = _resolve(_rows(3), shipped_types=SHIPPED)
    assert (t.tier, t.status, t.threshold_tokens, t.n) == (TIER_SHIPPED, "active", 4_000_000, 15)
    assert "not your own history" in t.reason


def test_disabled_says_why_in_one_line() -> None:
    t = _resolve(_rows(3), task_type="ml-eval", shipped_types=SHIPPED)
    assert (t.tier, t.status, t.threshold_tokens) == (TIER_DISABLED, "disabled", None)
    assert "\n" not in t.reason
    assert "alarm disabled" in t.reason and "have 3" in t.reason and "ml-eval" in t.reason
    assert _resolve([]).status == "disabled"  # no shipped table at all


def test_dominant_model_column_is_added_idempotently(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    open_db(db).close()
    conn = open_db(db)  # second open must not fail on the existing column
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    conn.close()
    assert "dominant_model" in cols
