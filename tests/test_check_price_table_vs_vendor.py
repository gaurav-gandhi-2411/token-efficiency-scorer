"""tests/test_check_price_table_vs_vendor.py — deterministic tests for
scripts/check_price_table_vs_vendor.py against a SAVED excerpt of Anthropic's real pricing.md
(tests/fixtures/pricing/anthropic_pricing_2026-10-09_excerpt.md, retrieved 2026-10-09) -- no live
network calls in CI, per this repo's test-suite convention.

The headline regression test reproduces the original miss: the table as of b261c4b (no
claude-opus-5-5 / claude-sonnet-5-5) checked against a page that lists them must FAIL. The
pre-v2 checker returned exit 0 on exactly that input.
"""

from __future__ import annotations

import copy
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.check_price_table_vs_vendor import (
    FetchError,
    _fetch,
    compare_table,
    main,
    model_key_from_display_name,
    parse_anthropic_markdown,
    parse_vendor_table,
)
from tes.cost import load_price_table

_FIXTURES = Path(__file__).parent / "fixtures" / "pricing"
_PAGE = _FIXTURES / "anthropic_pricing_2026-10-09_excerpt.md"
_OLD_TABLE = _FIXTURES / "prices_b261c4b.json"
_PAGE_TEXT = _PAGE.read_text(encoding="utf-8")
_BUNDLED = Path(__file__).resolve().parent.parent / "tes" / "data" / "prices.json"

_HEADER = (
    "| Model | Base input tokens | 5m cache writes | 1h cache writes "
    "| Cache hits and refreshes | Output tokens |\n| --- | --- | --- | --- | --- | --- |\n"
)


# --- parser ---


def test_parses_every_page_row_and_derives_keys() -> None:
    rows = parse_vendor_table(_PAGE_TEXT)
    assert len(rows) == 21
    by_key = {(r.model_key, r.tier): r for r in rows}
    assert by_key[("claude-opus-5-5", "base")].cache_read == 0.20
    assert by_key[("claude-sonnet-5-5", "base")].cache_read == 0.10
    assert by_key[("claude-fable-5-1", "base")].cache_read == 0.25
    assert by_key[("claude-fable-5", "base")].cache_read == 1.0
    assert by_key[("claude-opus-4-1", "base")].input == 15.0  # retired-link label stripped


def test_haiku_5_5_prompt_tiers_are_two_rows_with_thresholds() -> None:
    rows = [r for r in parse_vendor_table(_PAGE_TEXT) if r.model_key == "claude-haiku-5-5"]
    assert {(r.tier, r.threshold) for r in rows} == {("base", 100_000), ("long", 100_000)}
    long_row = next(r for r in rows if r.tier == "long")
    assert (long_row.input, long_row.output, long_row.cache_read) == (0.50, 2.50, 0.05)


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Claude Opus 5.5", "claude-opus-5-5"),
        ("Claude Opus 4", "claude-opus-4"),
        ("Claude Haiku 3.5", "claude-haiku-3-5"),
    ],
)
def test_model_key_derivation(name: str, key: str) -> None:
    assert model_key_from_display_name(name) == key


def test_output_column_is_not_the_cache_hit_column() -> None:
    # The original off-by-one: Cache hits read as Output ($0.50 vs $25 for Claude Opus 5).
    row = next(r for r in parse_vendor_table(_PAGE_TEXT) if r.model_key == "claude-opus-5")
    assert (row.output, row.cache_read) == (25.0, 0.50)


def test_columns_matched_by_name_not_position() -> None:
    md = (
        "## Model pricing\n\n"
        "| Model | Output tokens | Base input tokens | 5m cache writes | 1h cache writes "
        "| Cache hits and refreshes |\n| --- | --- | --- | --- | --- | --- |\n"
        "| Claude Test 1 | $9 / MTok | $1 / MTok | $1.25 / MTok | $2 / MTok | $0.10 / MTok |\n"
    )
    (row,) = parse_vendor_table(md)
    assert (row.input, row.output) == (1.0, 9.0)


def test_does_not_read_past_the_model_pricing_section() -> None:
    md = (
        _PAGE_TEXT
        + "\n## Next section\n\n"
        + _HEADER
        + "| Claude Phantom 9 | $999 / MTok | $1 / MTok | $1 / MTok | $1 / MTok | $1 / MTok |\n"
    )
    assert "claude-phantom-9" not in {r.model_key for r in parse_vendor_table(md)}


@pytest.mark.parametrize(
    "md",
    [
        "no pricing table here at all",
        "## Model pricing\n\nthe table moved\n",
        "## Model pricing\n\n| Model | Base input tokens |\n| --- | --- |\n| Claude X 1 | $1 / MTok |\n",
        # header ok, zero data rows
        "## Model pricing\n\n" + _HEADER,
        # unreadable price in a data row
        "## Model pricing\n\n"
        + _HEADER
        + "| Claude X 1 | contact sales | $1 / MTok | $1 / MTok | $1 / MTok | $1 / MTok |\n",
        # a prompt-tier label we do not understand
        "## Model pricing\n\n"
        + _HEADER
        + "| Claude X 1 (for prompts between 1 and 2 tokens) | $1 / MTok | $1 / MTok "
        "| $1 / MTok | $1 / MTok | $1 / MTok |\n",
    ],
)
def test_unparseable_page_fails_closed(md: str) -> None:
    with pytest.raises(FetchError):
        parse_vendor_table(md)


def test_legacy_wrapper_returns_empty_not_raise() -> None:
    assert parse_anthropic_markdown("no pricing table here at all") == {}
    assert parse_anthropic_markdown(_PAGE_TEXT)["Claude Opus 5.5"] == (4.0, 20.0)


