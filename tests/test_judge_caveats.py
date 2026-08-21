from __future__ import annotations

"""Tests: B3 DOV caveats present on both local judge and API judge verdict paths."""

from unittest.mock import MagicMock, patch

from tes.judge import (
    JUDGE_SETUP_HINT_FULL,
    ApiJudgeConfig,
    build_api_judge_consent_notice,
    score_trajectory_api,
)
from tes.score import TRAJECTORY_DOMAIN_OF_VALIDITY

# ---------------------------------------------------------------------------
# The B3 caveats that MUST appear in TRAJECTORY_DOMAIN_OF_VALIDITY
# ---------------------------------------------------------------------------


def test_trajectory_dov_contains_positive_corroborated():
    """DOV states positive signal is corroborated (B3 finding)."""
    assert "corroborated" in TRAJECTORY_DOMAIN_OF_VALIDITY.lower()


def test_trajectory_dov_contains_negative_model_dependent():
    """DOV states negative signal is model-dependent (B3 finding)."""
    assert "model-dependent" in TRAJECTORY_DOMAIN_OF_VALIDITY.lower()


def test_trajectory_dov_contains_no_human_calibration():
    """DOV states no human accuracy calibration (B3 limitation)."""
    assert "human" in TRAJECTORY_DOMAIN_OF_VALIDITY.lower()


# ---------------------------------------------------------------------------
# API judge consent notice contains B3 caveats
# ---------------------------------------------------------------------------


def test_consent_notice_contains_corroborated():
    """Consent notice includes the B3 positive-corroborated caveat."""
    notice = build_api_judge_consent_notice(
        "abc123", "infra-deploy", "claude-haiku-4-5-20251001", "ANTHROPIC_API_KEY env var"
    )
    assert "corroborated" in notice.lower()


def test_consent_notice_contains_model_dependent():
    """Consent notice includes the B3 negative-model-dependent caveat."""
    notice = build_api_judge_consent_notice(
        "abc123", "infra-deploy", "claude-haiku-4-5-20251001", "ANTHROPIC_API_KEY env var"
    )
    assert "model-dependent" in notice.lower()


def test_consent_notice_contains_no_tracegauge_server():
    """Consent notice explicitly states no tracegauge server involvement."""
    notice = build_api_judge_consent_notice(
        "abc123", "infra-deploy", "claude-haiku-4-5-20251001", "ANTHROPIC_API_KEY env var"
    )
    assert "no tracegauge server" in notice.lower()


def test_consent_notice_contains_send_warning():
    """Consent notice makes clear that data WILL BE SENT."""
    notice = build_api_judge_consent_notice(
        "abc123", "infra-deploy", "claude-haiku-4-5-20251001", "ANTHROPIC_API_KEY env var"
    )
    assert "send" in notice.lower()


def test_consent_notice_contains_api_availability_not_validity():
    """Consent notice clarifies that API path changes availability, not validity."""
    notice = build_api_judge_consent_notice(
        "abc123", "infra-deploy", "claude-haiku-4-5-20251001", "ANTHROPIC_API_KEY env var"
    )
    assert "availability" in notice.lower() or "available" in notice.lower()


# ---------------------------------------------------------------------------
# JUDGE_SETUP_HINT_FULL contains both setup paths
# ---------------------------------------------------------------------------


def test_setup_hint_full_mentions_ollama():
    """Full setup hint mentions Ollama local option."""
    assert "ollama" in JUDGE_SETUP_HINT_FULL.lower()


def test_setup_hint_full_mentions_api_option():
    """Full setup hint mentions the API key option."""
    assert "api" in JUDGE_SETUP_HINT_FULL.lower() or "--api-judge" in JUDGE_SETUP_HINT_FULL


def test_setup_hint_full_mentions_availability_not_validity():
    """Full setup hint notes token+waste run without judge (judge is enhancement)."""
    assert "token" in JUDGE_SETUP_HINT_FULL.lower()
    assert "waste" in JUDGE_SETUP_HINT_FULL.lower()


# ---------------------------------------------------------------------------
# API judge result carries same DOV context (integration check via mock)
# ---------------------------------------------------------------------------

_MINIMAL_RECORD = {
    "session_id": "test-caveats",
    "domain_id": "CC",
    "digest": {
        "session_id": "test-caveats",
        "domain": "CC",
        "resolved": True,
        "total_tokens": 200,
        "turn_count": 2,
        "h2_duplicate_count": 0,
        "cache_hit_rate": 0.5,
        "p25_token_ratio": 0.4,
        "output_tokens_available": True,
        "task_description": "Do a thing",
        "turns": [],
    },
}


def test_api_judge_verdict_is_compatible_with_score_session():
    """API judge_entry passes through score_session; result has API-specific DOV."""
    from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
    from tes.score import TRAJECTORY_DOMAIN_OF_VALIDITY, score_session

    config = ApiJudgeConfig(api_key="sk-key")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "content": [
            {
                "text": '{"verdict":"MUCH_BETTER","waste_categories":[],"confidence":0.9,"reasoning":"Good"}'
            }
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("tes.judge.httpx.post", return_value=mock_resp):
        judge_entry = score_trajectory_api(_MINIMAL_RECORD, config, consent_given=True)

    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    result = score_session(_MINIMAL_RECORD, baselines, judge_entry=judge_entry)

    assert result.judge_verdict == "MUCH_BETTER"
    # API-judge DOV must be DIFFERENT from the local judge DOV
    assert result.trajectory_domain_of_validity != TRAJECTORY_DOMAIN_OF_VALIDITY
    # Must still carry B3 caveats
    assert "corroborated" in result.trajectory_domain_of_validity.lower()
    assert "model-dependent" in result.trajectory_domain_of_validity.lower()
    # Must carry the API-specific extra caveat
    assert "not part of" in result.trajectory_domain_of_validity.lower()
    assert "indicative" in result.trajectory_domain_of_validity.lower()
    # Must name the API model
    assert "claude-haiku-4-5-20251001" in result.trajectory_domain_of_validity


def test_local_judge_verdict_uses_standard_dov():
    """Local judge (judge_path absent) still uses TRAJECTORY_DOMAIN_OF_VALIDITY."""
    from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
    from tes.score import TRAJECTORY_DOMAIN_OF_VALIDITY, score_session

    # A judge_entry without judge_path (local judge format)
    local_judge_entry = {
        "session_id": "test-caveats",
        "verdict": "BETTER",
        "judge_score": 0.75,
        "reasoning": "Mostly purposeful.",
        "confidence": 0.8,
    }

    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    result = score_session(_MINIMAL_RECORD, baselines, judge_entry=local_judge_entry)

    assert result.judge_verdict == "BETTER"
    assert result.trajectory_domain_of_validity == TRAJECTORY_DOMAIN_OF_VALIDITY
