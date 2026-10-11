"""Tests for scripts/check_prices_vs_litellm.py (optional, non-fatal LiteLLM cross-check).

The LiteLLM data below is a hand-written miniature in LiteLLM's real shape (per-token USD,
``litellm_provider``, bare first-party ids); nothing from the MIT file is vendored.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.check_prices_vs_litellm import crosscheck, first_party_claude_entries, main
from tes.cost import load_price_table

_BUNDLED = Path(__file__).resolve().parent.parent / "tes" / "data" / "prices.json"
_OLD_TABLE = Path(__file__).parent / "fixtures" / "pricing" / "prices_b261c4b.json"


def _opus_5_5(**over: float) -> dict[str, object]:
    base = {
        "litellm_provider": "anthropic",
        "input_cost_per_token": 4e-06,
        "output_cost_per_token": 2e-05,
        "cache_creation_input_token_cost": 5e-06,
        "cache_creation_input_token_cost_above_1hr": 8e-06,
        "cache_read_input_token_cost": 2e-07,
    }
    return {**base, **over}


def test_matching_entry_has_no_warnings() -> None:
    assert crosscheck(load_price_table(_BUNDLED), {"claude-opus-5-5": _opus_5_5()}) == []


def test_cache_read_disagreement_is_a_warning() -> None:
    litellm = {"claude-opus-5-5": _opus_5_5(cache_read_input_token_cost=4e-07)}
    (w,) = crosscheck(load_price_table(_BUNDLED), litellm)
    assert "cache_read_input_token_cost ours=$0.2 vs LiteLLM=$0.4" in w


def test_first_party_key_missing_from_table_is_a_warning() -> None:
    # Reproduces the original miss: b261c4b's table lacks claude-opus-5-5, LiteLLM has it.
    (w,) = crosscheck(load_price_table(_OLD_TABLE), {"claude-opus-5-5": _opus_5_5()})
    assert "claude-opus-5-5" in w and "no 'claude-opus-5-5'" in w


def test_date_suffixed_key_maps_to_dateless_table_key() -> None:
    litellm = {
        "claude-haiku-4-5-20251001": {
            "litellm_provider": "anthropic",
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 5e-06,
        }
    }
    assert crosscheck(load_price_table(_BUNDLED), litellm) == []


def test_other_providers_and_prefixed_keys_are_ignored() -> None:
    litellm = {
        "anthropic.claude-opus-9": {"litellm_provider": "bedrock_converse"},
        "azure_ai/claude-opus-9": {"litellm_provider": "azure_ai"},
        "bedrock/claude-opus-9": {"litellm_provider": "anthropic"},
        "claude-via-bedrock": {"litellm_provider": "bedrock"},
        "sample_spec": {"litellm_provider": "anthropic"},
        "claude-opus-5-5": _opus_5_5(),
    }
    assert set(first_party_claude_entries(litellm)) == {"claude-opus-5-5"}


def test_findings_are_non_fatal_unless_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "litellm.json"
    path.write_text(json.dumps({"claude-brand-new-1": _opus_5_5()}), encoding="utf-8")
    assert main(["--litellm", str(path)]) == 0
    assert "claude-brand-new-1" in capsys.readouterr().err
    assert main(["--litellm", str(path), "--strict"]) == 1


def test_unusable_file_is_reported_not_passed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "litellm.json"
    path.write_text(json.dumps({"gpt-4o": {"litellm_provider": "openai"}}), encoding="utf-8")
    assert main(["--litellm", str(path)]) == 0
    assert "NOT RUN" in capsys.readouterr().err
    assert main(["--litellm", str(path), "--strict"]) == 1