def test_fetch_raises_fetch_error_not_a_silent_default() -> None:
    with (
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("unreachable")),
        pytest.raises(FetchError),
    ):
        _fetch("https://example.invalid/pricing")


# --- the comparison ---


def test_bundled_table_matches_the_saved_official_page() -> None:
    result = compare_table(load_price_table(_BUNDLED), parse_vendor_table(_PAGE_TEXT))
    assert result == ([], [], [])


def test_original_miss_is_now_caught(capsys: pytest.CaptureFixture[str]) -> None:
    """REGRESSION: b261c4b's table has no claude-opus-5-5 / claude-sonnet-5-5 and the page does.
    The pre-v2 checker printed 'OK: 13 price entries verified' and exited 0 for this input."""
    assert main(["--page", str(_PAGE), "--prices", str(_OLD_TABLE)]) == 1
    err = capsys.readouterr().err
    for key in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-haiku-5-5"):
        assert key in err
    assert "MISSING FROM prices.json" in err


def test_main_ok_on_bundled_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--page", str(_PAGE)]) == 0
    assert capsys.readouterr().out.startswith("OK: 21 page rows")


def test_cache_hit_disagreement_is_caught() -> None:
    # (b): Opus 5.5 cache hits at the old table-wide 0.1x ($0.40) instead of 0.05x ($0.20).
    table = copy.deepcopy(load_price_table(_BUNDLED))
    del table["models"]["claude-opus-5-5"]["cache_read_multiplier"]
    _, mismatches, _ = compare_table(table, parse_vendor_table(_PAGE_TEXT))
    assert mismatches == ["claude-opus-5-5: cache hit ours=$0.4 vs page=$0.2 per MTok"]


def test_cache_write_disagreement_is_caught() -> None:
    table = copy.deepcopy(load_price_table(_BUNDLED))
    table["cache_multipliers"]["write_1hr"] = 1.0
    _, mismatches, _ = compare_table(table, parse_vendor_table(_PAGE_TEXT))
    assert mismatches
    assert all("1h cache write" in m for m in mismatches)


def test_input_output_mismatch_is_caught() -> None:
    table = copy.deepcopy(load_price_table(_BUNDLED))
    table["models"]["claude-sonnet-5-5"]["output_usd_per_mtok"] = 15.0
    _, mismatches, _ = compare_table(table, parse_vendor_table(_PAGE_TEXT))
    assert any(m.startswith("claude-sonnet-5-5: output ours=$15") for m in mismatches)


def test_model_missing_from_table_is_caught_regardless_of_name_map() -> None:
    # (a): any page model, including a brand-new id nobody mapped by hand.
    md = _PAGE_TEXT.replace("Claude Opus 5.5 ", "Claude Opus 9.9 ", 1)
    missing, _, stale = compare_table(load_price_table(_BUNDLED), parse_vendor_table(md))
    assert any(m.startswith("claude-opus-9-9:") for m in missing)
    assert any(s.startswith("claude-opus-5-5:") for s in stale)  # (c) dropped from the page


def test_ignored_page_key_needs_an_explicit_reason_entry() -> None:
    md = _PAGE_TEXT.replace("Claude Opus 5.5 ", "Claude Opus 9.9 ", 1)
    rows = parse_vendor_table(md)
    missing, _, _ = compare_table(
        load_price_table(_BUNDLED), rows, ignored={"claude-opus-9-9": "test-only id"}
    )
    assert not any("claude-opus-9-9" in m for m in missing)


def test_long_context_tier_mismatch_and_absence_are_caught() -> None:
    rows = parse_vendor_table(_PAGE_TEXT)
    table = copy.deepcopy(load_price_table(_BUNDLED))
    table["models"]["claude-haiku-5-5"]["long_context"]["output_usd_per_mtok"] = 3.0
    _, mismatches, _ = compare_table(table, rows)
    assert any("claude-haiku-5-5 (long): output ours=$3" in m for m in mismatches)
    del table["models"]["claude-haiku-5-5"]["long_context"]
    missing, _, _ = compare_table(table, rows)
    assert any("no long_context block" in m for m in missing)


def test_retired_entry_still_checked_when_the_page_lists_it() -> None:
    table = copy.deepcopy(load_price_table(_BUNDLED))
    table["models"]["claude-haiku-3-5"]["input_usd_per_mtok"] = 9.0
    _, mismatches, _ = compare_table(table, parse_vendor_table(_PAGE_TEXT))
    assert any(m.startswith("claude-haiku-3-5: input ours=$9") for m in mismatches)


def test_retired_entry_absent_from_page_is_exempt() -> None:
    table = load_price_table(_BUNDLED)
    assert table["models"]["claude-3-opus"]["retired"]  # retired and not on the page
    missing, mismatches, stale = compare_table(table, parse_vendor_table(_PAGE_TEXT))
    assert not any("claude-3-opus" in x for x in missing + mismatches + stale)


def test_main_fails_closed_when_the_fetch_fails() -> None:
    with patch(
        "scripts.check_price_table_vs_vendor._fetch", side_effect=FetchError("simulated outage")
    ):
        assert main([]) == 1


def test_main_fails_closed_when_the_page_parses_to_nothing() -> None:
    with patch("scripts.check_price_table_vs_vendor._fetch", return_value="<html>moved</html>"):
        assert main([]) == 1


def test_main_fails_closed_on_missing_saved_page(tmp_path: Path) -> None:
    assert main(["--page", str(tmp_path / "nope.md")]) == 1
