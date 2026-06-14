from __future__ import annotations

"""tes/intelligence/chat.py — Constrained conversational explainer.

The LLM is an EXPLAINER over already-measured data. It is NOT an analyst.
This distinction is enforced by construction in three layers:

  1. CONTEXT: only computed metrics + ML outputs are assembled here and sent.
     Raw session content, code, file paths, tool inputs/outputs are NEVER
     included. The context is JSON-structured numbers, percentages, labels.

  2. SYSTEM PROMPT: explicitly constrains the model to (a) answer only from
     the provided context, (b) say 'not measured' when a fact is absent from
     context, (c) say 'I don't predict' for future/hypothetical questions,
     (d) cite the metric source for every number stated.

  3. TESTS (test_chat_grounding.py, test_chat_no_code_egress.py): verify
     answers-from-context-only, out-of-scope handling, and no-code in payload.

Transport: same local(Ollama)/API(Anthropic) pattern as judge.py. API path
requires explicit consent (chat sends metrics-only — much less sensitive than
the judge's 300-char snippets — but consent gate is still mandatory).

Public API:
    ChatConfig              — local Ollama configuration for chat
    ChatApiConfig           — API configuration + consent gate
    CHAT_EGRESS_NOTICE      — shown before any API chat call
    build_chat_context()    — assemble metrics-only context for a question
    ask_local()             — call local Ollama; return answer string or None
    ask_api()               — call Anthropic API; consent_given required
"""

import json as _json
from dataclasses import dataclass
from typing import Any

import httpx

from tes.intelligence.cache import format_intelligence_summary, get_or_compute_intelligence

# ---------------------------------------------------------------------------
# System prompt (the honesty boundary, enforced in text)
# ---------------------------------------------------------------------------

CHAT_SYSTEM_PROMPT = """\
You are an explainer for Claude Code session efficiency data collected by tracegauge.

Your role: explain the MEASURED DATA provided in the context block below. Nothing more.

RULES — enforce these strictly:
1. Answer ONLY from the metrics, statistics, and patterns in the provided context.
   Do not add facts, comparisons, or analysis that are not in the context.
2. If asked for a number or fact that is NOT in the context, respond:
   "I don't have that measured — tracegauge hasn't collected that metric."
3. If asked about future costs, predictions, forecasts, or what-ifs, respond:
   "I don't predict future behavior — I only explain what's already measured in your session history."
4. If asked for quality judgments ("was this session good/bad/efficient?"), respond:
   "tracegauge doesn't rate session quality — it describes measured patterns like token distribution and waste detection."
5. When you state a number, say where it comes from. Example: "from your attribution data, context re-send was 95% of billed tokens" not just "context re-send was 95%."
6. Keep answers to 3-6 sentences. Be specific and plain-language. No bullet lists unless the question asks for a list.
7. The three session archetypes describe modest variation on a fairly homogeneous corpus — most sessions have 93-96% context re-send. Do not dramatize archetypes into distinct "personalities." They reflect mainly differences in size, context-building stage, and waste presence.

WHAT YOU MUST NEVER DO:
- Invent numbers not in the context
- Make predictions or forecasts
- Suggest the user should change their workflow (unless a specific insight in the context directly implies an actionable pattern)
- Cite external benchmarks or compare to other developers
- Hallucinate patterns or clusters not in the provided analysis
"""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class ChatConfig:
    """Local Ollama chat configuration."""
    endpoint: str = "http://localhost:11434"
    model: str = "qwen3:8b"
    probe_timeout_s: float = 3.0
    inference_timeout_s: float = 120.0


@dataclass
class ChatApiConfig:
    """Anthropic API chat configuration. Consent required before any network call."""
    api_key: str
    model: str = "claude-haiku-4-5-20251001"
    inference_timeout_s: float = 60.0


