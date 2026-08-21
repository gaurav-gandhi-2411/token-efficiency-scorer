from __future__ import annotations

"""tes/judge.py — Tiered trajectory-quality judge with Ollama availability detection.

Self-contained implementation (no scripts/ or src/ import) so the installed wheel
works without repo access.

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

import os
import sys
from dataclasses import dataclass
from typing import Any

import httpx

from tes._digest import SessionDigest, TurnDigest, digest_to_text, reconstruct_digest  # noqa: F401

# ---------------------------------------------------------------------------
# Judge constants (inlined from scripts/layer2_judge.py)
# ---------------------------------------------------------------------------

JUDGE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["MUCH_BETTER", "BETTER", "SIMILAR", "WORSE", "MUCH_WORSE"],
        },
        "waste_categories": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "redundant_read",
                    "failed_retry",
                    "context_bloat",
                    "trajectory_drift",
                    "duplicate_output",
                ],
            },
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "waste_categories", "confidence", "reasoning"],
}

VERDICT_TO_FLOAT: dict[str, float] = {
    "MUCH_BETTER": 1.00,
    "BETTER": 0.75,
    "SIMILAR": 0.50,
    "WORSE": 0.25,
    "MUCH_WORSE": 0.00,
}

JUDGE_SYSTEM_PROMPT = """\
You are a trajectory quality judge for AI coding agent sessions.
Your sole job: assess how purposefully and directly the agent navigated the task.
Do NOT assess token efficiency, task success, or code quality.
Rate TRAJECTORY BEHAVIOR ONLY.
Respond with ONLY valid JSON — no text outside the JSON.
"""

_JUDGE_USER_TEMPLATE = """\
/no_think
TASK: {task_description}

DOMAIN: {domain}

TRAJECTORY:
{digest_text}

EVALUATION CRITERIA (apply all four in this fixed order):
C1. Turn purposefulness: does each turn advance task state, or is it exploratory/redundant?
C2. Trajectory coherence: does the agent avoid unanchored backtracking and exact retries of \
failed commands?
C3. Tool utilization: are tool results integrated into the next action, or ignored/repeated?
C4. Context discipline: does the agent avoid unnecessary re-reads and duplicate outputs?

Rate the PURPOSEFULNESS of the agent's trajectory — how directly and coherently it worked \
toward the goal, regardless of how many tokens were used or whether the task succeeded.

  MUCH_BETTER — very purposeful: direct path, no dead ends, tool results drive next steps
  BETTER       — mostly purposeful, minor redundancy or exploration
  SIMILAR      — some backtracking or redundancy but overall coherent
  WORSE        — unfocused: repeated failures, poor tool integration, backtracking
  MUCH_WORSE   — very unfocused: flailing, redundant loops, dead-end exploration

