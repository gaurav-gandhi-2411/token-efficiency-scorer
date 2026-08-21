from __future__ import annotations

"""tests/test_judge_tiering.py — Unit tests for tes/judge.py tiered availability logic.

All judge calls are mocked — no real Ollama instance required.
Tests cover: availability detection (true/false/prefix), clean UNAVAILABLE path,
API failure path, happy-path judge entry, and the integration of absent-judge
into score_session producing a valid ThreeAxisResult.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from unittest.mock import patch

from tes.judge import JudgeConfig, is_judge_available, score_trajectory

# ---------------------------------------------------------------------------
# Test 1 — connection refused / probe returns empty list
# ---------------------------------------------------------------------------


def test_is_judge_available_false_on_connection_error() -> None:
    """Ollama endpoint unreachable → is_judge_available returns False."""
    with patch("tes.judge._probe_ollama_tags", return_value=[]):
        result = is_judge_available(JudgeConfig())
    assert result is False


# ---------------------------------------------------------------------------
# Test 2 — Ollama up but wrong model installed
# ---------------------------------------------------------------------------


def test_is_judge_available_false_when_model_not_in_list() -> None:
    """Ollama reachable but target model absent → is_judge_available returns False."""
    with patch("tes.judge._probe_ollama_tags", return_value=["llama3:8b", "mistral:7b"]):
        result = is_judge_available(JudgeConfig())
    assert result is False


# ---------------------------------------------------------------------------
# Test 3 — exact match
# ---------------------------------------------------------------------------


def test_is_judge_available_true_when_model_present() -> None:
    """Ollama reachable and exact model name in tag list → returns True."""
    with patch("tes.judge._probe_ollama_tags", return_value=["qwen3:30b-a3b", "llama3:8b"]):
        result = is_judge_available(JudgeConfig())
    assert result is True


# ---------------------------------------------------------------------------
# Test 4 — prefix-tolerant match ("qwen3:30b-a3b:latest" counts)
# ---------------------------------------------------------------------------


def test_is_judge_available_true_when_model_has_tag_suffix() -> None:
    """Prefix-tolerant matching: 'qwen3:30b-a3b:latest' satisfies 'qwen3:30b-a3b'."""
    with patch("tes.judge._probe_ollama_tags", return_value=["qwen3:30b-a3b:latest"]):
        result = is_judge_available(JudgeConfig())
    assert result is True


# ---------------------------------------------------------------------------
# Test 5 — judge absent → score_trajectory returns None cleanly
# ---------------------------------------------------------------------------


def test_score_trajectory_returns_none_when_judge_absent() -> None:
    """When judge is unavailable, score_trajectory returns None without raising."""
    with patch("tes.judge.is_judge_available", return_value=False):
        result = score_trajectory({"session_id": "abc", "digest": {}}, JudgeConfig())
    assert result is None


# ---------------------------------------------------------------------------
# Test 6 — judge present but API call fails → None gracefully
# ---------------------------------------------------------------------------


def test_score_trajectory_returns_none_when_api_fails() -> None:
    """Judge available but _call_judge_api returns None → score_trajectory returns None."""
    with (
        patch("tes.judge.is_judge_available", return_value=True),
        patch("tes.judge._call_judge_api", return_value=None),
    ):
        result = score_trajectory({"session_id": "abc", "digest": {}}, JudgeConfig())
    assert result is None


# ---------------------------------------------------------------------------
# Test 7 — happy path: judge available + valid response
# ---------------------------------------------------------------------------


def test_score_trajectory_returns_judge_entry_when_available() -> None:
    """Judge available and returns valid entry → score_trajectory passes it through."""
    fake_entry = {
        "session_id": "test123",
        "verdict": "MUCH_BETTER",
        "judge_score": 1.0,
        "reasoning": "direct and purposeful",
        "confidence": 0.9,
    }
    with (
        patch("tes.judge.is_judge_available", return_value=True),
        patch("tes.judge._call_judge_api", return_value=fake_entry),
    ):
        result = score_trajectory({"session_id": "test123", "digest": {}}, JudgeConfig())

    assert result is not None
    assert result["verdict"] == "MUCH_BETTER"
    assert result["judge_score"] == 1.0
    assert "reasoning" in result


# ---------------------------------------------------------------------------
# Test 8 — integration: absent judge → valid ThreeAxisResult (not an error)
# ---------------------------------------------------------------------------


def test_score_trajectory_none_result_is_clean_not_error() -> None:
    """Absent judge produces a valid ThreeAxisResult: UNAVAILABLE is the normal output.

    Confirms: judge_verdict is None, band_verdict is not 'error',
    trajectory_domain_of_validity is populated even when judge is absent.
    """
    from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
    from tes.score import score_session

    # Load real baselines from the bundled path
    baselines = load_baselines(str(BUNDLED_BASELINES_PATH))

    # Load one real pool record
    pool_path = Path(__file__).resolve().parents[1] / "data" / "corpus_pool" / "pool_adapted.jsonl"
    with pool_path.open(encoding="utf-8") as fh:
        record = json.loads(fh.readline())

    # Absent judge → None
    with patch("tes.judge.is_judge_available", return_value=False):
        judge_entry = score_trajectory(record, JudgeConfig())
    assert judge_entry is None

    # score_session must handle None gracefully → still a valid ThreeAxisResult
    result = score_session(record, baselines, judge_entry=None, waste_entry=None)

    assert result.judge_verdict is None, "trajectory axis must be UNAVAILABLE (None)"
    assert result.band_verdict != "error", "token axis must still produce a valid verdict"
    assert result.trajectory_domain_of_validity != "", (
        "domain_of_validity must be populated even when trajectory is UNAVAILABLE"
    )