# Consent notice for the API chat path.
# Chat sends METRICS ONLY (not session content/snippets) — much less sensitive
# than the judge path, but we still show a consent screen: egress is egress.
_SEP = "─" * 70
CHAT_EGRESS_NOTICE = (
    _SEP + "\n"
    + "CHAT — API CALL OPT-IN CONSENT\n"
    + _SEP + "\n"
    + "\n"
    + "Answering this question will send SESSION METRICS to Anthropic.\n"
    + "\n"
    + "What will be sent (METRICS ONLY — no session content, no code):\n"
    + "  • Corpus statistics: session counts, cost/token percentiles\n"
    + "  • Cluster descriptions: archetype names, centroid feature values\n"
    + "  • Anomaly counts and statistics\n"
    + "  • Specific session numbers if you asked about a named session\n"
    + "\n"
    + "What will NOT be sent:\n"
    + "  • Session content, code, tool inputs/outputs, or file paths\n"
    + "  • Any text from your actual sessions\n"
    + "  • Raw session IDs or project names\n"
    + "\n"
    + "This is significantly less data than the opt-in API judge (which sends\n"
    + "300-character session snippets). Only computed numbers and statistics.\n"
    + "\n"
    + "Provider: Anthropic (api.anthropic.com)\n"
    + "No tracegauge server. Your key, your provider, direct connection.\n"
    + _SEP
)


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def build_chat_context(
    question: str,
    *,
    db_path: str | None = None,
    force_recompute: bool = False,
) -> dict[str, Any]:
    """Assemble the metrics-only context the LLM will use to answer `question`.

    The context is a structured dict of computed numbers and ML outputs.
    It NEVER contains raw session content, code, file paths, or tool outputs.

    Returns a dict with:
      "intelligence": ML summary (archetypes, anomaly count, validity)
      "corpus_stats": aggregate metrics from the store
      "session": specific session data if question mentions a session ID (else None)
      "question": the original question (for prompt construction)
    """
    from tes.store import open_db, list_sessions
    import numpy as np

    intelligence = get_or_compute_intelligence(db_path=db_path, force_recompute=force_recompute)

    conn = open_db(db_path)
    rows = list_sessions(conn, limit=5000, offset=0)
    conn.close()

    # --- Corpus-level stats (metrics only) ---
    content_rows = [r for r in rows if r.get("real_tokens", 0) > 0]
    all_costs = [r["session_cost_usd"] for r in content_rows if r.get("session_cost_usd")]
    all_tokens = [r["real_tokens"] for r in content_rows if r.get("real_tokens", 0) > 0]
    waste_rows = [r for r in content_rows if (r.get("waste_event_count") or 0) > 0]

    from collections import Counter
    type_counts = Counter(r["task_type"] for r in rows)

    corpus_stats: dict[str, Any] = {
        "total_sessions_in_store": len(rows),
        "content_sessions": len(content_rows),
        "task_type_counts": dict(type_counts),
        "cost_usd": {
            "n": len(all_costs),
            "median": round(float(np.median(all_costs)), 2) if all_costs else None,
            "p75": round(float(np.percentile(all_costs, 75)), 2) if all_costs else None,
            "p95": round(float(np.percentile(all_costs, 95)), 2) if all_costs else None,
            "total": round(sum(all_costs), 2) if all_costs else None,
        },
        "real_tokens": {
            "median": int(np.median(all_tokens)) if all_tokens else None,
            "p75": int(np.percentile(all_tokens, 75)) if all_tokens else None,
        },
        "waste": {
            "sessions_with_waste": len(waste_rows),
            "pct_of_content": round(100.0 * len(waste_rows) / max(len(content_rows), 1), 1),
            "total_waste_events": sum((r.get("waste_event_count") or 0) for r in waste_rows),
        },
    }

    # --- Session lookup (if question references a session ID) ---
    session_data = None
    # Simple heuristic: look for UUID-like strings in the question
    import re
    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
    session_ids_in_q = uuid_pattern.findall(question)
    if session_ids_in_q:
        from tes.store import get_session
        conn = open_db(db_path)
        for sid in session_ids_in_q[:1]:  # max 1 session per query
            row = get_session(conn, sid)
            if row:
                session_data = _summarize_session(row)
                break
        conn.close()

    return {
        "question": question,
        "intelligence": intelligence,
        "corpus_stats": corpus_stats,
        "session": session_data,
    }


