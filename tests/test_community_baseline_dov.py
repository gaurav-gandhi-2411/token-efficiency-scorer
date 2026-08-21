from __future__ import annotations

"""Tests: community baseline always carries its domain-of-validity.

Covers (per spec.md's community-baseline requirement + the 0.9.0 build task):
  1. A computed result always has a non-empty domain_of_validity mentioning
     contributor count, self-selection, and content-free coarseness.
  2. A task_type below the minimum-contributor floor returns None — never a
     misleadingly-precise percentile from too few people.
  3. fetch_community_baseline() returns None (never raises) on a mocked
     network timeout, HTTP error, or malformed-JSON response.
"""

from unittest.mock import MagicMock, patch

import httpx
from tes.community_baseline import (
    MIN_CONTRIBUTORS,
    compute_community_baseline,
    fetch_community_baseline,
    score_against_community,
)


def _make_row(
    task_type: str,
    real_tokens: int,
    contributor_id: str | None,
) -> dict:
    """Build a minimal content-free contribution row (only the fields this module reads)."""
    return {
        "task_type": task_type,
        "real_tokens": real_tokens,
        "token_count_input": None,
        "token_count_output": None,
        "cache_creation": None,
        "cache_read": None,
        "waste_event_count": 0,
        "waste_detectors_fired": [],
        "model": "other",
        "turn_count": 50,
        "week_bucket": "2026-W23",
        "tracegauge_version": "0.9.0",
        "schema_version": "1",
        "contributor_id": contributor_id,
    }


def _pooled_rows_above_floor() -> list[dict]:
    """Rows for 'debug-fix' from MIN_CONTRIBUTORS distinct contributors, 2 sessions each."""
    rows: list[dict] = []
    base = 100_000
    for i in range(MIN_CONTRIBUTORS):
        cid = f"contributor-{i}"
        rows.append(_make_row("debug-fix", base + i * 10_000, cid))
        rows.append(_make_row("debug-fix", base + i * 10_000 + 5_000, cid))
    return rows


def _pooled_rows_below_floor() -> list[dict]:
    """Rows for 'ml-eval' from only 2 distinct contributors (below MIN_CONTRIBUTORS)."""
    rows: list[dict] = []
    for i in range(2):
        cid = f"solo-contributor-{i}"
        for _ in range(30):  # many sessions, but from too few people
            rows.append(_make_row("ml-eval", 500_000, cid))
    return rows


# ---------------------------------------------------------------------------
# 1. Computed result always carries a DOV mentioning the three required caveats
# ---------------------------------------------------------------------------


def test_score_against_community_carries_full_dov():
    """A scoreable task_type returns a result whose DOV mentions all three caveats."""
    rows = _pooled_rows_above_floor()
    baseline = compute_community_baseline(rows)

    result = score_against_community(105_000, "debug-fix", baseline)

    assert result is not None
    dov = result["domain_of_validity"]
    assert isinstance(dov, str)
    assert dov.strip() != ""
    # (a) contributor count
    assert str(MIN_CONTRIBUTORS) in dov
    # (b) self-selection bias
    assert "self-selection" in dov.lower() or "self selection" in dov.lower()
    assert "opted in" in dov.lower() or "opt-in" in dov.lower() or "opt in" in dov.lower()
    # (c) content-free coarseness
    assert "content-free" in dov.lower()
    assert "task complexity" in dov.lower() or "model choice" in dov.lower()


def test_dov_present_across_percentile_range():
    """DOV is populated regardless of where real_tokens falls in the distribution."""
    rows = _pooled_rows_above_floor()
    baseline = compute_community_baseline(rows)

    for probe_tokens in (1, 100_000, 500_000, 10_000_000):
        result = score_against_community(probe_tokens, "debug-fix", baseline)
        assert result is not None
        assert result["domain_of_validity"]
        assert 0 <= result["percentile"] <= 100


def test_result_shape_has_no_bare_percentile_path():
    """Every field expected on a result dict is present alongside the percentile —
    there is no code path that returns a percentile number without its DOV."""
    rows = _pooled_rows_above_floor()
    baseline = compute_community_baseline(rows)
    result = score_against_community(105_000, "debug-fix", baseline)
    assert result is not None
    for key in (
        "task_type",
        "real_tokens",
        "percentile",
        "percentile_label",
        "contributor_count",
        "session_count",
        "domain_of_validity",
    ):
        assert key in result


# ---------------------------------------------------------------------------
# 2. Below-floor task_type returns None, not a misleadingly-precise percentile
# ---------------------------------------------------------------------------


