from __future__ import annotations

"""tests/test_caveats_present.py — Verify domain-of-validity appears in formatted output.

The CLI's honesty guarantee: each axis must carry its domain-of-validity in
formatted output (both human and JSON), and UNAVAILABLE must be explicit, never silent.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from tes.report import format_human, format_json
from tes.score import (
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
    ThreeAxisResult,
)

# Minimal ThreeAxisResult for formatting tests — covers the judge-absent case
_SAMPLE_RESULT = ThreeAxisResult(
    session_id="test-session-0000",
    task_type="infra-deploy",
    real_tokens=482_391,
    scope_status="out_of_scope",
    baseline_available=True,
    p25=None,
    p75=None,
    median=None,
    band_verdict="unavailable",
    interpretation="Session scope too small for a token-economy reference.",
    token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
    baseline_source="b2_corpus",
    judge_verdict=None,
    judge_score=None,
    judge_reasoning=None,
    trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
    waste_event_count=0,
    waste_events=[],
    waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
)


def test_token_domain_present_in_human_output() -> None:
    output = format_human(_SAMPLE_RESULT)
    # Key fragment from TOKEN_DOMAIN_OF_VALIDITY
    assert "high-waste infra/ML-ops corpus" in output


def test_trajectory_domain_present_in_human_output() -> None:
    output = format_human(_SAMPLE_RESULT)
    # Key fragment from TRAJECTORY_DOMAIN_OF_VALIDITY
    assert "cross-model corroborated" in output


def test_waste_domain_present_in_human_output() -> None:
    output = format_human(_SAMPLE_RESULT)
    # Key fragment from WASTE_DOMAIN_OF_VALIDITY
    assert "Observable-invariant waste only" in output


def test_trajectory_unavailable_is_explicit() -> None:
    output = format_human(_SAMPLE_RESULT)
    assert "UNAVAILABLE" in output


def test_three_sections_always_present() -> None:
    output = format_human(_SAMPLE_RESULT)
    assert "TOKEN ECONOMY" in output
    assert "TRAJECTORY QUALITY" in output
    assert "DETERMINISTIC WASTE" in output


def test_no_composite_score() -> None:
    output = format_human(_SAMPLE_RESULT)
    # Should never see a single blended score
    assert "efficiency score:" not in output.lower()
    assert "composite" not in output.lower()


def test_json_includes_all_domain_strings() -> None:
    output = format_json(_SAMPLE_RESULT)
    data = json.loads(output)
    assert "token_domain_of_validity" in data
    assert "trajectory_domain_of_validity" in data
    assert "waste_domain_of_validity" in data
    # Caveats travel with the data, not just the pretty-print
    assert "high-waste infra" in data["token_domain_of_validity"]
    assert "cross-model corroborated" in data["trajectory_domain_of_validity"]
    assert "Observable-invariant" in data["waste_domain_of_validity"]


def test_json_includes_proof_turns_when_present() -> None:
    result_with_waste = ThreeAxisResult(
        session_id="test-waste",
        task_type="debug-fix",
        real_tokens=500_000,
        scope_status="in_scope",
        baseline_available=True,
        p25=353_000,
        p75=654_000,
        median=524_000,
        band_verdict="within_band",
        interpretation="Within band.",
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        baseline_source="b2_corpus",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning=None,
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        waste_event_count=1,
        waste_events=[{
            "detector": "REPEATED-FAILED-RETRY",
            "session_id": "test-waste",
            "turns": [14, 15, 18, 19],
            "repeat_count": 2,
            "evidence": {"error_snippet": "Exit code 1\nERROR: something", "gap": 0},
        }],
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
    )
    output = format_json(result_with_waste)
    data = json.loads(output)
    assert len(data["waste_events"]) == 1
    assert data["waste_events"][0]["turns"] == [14, 15, 18, 19]
