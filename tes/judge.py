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
    JUDGE_OUTPUT_SCHEMA,
    JUDGE_SYSTEM_PROMPT,
    VERDICT_TO_FLOAT,
    _build_user_prompt,
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
    inference_timeout_s: float = 300.0  # covers cold load (~30-60s) + large-session prefill


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
    """Call the Ollama judge API; return judge_entry dict or None on any failure.

    Uses config.inference_timeout_s for the read timeout so a cold-loading
    model degrades to UNAVAILABLE (None) rather than hanging the CLI.
    """
    import json as _json

    scoring_rec = {**record, "domain_id": record.get("domain_id", "CC")}
    user_prompt = _build_user_prompt(scoring_rec)

    try:
        response_text = ""
        with httpx.stream(
            "POST",
            f"{config.endpoint}/api/chat",
            json={
                "model": config.model,
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": True,
                "format": JUDGE_OUTPUT_SCHEMA,
                "options": {"temperature": 0, "seed": 42, "num_ctx": 32768, "num_predict": 6144},
            },
            timeout=httpx.Timeout(
                connect=30.0, read=config.inference_timeout_s, write=30.0, pool=30.0
            ),
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                response_text += chunk.get("message", {}).get("content", "")
                if chunk.get("done"):
                    break
        raw = _json.loads(response_text)
    except httpx.ReadTimeout:
        print(
            f"  Judge timed out after {config.inference_timeout_s:.0f}s "
            f"— trajectory UNAVAILABLE",
            file=sys.stderr,
        )
        return None
    except httpx.HTTPError as exc:
        print(f"  Judge HTTP error: {exc}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  Judge error: {exc}", file=sys.stderr)
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
