"""tests/test_check_price_table_vs_vendor.py — GG1: deterministic tests for
scripts/check_price_table_vs_vendor.py against a recorded markdown fixture
(a small representative excerpt of what Anthropic's pricing.md endpoint
actually returned when fetched live this session, 2026-08-16) -- no live
network calls in CI, per this repo's existing test-suite convention.
"""

from __future__ import annotations

from unittest.mock import patch

from scripts.check_price_table_vs_vendor import (
    FetchError,
    main,
    parse_anthropic_markdown,
)

_ANTHROPIC_MD_FIXTURE = """---
title: Pricing
---

## Model pricing

The following table shows pricing for all Claude models:

| Model | Base Input Tokens | 5m Cache Writes | 1h Cache Writes | Cache Hits & Refreshes | Output Tokens |
| --- | --- | --- | --- | --- | --- |
| Claude Opus 5 | $5 / MTok | $6.25 / MTok | $10 / MTok | $0.50 / MTok | $25 / MTok |
| Claude Sonnet 5 | $2 / MTok | $2.50 / MTok | $4 / MTok | $0.20 / MTok | $10 / MTok |
| Claude Opus 4.1 ([retired, except on Bedrock and Google Cloud](https://example.invalid)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |

## Next section

Unrelated content that must not be read as part of the pricing table.

| Model | Some Other Column |
| --- | --- |
| Claude Opus 5 | $999 / MTok |
"""


def test_parses_expected_models_and_rates():
    result = parse_anthropic_markdown(_ANTHROPIC_MD_FIXTURE)
    assert result["Claude Opus 5"] == (5.0, 25.0)
    assert result["Claude Sonnet 5"] == (2.0, 10.0)


def test_strips_markdown_link_from_still_listed_model_name():
    result = parse_anthropic_markdown(_ANTHROPIC_MD_FIXTURE)
    assert "Claude Opus 4.1" in result
    assert result["Claude Opus 4.1"] == (15.0, 75.0)


def test_output_column_is_not_the_cache_hit_column():
    # Regression test for the exact off-by-one this parser's first version
    # (built for adk-tracegauge, ported here) had: cells[4] (Cache Hits &
    # Refreshes) read as Output instead of cells[5] (Output Tokens) --
    # $0.50 vs $25 for Claude Opus 5 would have been silently swapped.
    result = parse_anthropic_markdown(_ANTHROPIC_MD_FIXTURE)
    assert result["Claude Opus 5"][1] == 25.0
    assert result["Claude Opus 5"][1] != 0.50


def test_does_not_read_past_the_model_pricing_section():
    result = parse_anthropic_markdown(_ANTHROPIC_MD_FIXTURE)
    assert result["Claude Opus 5"][0] != 999.0


def test_missing_table_header_returns_empty_not_raise():
    assert parse_anthropic_markdown("no pricing table here at all") == {}


def test_fetch_raises_fetch_error_not_a_silent_default():
    import urllib.error

    from scripts.check_price_table_vs_vendor import _fetch

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("network unreachable"),
    ):
        try:
            _fetch("https://example.invalid/pricing")
            raise AssertionError("expected FetchError")
        except FetchError:
            pass


def test_main_returns_nonzero_when_vendor_fetch_fails():
    with patch(
        "scripts.check_price_table_vs_vendor._fetch",
        side_effect=FetchError("simulated outage"),
    ):
        assert main() == 1


def test_main_skips_retired_entries():
    with (
        patch(
            "scripts.check_price_table_vs_vendor._fetch",
            return_value=_ANTHROPIC_MD_FIXTURE,
        ),
        patch(
            "scripts.check_price_table_vs_vendor.load_price_table",
            return_value={
                "models": {
                    "claude-3-opus": {
                        "input_usd_per_mtok": 999.0,
                        "output_usd_per_mtok": 999.0,
                        "retired": True,
                    },
                }
            },
        ),
    ):
        # A wildly-wrong "retired" entry not mapped/checked must not fail
        # the run -- retired entries are exempt by design.
        assert main() == 0


def test_main_returns_zero_when_entry_matches():
    with (
        patch(
            "scripts.check_price_table_vs_vendor._fetch",
            return_value=_ANTHROPIC_MD_FIXTURE,
        ),
        patch(
            "scripts.check_price_table_vs_vendor.load_price_table",
            return_value={
                "models": {
                    "claude-opus-5": {
                        "input_usd_per_mtok": 5.0,
                        "output_usd_per_mtok": 25.0,
                    },
                }
            },
        ),
    ):
        assert main() == 0


def test_main_returns_nonzero_on_a_real_mismatch():
    with (
        patch(
            "scripts.check_price_table_vs_vendor._fetch",
            return_value=_ANTHROPIC_MD_FIXTURE,
        ),
        patch(
            "scripts.check_price_table_vs_vendor.load_price_table",
            return_value={
                "models": {
                    "claude-opus-5": {
                        "input_usd_per_mtok": 5.0,
                        "output_usd_per_mtok": 999.0,
                    },
                }
            },
        ),
    ):
        assert main() == 1
