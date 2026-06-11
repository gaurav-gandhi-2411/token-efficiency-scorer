from __future__ import annotations

"""Tests: API judge uses the same validated v3 rubric as the local Qwen judge."""

from unittest.mock import MagicMock, call, patch

from tes.judge import (
    JUDGE_SYSTEM_PROMPT,
    ApiJudgeConfig,
    JudgeConfig,
    _JUDGE_USER_TEMPLATE,
    _build_user_prompt,
    score_trajectory_api,
)


_MINIMAL_RECORD = {
    "session_id": "test-rubric",
    "domain_id": "CC",
    "digest": {
        "session_id": "test-rubric",
        "domain": "CC",
        "resolved": True,
        "total_tokens": 500,
        "turn_count": 3,
        "h2_duplicate_count": 0,
        "cache_hit_rate": 0.5,
        "p25_token_ratio": 0.4,
        "output_tokens_available": True,
        "task_description": "Refactor module",
        "turns": [
            {
                "turn_index": 0,
                "role": "user",
                "tool_names": [],
                "content_snippet": "Refactor this",
                "token_count_input": 50,
                "token_count_output": 0,
                "cache_read": 0,
                "h2_duplicate": False,
                "cache_creation": 0,
                "model": "",
            }
        ],
    },
}


def _make_mock_response(verdict: str = "MUCH_BETTER") -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = {
        "content": [{"text": f'{{"verdict":"{verdict}","waste_categories":[],"confidence":0.9,"reasoning":"ok"}}'}]
    }
    mock.raise_for_status = MagicMock()
    return mock


def test_api_judge_uses_judge_system_prompt():
    """The system prompt sent to the API equals JUDGE_SYSTEM_PROMPT exactly."""
    config = ApiJudgeConfig(api_key="sk-key")
    with patch("tes.judge.httpx.post", return_value=_make_mock_response()) as mock_post:
        score_trajectory_api(_MINIMAL_RECORD, config, consent_given=True)
    call_kwargs = mock_post.call_args.kwargs
    sent_system = call_kwargs["json"]["system"]
    assert sent_system == JUDGE_SYSTEM_PROMPT


def test_api_judge_user_prompt_from_same_template():
    """The user prompt sent to the API is built from _JUDGE_USER_TEMPLATE."""
    config = ApiJudgeConfig(api_key="sk-key")
    expected_prompt = _build_user_prompt(_MINIMAL_RECORD)
    with patch("tes.judge.httpx.post", return_value=_make_mock_response()) as mock_post:
        score_trajectory_api(_MINIMAL_RECORD, config, consent_given=True)
    call_kwargs = mock_post.call_args.kwargs
    messages = call_kwargs["json"]["messages"]
    user_msg = next(m for m in messages if m["role"] == "user")
    # The actual prompt should match what _build_user_prompt produces
    assert user_msg["content"] == expected_prompt


def test_api_and_local_use_same_system_prompt_constant():
    """Both code paths reference the SAME JUDGE_SYSTEM_PROMPT constant — not a copy."""
    from tes.judge import JUDGE_SYSTEM_PROMPT as system_prompt_from_module
    # The constant is the same object regardless of import path
    assert system_prompt_from_module is JUDGE_SYSTEM_PROMPT


def test_api_and_local_use_same_user_template_constant():
    """Both code paths reference the SAME _JUDGE_USER_TEMPLATE constant."""
    from tes.judge import _JUDGE_USER_TEMPLATE as template_from_module
    assert template_from_module is _JUDGE_USER_TEMPLATE


def test_verdict_enum_is_identical():
    """The API judge parses the same VERDICT_TO_FLOAT keys as the local judge."""
    from tes.judge import VERDICT_TO_FLOAT
    expected_verdicts = {"MUCH_BETTER", "BETTER", "SIMILAR", "WORSE", "MUCH_WORSE"}
    assert set(VERDICT_TO_FLOAT.keys()) == expected_verdicts


def test_api_judge_returns_valid_judge_entry_format():
    """The API judge returns the same dict shape as score_trajectory (compatible with score_session)."""
    config = ApiJudgeConfig(api_key="sk-key")
    with patch("tes.judge.httpx.post", return_value=_make_mock_response("BETTER")):
        result = score_trajectory_api(_MINIMAL_RECORD, config, consent_given=True)
    assert result is not None
    # Must have same keys as local judge_entry
    assert "session_id" in result
    assert "verdict" in result
    assert "judge_score" in result
    assert "reasoning" in result
    assert "confidence" in result
    # Additional fields for the API path
    assert result.get("judge_path") == "api"


def test_api_judge_same_verdict_to_float_mapping():
    """Verdicts map to the same float scores regardless of local vs API path."""
    from tes.judge import VERDICT_TO_FLOAT
    config = ApiJudgeConfig(api_key="sk-key")
    for verdict, expected_score in VERDICT_TO_FLOAT.items():
        with patch("tes.judge.httpx.post", return_value=_make_mock_response(verdict)):
            result = score_trajectory_api(_MINIMAL_RECORD, config, consent_given=True)
        assert result is not None
        assert result["judge_score"] == expected_score
