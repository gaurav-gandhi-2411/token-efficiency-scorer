from __future__ import annotations

"""tests/test_price_override.py — Tests for the price-table override mechanism."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from tes._digest import TurnDigest
from tes.cost import compute_turn_cost, load_price_table

_CUSTOM_PRICES: dict = {
    "as_of": "2099-01-01",
    "cache_multipliers": {"read": 0.1, "write_5min": 1.25, "write_1hr": 2.0},
    "models": {
        "claude-sonnet-4-6": {"input_usd_per_mtok": 6.0, "output_usd_per_mtok": 30.0},
    },
    "model_patterns": [
        {"prefix": "claude-sonnet-4-6", "model_key": "claude-sonnet-4-6"},
    ],
    "default_model": "claude-sonnet-4-6",
    "approximate_threshold_pct": 25,
}


# ---------------------------------------------------------------------------
# Test 1 — TES_PRICE_TABLE env var overrides bundled
# ---------------------------------------------------------------------------


def test_env_var_overrides_bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom_file = tmp_path / "custom_prices.json"
    custom_file.write_text(json.dumps(_CUSTOM_PRICES), encoding="utf-8")

    monkeypatch.setenv("TES_PRICE_TABLE", str(custom_file))
    monkeypatch.delenv("TES_PRICE_TABLE", raising=False)
    monkeypatch.setenv("TES_PRICE_TABLE", str(custom_file))

    table = load_price_table()
    assert table["as_of"] == "2099-01-01"


# ---------------------------------------------------------------------------
# Test 2 — ~/.tes/prices.json overrides bundled (when no env var)
# ---------------------------------------------------------------------------


def test_home_override_used_when_no_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    tes_dir = fake_home / ".tes"
    tes_dir.mkdir(parents=True)
    (tes_dir / "prices.json").write_text(json.dumps(_CUSTOM_PRICES), encoding="utf-8")

    monkeypatch.delenv("TES_PRICE_TABLE", raising=False)

    with patch("tes.cost.Path.home", return_value=fake_home):
        table = load_price_table()

    assert table["as_of"] == "2099-01-01"


# ---------------------------------------------------------------------------
# Test 3 — Explicit path arg takes precedence over env var
# ---------------------------------------------------------------------------


def test_explicit_path_takes_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file_a = tmp_path / "file_a.json"
    file_b = tmp_path / "file_b.json"
    file_a.write_text(json.dumps({**_CUSTOM_PRICES, "as_of": "file-a"}), encoding="utf-8")
    file_b.write_text(json.dumps({**_CUSTOM_PRICES, "as_of": "file-b"}), encoding="utf-8")

    monkeypatch.setenv("TES_PRICE_TABLE", str(file_a))

    table = load_price_table(path=file_b)
    assert table["as_of"] == "file-b"


# ---------------------------------------------------------------------------
# Test 4 — Bundled table loads when no override
# ---------------------------------------------------------------------------


def test_bundled_table_loads_when_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TES_PRICE_TABLE", raising=False)

    # Ensure ~/.tes/prices.json does not exist by pointing home to a temp dir without it
    with patch("tes.cost.Path.home", return_value=Path("/nonexistent_temp_home_xyz")):
        table = load_price_table()

    assert table["as_of"] == "2026-08-15"
    assert "claude-sonnet-4-6" in table["models"]


# ---------------------------------------------------------------------------
# Test 5 — Custom prices used in cost computation
# ---------------------------------------------------------------------------


def test_custom_prices_used_in_computation() -> None:
    # Sonnet at $6 input / $30 output
    # 1M fresh tokens, 0 cache, 100K output
    # expected = 1_000_000 × 6.0 / 1_000_000 + 100_000 × 30.0 / 1_000_000
    #          = 6.0 + 3.0 = 9.0
    turn = TurnDigest(
        turn_index=0,
        role="ai",
        tool_names=[],
        content_snippet="",
        token_count_input=1_000_000,
        token_count_output=100_000,
        cache_read=0,
        h2_duplicate=False,
        cache_creation=0,
        model="claude-sonnet-4-6",
    )
    tc = compute_turn_cost(turn, _CUSTOM_PRICES)
    assert tc.total_usd == pytest.approx(9.0, rel=1e-9)