def _summarize_session(row: dict) -> dict[str, Any]:
    """Build a metrics-only summary of a single session for the chat context.

    NEVER includes raw content, code, tool inputs/outputs, or file paths.
    Only numbers, labels, and measured metrics.
    """
    waste_events = row.get("waste_events") or []
    waste_types = list({e.get("detector", "unknown") for e in waste_events})
    return {
        "session_id_prefix": row["session_id"][:8] + "...",   # partial ID only
        "task_type": row.get("task_type"),
        "scored_at": row.get("scored_at"),
        "real_tokens": row.get("real_tokens"),
        "turn_count": row.get("turn_count"),
        "band_verdict": row.get("band_verdict"),
        "scope_status": row.get("scope_status"),
        "session_cost_usd": row.get("session_cost_usd"),
        "judge_verdict": row.get("judge_verdict"),
        "judge_score": row.get("judge_score"),
        "waste_event_count": row.get("waste_event_count"),
        "waste_types_detected": waste_types,
        "baseline_source": row.get("baseline_source"),
    }


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_user_message(context: dict[str, Any]) -> str:
    """Build the user message (context + question) for the LLM.

    The context block is always included so the model has grounded data.
    No session content or code ever enters this block.
    """
    intelligence_text = format_intelligence_summary(context["intelligence"])
    corpus = context["corpus_stats"]

    lines = [
        "=== MEASURED SESSION DATA (your context for answering) ===",
        "",
        f"CORPUS: {corpus['total_sessions_in_store']} total sessions in store; "
        f"{corpus['content_sessions']} content sessions (real_tokens > 0).",
        f"Task type breakdown: {corpus['task_type_counts']}",
        "",
        "COST (content sessions with cost data):",
        f"  Median: ${corpus['cost_usd']['median']}/session  "
        f"  P75: ${corpus['cost_usd']['p75']}/session  "
        f"  P95: ${corpus['cost_usd']['p95']}/session  "
        f"  Total corpus: ${corpus['cost_usd']['total']}",
        "",
        "TOKEN ECONOMY (content sessions):",
        f"  Median real_tokens: {corpus['real_tokens']['median'] or 'n/a'}  "
        f"  P75: {corpus['real_tokens']['p75'] or 'n/a'}",
        "",
        f"WASTE: {corpus['waste']['sessions_with_waste']} of {corpus['content_sessions']} "
        f"content sessions ({corpus['waste']['pct_of_content']}%) have detected waste "
        f"({corpus['waste']['total_waste_events']} total waste events).",
        "",
        intelligence_text,
    ]

    if context.get("session"):
        s = context["session"]
        lines += [
            "",
            f"SPECIFIC SESSION (partial ID: {s['session_id_prefix']}*):",
            f"  task_type: {s['task_type']}  scored: {s['scored_at']}",
            f"  real_tokens: {s['real_tokens']:,}  turn_count: {s['turn_count']}  "
            f"  cost: ${s['session_cost_usd']}",
            f"  token_verdict: {s['band_verdict']} (scope: {s['scope_status']})",
            f"  judge_verdict: {s['judge_verdict']} (score: {s['judge_score']})",
            f"  waste_events: {s['waste_event_count']} ({s['waste_types_detected']})",
            f"  baseline_source: {s['baseline_source']}",
        ]

    lines += [
        "",
        "=== USER QUESTION ===",
        context["question"],
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Local Ollama call
# ---------------------------------------------------------------------------


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models (e.g. qwen3).

    Some thinking-capable models embed their reasoning chain in the content field
    even when think=False is requested. Handles two cases:
    - Complete thinking blocks: <think>...</think> followed by the answer
    - Truncated thinking (no </think>): the whole response is reasoning with no answer;
      we detect this and return empty string so the caller falls back gracefully.
    """
    import re as _re
    # Remove complete <think>...</think> blocks
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL)
    # If </think> appears without an opening tag, keep only what follows it
    if "</think>" in text:
        text = text.split("</think>", 1)[-1]
    return text.strip()


def _probe_ollama_models(endpoint: str, timeout: float) -> list[str]:
    try:
        resp = httpx.get(f"{endpoint}/api/tags", timeout=timeout)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


def _pick_chat_model(config: ChatConfig) -> str | None:
    """Return the best available Ollama model for chat, or None if Ollama is down."""
    models = _probe_ollama_models(config.endpoint, config.probe_timeout_s)
    if not models:
        return None
    # Prefer the configured model
    for m in models:
        if m == config.model or m.startswith(f"{config.model}:"):
            return m
    # Fall back to any model with >= 7B params (rough heuristic: name contains a number >= 7)
    import re as _re
    for m in models:
        nums = [int(x) for x in _re.findall(r"\d+", m) if int(x) >= 7]
        if nums:
            return m
    # Fall back to whatever is available
    return models[0] if models else None


def ask_local(
    question: str,
    *,
    db_path: str | None = None,
    config: ChatConfig | None = None,
    force_recompute: bool = False,
) -> str | None:
    """Ask a question using the local Ollama model. Returns answer string or None.

    Returns None if Ollama is unavailable.
    SENDS METRICS ONLY — no session content, no code.
    """
    if config is None:
        config = ChatConfig()

    model = _pick_chat_model(config)
    if model is None:
        return None

    context = build_chat_context(question, db_path=db_path, force_recompute=force_recompute)
    user_message = _build_user_message(context)

    try:
        response_text = ""
        with httpx.stream(
            "POST",
            f"{config.endpoint}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "stream": True,
                "options": {"temperature": 0.1, "seed": 42, "num_ctx": 8192, "num_predict": 1024},
                "think": False,  # suppress extended thinking on qwen3/thinking-capable models
            },
            timeout=httpx.Timeout(connect=5.0, read=config.inference_timeout_s, write=10.0, pool=10.0),
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
        return _strip_thinking(response_text) or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# API call (Anthropic, consent required)
# ---------------------------------------------------------------------------


def ask_api(
    question: str,
    config: ChatApiConfig,
    *,
    consent_given: bool,
    db_path: str | None = None,
    force_recompute: bool = False,
) -> str | None:
    """Ask a question via the Anthropic API. Returns answer string or None.

    REQUIRES consent_given=True. Returns None immediately if consent not given.
    SENDS METRICS ONLY — no session content, no code.
    """
    if not consent_given:
        return None
    if not config.api_key:
        return None

    context = build_chat_context(question, db_path=db_path, force_recompute=force_recompute)
    user_message = _build_user_message(context)

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
                "max_tokens": 512,
                "system": CHAT_SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_message}],
            },
            timeout=config.inference_timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"].strip() or None
    except Exception:
        return None


__all__ = [
    "ChatConfig",
    "ChatApiConfig",
    "CHAT_SYSTEM_PROMPT",
    "CHAT_EGRESS_NOTICE",
    "build_chat_context",
    "ask_local",
    "ask_api",
]
