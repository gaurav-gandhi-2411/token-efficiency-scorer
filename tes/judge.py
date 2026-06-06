from __future__ import annotations

"""tes/judge.py — Tiered trajectory-quality judge with Ollama availability detection.

The judge is OPTIONAL and requires ~18GB VRAM (Qwen3 30B). For most users,
trajectory axis will be UNAVAILABLE — that is the expected, normal state.
Two axes (token + waste) with UNAVAILABLE trajectory is a complete result,
not a degraded one.

Public API:
    JudgeConfig       — model, endpoint, probe_timeout_s
    JUDGE_SETUP_HINT  — user-facing message when judge is absent
    is_judge_available(config) -> bool
    score_trajectory(record, config) -> dict | None
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from layer2_judge import (  # noqa: E402
    VERDICT_TO_FLOAT,
    _build_user_prompt,
    _call_ollama,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class JudgeConfig:
    """Configuration for the local Ollama judge.

    Defaults to the validated Qwen3 configuration from the credibility arc.
    """

    model: str = "qwen3:30b-a3b"
    endpoint: str = "http://localhost:11434"
    probe_timeout_s: float = 3.0   # fast probe — fail quickly when Ollama is absent


# User-facing hint when judge is unavailable.
JUDGE_SETUP_HINT: str = (
    "Trajectory quality requires a local judge model (~18GB VRAM). "
    "To enable: install Ollama (https://ollama.ai), then run: "
    "ollama pull qwen3:30b-a3b. "
    "Without the judge, token and waste axes still run fully."
)


# ---------------------------------------------------------------------------
# Availability detection
# ---------------------------------------------------------------------------


def _probe_ollama_tags(endpoint: str, timeout: float) -> list[str]:
    """Return installed model names from Ollama /api/tags, or [] on any error.

    Separate function so tests can mock it without touching httpx internals.
    """
    try:
        resp = httpx.get(f"{endpoint}/api/tags", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def is_judge_available(config: JudgeConfig | None = None) -> bool:
    """Return True if Ollama is reachable and the configured model is installed.

    Checks: (1) Ollama endpoint responds, (2) configured model is in the tag list.
    Model name matching is prefix-tolerant: "qwen3:30b-a3b" matches
    "qwen3:30b-a3b" and "qwen3:30b-a3b:latest".
    """
    if config is None:
        config = JudgeConfig()
    tags = _probe_ollama_tags(config.endpoint, config.probe_timeout_s)
    return any(tag == config.model or tag.startswith(f"{config.model}:") for tag in tags)


# ---------------------------------------------------------------------------
# Judge call (mockable wrapper)
# ---------------------------------------------------------------------------


def _call_judge_api(record: dict[str, Any], config: JudgeConfig) -> dict[str, Any] | None:
    """Call the Ollama judge API and return a judge_entry dict, or None on failure.

    Separate function so tests can mock at the API boundary without patching httpx.
    Wraps layer2_judge._build_user_prompt + _call_ollama with the validated v3 prompt.
    """
    # layer2_judge._build_user_prompt requires rec["domain_id"]; default for CC-adapted records.
    scoring_rec = {**record, "domain_id": record.get("domain_id", "CC")}

    user_prompt = _build_user_prompt(scoring_rec)
    raw = _call_ollama(user_prompt, config.endpoint, config.model)
    if raw is None:
        return None

    verdict = str(raw.get("verdict", "")).upper().strip()
    if verdict not in VERDICT_TO_FLOAT:
        return None

    raw_conf = raw.get("confidence", 0.5)
    try:
        confidence = float(raw_conf)
    except (TypeError, ValueError):
        confidence = 0.5

    return {
        "session_id": record.get("session_id", ""),
        "verdict": verdict,
        "judge_score": VERDICT_TO_FLOAT[verdict],
        "reasoning": str(raw.get("reasoning", "")),
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# Public scoring function
# ---------------------------------------------------------------------------


def score_trajectory(
    record: dict[str, Any],
    config: JudgeConfig | None = None,
) -> dict[str, Any] | None:
    """Run the v3 trajectory judge on an adapted session record.

    Returns a judge_entry dict suitable for passing to tes.score.score_session,
    or None if the judge is not available or scoring fails.

    DESIGN: None is the EXPECTED return value for most users (no local GPU).
    Pass the result directly to score_session(judge_entry=result) — score_session
    handles None gracefully by leaving trajectory axis UNAVAILABLE.

    Parameters
    ----------
    record:
        Adapted session record from claudecode_adapter.adapt_session or pool_adapted.jsonl.
    config:
        Judge configuration. Defaults to JudgeConfig() (local Qwen3 at localhost:11434).

    Returns
    -------
    dict or None
        judge_entry dict with keys: session_id, verdict, judge_score, reasoning, confidence
        OR None if judge is not available or scoring fails.
    """
    if config is None:
        config = JudgeConfig()
    if not is_judge_available(config):
        return None
    return _call_judge_api(record, config)


__all__ = [
    "JudgeConfig",
    "JUDGE_SETUP_HINT",
    "is_judge_available",
    "score_trajectory",
]