def test_below_contributor_floor_returns_none():
    """A task_type with fewer than MIN_CONTRIBUTORS distinct contributors is unscoreable."""
    rows = _pooled_rows_below_floor()
    baseline = compute_community_baseline(rows)

    assert baseline["types"]["ml-eval"]["available"] is False
    assert baseline["types"]["ml-eval"]["contributor_count"] == 2

    result = score_against_community(500_000, "ml-eval", baseline)
    assert result is None


def test_missing_task_type_returns_none():
    """A task_type absent from the community baseline entirely returns None."""
    rows = _pooled_rows_above_floor()
    baseline = compute_community_baseline(rows)
    result = score_against_community(100_000, "research-recon", baseline)
    assert result is None


def test_exactly_at_floor_is_scoreable():
    """MIN_CONTRIBUTORS distinct contributors (the floor itself) IS scoreable."""
    rows = _pooled_rows_above_floor()
    baseline = compute_community_baseline(rows)
    assert baseline["types"]["debug-fix"]["contributor_count"] == MIN_CONTRIBUTORS
    assert baseline["types"]["debug-fix"]["available"] is True
    result = score_against_community(105_000, "debug-fix", baseline)
    assert result is not None


def test_one_below_floor_returns_none():
    """MIN_CONTRIBUTORS - 1 distinct contributors is NOT scoreable."""
    rows: list[dict] = []
    for i in range(MIN_CONTRIBUTORS - 1):
        rows.append(_make_row("infra-deploy", 200_000 + i * 1_000, f"c-{i}"))
    baseline = compute_community_baseline(rows)
    assert baseline["types"]["infra-deploy"]["available"] is False
    assert score_against_community(200_000, "infra-deploy", baseline) is None


# ---------------------------------------------------------------------------
# 3. fetch_community_baseline never raises — graceful None on any failure
# ---------------------------------------------------------------------------


def test_fetch_returns_none_on_timeout():
    """A network timeout degrades to None, never raises."""
    with patch(
        "tes.community_baseline.httpx.get",
        side_effect=httpx.ConnectTimeout("timed out"),
    ):
        result = fetch_community_baseline("https://example.invalid/community_baseline.json")
    assert result is None


def test_fetch_returns_none_on_connect_error():
    """A network/connection error degrades to None, never raises."""
    with patch(
        "tes.community_baseline.httpx.get",
        side_effect=httpx.ConnectError("refused"),
    ):
        result = fetch_community_baseline("https://example.invalid/community_baseline.json")
    assert result is None


def test_fetch_returns_none_on_http_error_status():
    """A non-2xx HTTP status degrades to None, never raises."""
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock(status_code=404)
    )
    with patch("tes.community_baseline.httpx.get", return_value=mock_response):
        result = fetch_community_baseline("https://example.invalid/community_baseline.json")
    assert result is None


def test_fetch_returns_none_on_malformed_json():
    """A response whose body is not valid JSON degrades to None, never raises."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.side_effect = ValueError("not JSON")
    with patch("tes.community_baseline.httpx.get", return_value=mock_response):
        result = fetch_community_baseline("https://example.invalid/community_baseline.json")
    assert result is None


def test_fetch_returns_none_on_non_dict_json():
    """A response that parses to valid JSON but isn't an object degrades to None."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = [1, 2, 3]
    with patch("tes.community_baseline.httpx.get", return_value=mock_response):
        result = fetch_community_baseline("https://example.invalid/community_baseline.json")
    assert result is None


def test_fetch_success_returns_parsed_dict():
    """A clean 200 with valid JSON body returns the parsed dict."""
    baseline = compute_community_baseline(_pooled_rows_above_floor())
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = baseline
    with patch("tes.community_baseline.httpx.get", return_value=mock_response) as mock_get:
        result = fetch_community_baseline(
            "https://example.invalid/community_baseline.json", timeout_s=5.0
        )
    mock_get.assert_called_once()
    assert result == baseline


# ---------------------------------------------------------------------------
# Extras: pure-function batch computation sanity
# ---------------------------------------------------------------------------


def test_compute_community_baseline_is_pure_no_io(tmp_path):
    """compute_community_baseline takes rows in, dict out — no filesystem/network touched."""
    rows = _pooled_rows_above_floor()
    baseline = compute_community_baseline(rows)
    assert baseline["schema_version"]
    assert baseline["token_measure"]
    assert baseline["min_contributors"] == MIN_CONTRIBUTORS
    assert "debug-fix" in baseline["types"]


def test_compute_community_baseline_empty_rows():
    """Empty input produces an empty (but well-formed) baseline dict."""
    baseline = compute_community_baseline([])
    assert baseline["types"] == {}


def test_compute_community_baseline_skips_rows_without_task_type():
    """Rows with missing/empty task_type are skipped rather than crashing."""
    rows = [{"task_type": None, "real_tokens": 1000, "contributor_id": "x"}]
    baseline = compute_community_baseline(rows)
    assert baseline["types"] == {}