Respond with ONLY valid JSON:
{{
  "verdict": "<MUCH_BETTER|BETTER|SIMILAR|WORSE|MUCH_WORSE>",
  "waste_categories": ["<subset of: redundant_read, failed_retry, context_bloat, \
trajectory_drift, duplicate_output>"],
  "confidence": <0.0 to 1.0; use < 0.5 for ambiguous sessions>,
  "reasoning": "<1-2 sentences citing specific turn numbers or behavioral patterns observed>"
}}
"""


def _build_user_prompt(rec: dict[str, Any]) -> str:
    """Build the judge user prompt for a single session."""
    digest = reconstruct_digest(rec["digest"])
    digest_text = digest_to_text(digest)
    task_description = digest.task_description[:400]

    return _JUDGE_USER_TEMPLATE.format(
        task_description=task_description,
        domain=rec["domain_id"],
        digest_text=digest_text,
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
    probe_timeout_s: float = 3.0  # fast probe — fail quickly when Ollama is absent
    inference_timeout_s: float = 300.0  # covers cold load (~30-60s) + large-session prefill


@dataclass
class ApiJudgeConfig:
    """Configuration for the opt-in API-key trajectory judge.

    The user's own API key. No tracegauge server involved. The call goes
    directly from this machine to the API provider with the user's key.

    REQUIRES consent_given=True in score_trajectory_api() before any
    network call is made — enforced unconditionally.

    Same validated v3 rubric as the local Qwen judge (same JUDGE_SYSTEM_PROMPT
    + _JUDGE_USER_TEMPLATE). Different model may interpret the rubric differently
    (B3: exact-match cross-model agreement is 58%; adjacent 85%). B3 caveats apply.
    """

    api_key: str  # user's own key; never shipped with tracegauge
    model: str = "claude-haiku-4-5-20251001"  # override with --api-judge-model
    provider: str = "anthropic"  # only "anthropic" currently supported
    inference_timeout_s: float = 120.0


# User-facing hint when judge is unavailable.
JUDGE_SETUP_HINT: str = (
    "Trajectory quality requires a local judge model (~18GB VRAM). "
    "To enable: install Ollama (https://ollama.ai), then run: "
    "ollama pull qwen3:30b-a3b. "
    "Without the judge, token and waste axes still run fully."
)

# Template for the interactive consent screen shown before any API judge call.
# Mirrors the P7 contribution-preview discipline: show exactly what leaves before
# it leaves. NEVER-SENT list is conservative — only claims what's guaranteed.
# 300-char snippets CAN carry code/content; secrets are scrubbed but arbitrary
# content is not filtered. That distinction must appear here, not just in docs.
_CONSENT_SEP = "═" * 70
API_JUDGE_CONSENT_NOTICE_TEMPLATE: str = (
    _CONSENT_SEP
    + "\n"
    + "API JUDGE — OPT-IN CONSENT\n"
    + _CONSENT_SEP
    + "\n"
    + "\n"
    + "Enabling the API judge will SEND SESSION TRAJECTORY DATA to Anthropic\n"
    + "using your API key.\n"
    + "\n"
    + "What will be sent:\n"
    + "  • Session ID: {session_id}\n"
    + "  • Task type:  {task_type}\n"
    + "  • Turn-by-turn trajectory: tool names, token counts per turn\n"
    + "  • 300-char snippets of tool inputs/outputs — these MAY contain code,\n"
    + "    file content, or other material from your session. Detected API keys\n"
    + "    and secrets are redacted at ingestion; other content is NOT filtered.\n"
    + "\n"
    + "What will NOT be sent:\n"
    + "  • Detected secrets/API keys (redacted at ingestion before any call)\n"
    + "  • Complete file contents (only up to 300-char excerpts in snippets)\n"
    + "  • Full file paths or project names beyond what appears in snippets\n"
    + "\n"
    + "Provider: Anthropic (api.anthropic.com)\n"
    + "Model:    {model}\n"
    + "Your key: {api_key_source}\n"
    + "\n"
    + "No tracegauge server. The call goes directly from your machine to Anthropic.\n"
    + "Your data, your key, your provider. tracegauge never sees the response.\n"
    + "\n"
    + "Domain-of-validity (same as local judge — API path changes availability only):\n"
    + "  Positive signal (MUCH_BETTER/BETTER) corroborated at 84–96%% cross-model.\n"
    + "  Negative signal (WORSE/MUCH_WORSE) is model-dependent — do not treat as fact.\n"
    + "  No human accuracy calibration. API judge uses the same v3 rubric as the\n"
    + "  validated local Qwen judge; a different model may interpret it differently.\n"
    + "\n"
    + _CONSENT_SEP
)


def build_api_judge_consent_notice(
    session_id: str,
    task_type: str,
    model: str,
    api_key_source: str,
) -> str:
    """Build the consent notice string for a specific session."""
    return API_JUDGE_CONSENT_NOTICE_TEMPLATE.format(
        session_id=session_id,
        task_type=task_type,
        model=model,
        api_key_source=api_key_source,
    )


JUDGE_SETUP_HINT_FULL: str = (
    "Trajectory quality is UNAVAILABLE (no local judge configured).\n"
    "\n"
    "Two options to enable it:\n"
    "\n"
    "  Option 1 — Local (free, requires ~18 GB VRAM):\n"
    "    1. Install Ollama: https://ollama.ai\n"
    "    2. ollama pull qwen3:30b-a3b\n"
    "    3. Re-run — the judge auto-detects when the model is available.\n"
    "\n"
    "  Option 2 — API (opt-in, your own key, sends trajectory data to Anthropic):\n"
    "    export ANTHROPIC_API_KEY=<your-key>\n"
    "    tes score <path> --api-judge\n"
    "    (Shows a consent screen before sending anything.)\n"
    "\n"
    "Without the judge, token and waste axes still run fully.\n"
    "Token + waste is a complete result — trajectory is an enhancement, not a fix."
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


def detect_env_api_key(env_var: str = "ANTHROPIC_API_KEY") -> str | None:
    """Detect an Anthropic API key in the environment. DETECT ONLY — never sends.

    Returns the key string if a non-empty value is present, else None.

    CRITICAL: detecting a key does NOT authorize egress. This function performs
    zero network activity and grants no permission to transmit. Any API-judge
    call still passes through the unconditional per-session consent gate
    (score_trajectory_api(consent_given=...)) before a single byte leaves the
    machine. Auto-detect a key ≠ auto-send — that boundary is the moat.
    """
    key = os.environ.get(env_var, "").strip()
    return key or None


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
            f"  Judge timed out after {config.inference_timeout_s:.0f}s — trajectory UNAVAILABLE",
            file=sys.stderr,
        )
        return None
    except httpx.HTTPStatusError as exc:
        # e.g. Ollama returns 500 when the model OOMs or fails mid-inference
        print(
            f"  Judge unavailable: Ollama returned HTTP {exc.response.status_code} "
            f"(model error or OOM) — trajectory UNAVAILABLE",
            file=sys.stderr,
        )
        return None
    except httpx.HTTPError:
        print("  Judge unavailable: network error — trajectory UNAVAILABLE", file=sys.stderr)
        return None
    except Exception:
        print("  Judge error — trajectory UNAVAILABLE", file=sys.stderr)
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
        Adapted session record from tes.adapt.adapt_session or pool_adapted.jsonl.
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


def _call_api_judge(
    record: dict[str, Any],
    config: ApiJudgeConfig,
) -> dict[str, Any] | None:
    """Internal Anthropic API call — only called after consent_given=True is confirmed.

    Uses the EXACT SAME JUDGE_SYSTEM_PROMPT and _build_user_prompt as the local
    Ollama judge. The rubric is identical; only the model and transport differ.

    NOTE: build_waste_entry(session_id, turns) expects digest turn dicts (the
    record["digest"]["turns"] list), not raw JSONL turns. This matches the
    watcher's call convention and must be maintained by any caller.
    """
    import json as _json

    scoring_rec = {**record, "domain_id": record.get("domain_id", "CC")}
    user_prompt = _build_user_prompt(scoring_rec)

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.model,
                "max_tokens": 1024,
                "system": JUDGE_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=config.inference_timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]
        raw = _json.loads(text)
    except httpx.ReadTimeout:
        print(
            f"  API judge timed out after {config.inference_timeout_s:.0f}s "
            "— trajectory UNAVAILABLE",
            file=sys.stderr,
        )
        return None
    except httpx.HTTPStatusError as exc:
        print(f"  API judge HTTP error {exc.response.status_code}: {exc}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  API judge error: {exc}", file=sys.stderr)
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
        "judge_path": "api",
        "api_model": config.model,
    }


def score_trajectory_api(
    record: dict[str, Any],
    config: ApiJudgeConfig,
    *,
    consent_given: bool,
) -> dict[str, Any] | None:
    """Run the v3 trajectory judge via the Anthropic API (opt-in path).

    Uses the SAME validated v3 rubric (JUDGE_SYSTEM_PROMPT + _JUDGE_USER_TEMPLATE)
    as the local Qwen judge. Different model → may interpret rubric differently.
    B3 caveats apply identically: positive corroborated, negative model-dependent,
    no human calibration.

    Parameters
    ----------
    record:
        Adapted session record from tes.adapt.adapt_session.
    config:
        ApiJudgeConfig with the user's own API key and model choice.
    consent_given:
        MUST be True before any network call is attempted. When False, returns
        None immediately with ZERO network activity — the consent gate is
        unconditional and cannot be bypassed. Callers obtain consent via the
        interactive prompt built from build_api_judge_consent_notice().

    Returns
    -------
    dict or None
        Same judge_entry dict format as score_trajectory() — compatible with
        score_session(judge_entry=result). None on no consent, missing key,
        or any call failure.
    """
    if not consent_given:
        return None
    if not config.api_key:
        return None
    return _call_api_judge(record, config)


__all__ = [
    "JudgeConfig",
    "ApiJudgeConfig",
    "JUDGE_SETUP_HINT",
    "JUDGE_SETUP_HINT_FULL",
    "API_JUDGE_CONSENT_NOTICE_TEMPLATE",
    "build_api_judge_consent_notice",
    "detect_env_api_key",
    "is_judge_available",
    "score_trajectory",
    "score_trajectory_api",
]
