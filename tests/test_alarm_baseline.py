from __future__ import annotations

"""Model-era helpers for the alarm baseline: normalized ids and the dominant model.
"""

from pathlib import Path

from tes.alarm_baseline import dominant_model, normalize_model
from tes.store import open_db

S5, S55 = "claude-sonnet-5", "claude-sonnet-5-5"


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


def test_dominant_model_column_is_added_idempotently(tmp_path: Path) -> None:
    db = tmp_path / "m.db"
    open_db(db).close()
    conn = open_db(db)  # second open must not fail on the existing column
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    conn.close()
    assert "dominant_model" in cols
