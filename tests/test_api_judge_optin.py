from __future__ import annotations

"""Tests: API judge is opt-in, consent blocks all egress, no key → graceful None."""

from unittest.mock import MagicMock, patch

import pytest

from tes.judge import ApiJudgeConfig, score_trajectory_api


_MINIMAL_RECORD = {
    "session_id": "test-session-optin",
    "domain_id": "CC",
    "digest": {
        "session_id": "test-session-optin",
        "domain": "CC",
        "resolved": True,
        "total_tokens": 1000,
        "turn_count": 5,
        "h2_duplicate_count": 0,
        "cache_hit_rate": 0.8,
        "p25_token_ratio": 0.5,
        "output_tokens_available": True,
        "task_description": "Fix a bug",
        "turns": [
            {
                "turn_index": 0,
                "role": "user",
                "tool_names": [],
                "content_snippet": "Fix this",
                "token_count_input": 100,
                "token_count_output": 0,
                "cache_read": 0,
                "h2_duplicate": False,
                "cache_creation": 0,
                "model": "",
            }
        ],
    },
}


def test_api_judge_off_by_default():
    """score_trajectory_api returns None when consent_given=False (no network call)."""
    config = ApiJudgeConfig(api_key="sk-test-key")
    with patch("tes.judge.httpx.post") as mock_post:
        result = score_trajectory_api(_MINIMAL_RECORD, config, consent_given=False)
    assert result is None
    mock_post.assert_not_called()


def test_consent_gate_blocks_network_call():
    """httpx.post is NEVER called when consent_given=False, regardless of key validity."""
    config = ApiJudgeConfig(api_key="sk-any-key")
    with patch("tes.judge.httpx.post") as mock_post:
        score_trajectory_api(_MINIMAL_RECORD, config, consent_given=False)
    mock_post.assert_not_called()


def test_no_key_returns_none_no_network():
    """Empty api_key returns None without any network call, even with consent."""
    config = ApiJudgeConfig(api_key="")
    with patch("tes.judge.httpx.post") as mock_post:
        result = score_trajectory_api(_MINIMAL_RECORD, config, consent_given=True)
    assert result is None
    mock_post.assert_not_called()


def test_consent_given_true_attempts_call():
    """With consent_given=True and a key, the API is called (mock returns a valid response)."""
    config = ApiJudgeConfig(api_key="sk-valid-key")
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "content": [{"text": '{"verdict":"MUCH_BETTER","waste_categories":[],"confidence":0.9,"reasoning":"Good session."}'}]
    }
    mock_response.raise_for_status = MagicMock()
    with patch("tes.judge.httpx.post", return_value=mock_response) as mock_post:
        result = score_trajectory_api(_MINIMAL_RECORD, config, consent_given=True)
    mock_post.assert_called_once()
    assert result is not None
    assert result["verdict"] == "MUCH_BETTER"


def test_consent_false_returns_none_not_error():
    """consent_given=False returns None (not raises), so callers can handle gracefully."""
    config = ApiJudgeConfig(api_key="sk-key")
    result = score_trajectory_api(_MINIMAL_RECORD, config, consent_given=False)
    assert result is None


def test_api_call_failure_returns_none():
    """Network error returns None — UNAVAILABLE, not an exception propagated to caller."""
    import httpx as _httpx
    config = ApiJudgeConfig(api_key="sk-key")
    with patch("tes.judge.httpx.post", side_effect=_httpx.ConnectError("refused")):
        result = score_trajectory_api(_MINIMAL_RECORD, config, consent_given=True)
    assert result is None


def test_consent_given_false_even_with_valid_key_no_egress():
    """Regression: verify consent=False blocks even when key looks valid."""
    config = ApiJudgeConfig(api_key="sk-ant-real-looking-key-123")
    call_count = {"n": 0}

    def counting_post(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("httpx.post should never be called without consent")

    with patch("tes.judge.httpx.post", side_effect=counting_post):
        result = score_trajectory_api(_MINIMAL_RECORD, config, consent_given=False)

    assert result is None
    assert call_count["n"] == 0
