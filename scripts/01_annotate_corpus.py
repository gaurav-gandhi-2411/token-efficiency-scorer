"""
01_annotate_corpus.py  — Phase A.1 annotation

Annotates all 200 sessions with LLM-derived waste labels using:
  - Bulk (200 sessions): openai/gpt-oss-120b via Groq (OpenAI-compatible)
  - IAA subset (25 sessions, seed=42): claude-sonnet-4-6 via Anthropic

Per-session labels: H1 (redundant_read), H2 (duplicate_message), H3 (backtrack),
H4 (tool_result_used), plus session-level waste_pct and waste_categories.
All output fields are prefixed with `llm_`.

Outputs:
  data/validation-corpus/annotations/gpt_oss/{session_id}.json
  data/validation-corpus/annotations/sonnet_iaa/{session_id}.json  (25 sessions)
  data/validation-corpus/annotations/iaa_phaseA1.json
  data/cost-log.jsonl

Usage:
    python scripts/01_annotate_corpus.py --mode preflight   # 5-session cost check + halt
    python scripts/01_annotate_corpus.py                    # preflight then full run
    python scripts/01_annotate_corpus.py --mode bulk-only   # GPT-OSS only (skip Sonnet IAA)
    python scripts/01_annotate_corpus.py --mode iaa-only    # Sonnet IAA + kappa only
    python scripts/01_annotate_corpus.py --dry-run          # print first prompt, no API calls
    python scripts/01_annotate_corpus.py --mode batch-submit [--limit N] [--force]
    python scripts/01_annotate_corpus.py --mode batch-poll --batch-id <id>
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import sys
import time
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
TRACES_DIR = REPO_ROOT / "data" / "validation-corpus" / "traces_normalized"
ANNOT_DIR = REPO_ROOT / "data" / "validation-corpus" / "annotations"
GPT_OSS_DIR = ANNOT_DIR / "gpt_oss"
SONNET_IAA_DIR = ANNOT_DIR / "sonnet_iaa"
COST_LOG = REPO_ROOT / "data" / "cost-log.jsonl"

# Batch API directories and logs — created at runtime if needed
BATCH_REQUESTS_DIR = DATA_DIR / "batch_requests"
BATCH_RESPONSES_DIR = DATA_DIR / "batch_responses"
BATCH_JOBS_LOG = DATA_DIR / "batch_jobs.jsonl"
# Lands at data/validation-corpus/skipped.jsonl
SKIPPED_LOG = TRACES_DIR.parent / "skipped.jsonl"

for _d in (GPT_OSS_DIR, SONNET_IAA_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SEED = 42
IAA_N = 25
PREFLIGHT_N = 5

# Cap assistant turns shown per session — keeps prompts under 131k tokens
MAX_ASST_TURNS_SHOWN = 80
CONTENT_PREVIEW_CHARS = 600
RESULT_PREVIEW_CHARS = 300

GROQ_MODEL = "openai/gpt-oss-120b"
SONNET_MODEL = "claude-sonnet-4-6"
GROQ_CONTEXT_LIMIT = 131_072  # tokens
# Groq free-tier TPM limit is ~8 000 tokens/min for gpt-oss-120b.
# A 20-second inter-request pause keeps us under 3 sessions/min * avg 3k tokens = ~9k.
# Retry rate-limit (413) with 70-second sleep to let the window reset.
INTER_REQUEST_SLEEP_SEC: float = 20.0
RATE_LIMIT_SLEEP_SEC: float = 70.0

# Pricing constants (USD per million tokens)
# gpt-oss-120b via Groq — use $0.90/$0.90 as conservative estimate; update after preflight
GROQ_COST_PER_M_IN: float = 0.90
GROQ_COST_PER_M_OUT: float = 0.90
# Sonnet 4.6 standard pricing; cached reads are 10% of input price
SONNET_COST_PER_M_IN: float = 3.00
SONNET_COST_PER_M_IN_CACHED: float = 0.30
SONNET_COST_PER_M_OUT: float = 15.00

# Groq Batch API pricing — 50% discount on sync rates
# Sync: $0.90/M in, $0.90/M out → Batch: $0.075/M in, $0.30/M out
# (Using Groq's published batch discount of ~$0.15/M in, $0.60/M out halved)
GROQ_BATCH_COST_PER_M_IN: float = 0.075
GROQ_BATCH_COST_PER_M_OUT: float = 0.30

# Anthropic claude-haiku-4-5 pricing (USD per million tokens)
HAIKU_MODEL: str = "claude-haiku-4-5-20251001"
HAIKU_COST_PER_M_IN: float = 0.80
HAIKU_COST_PER_M_OUT: float = 4.00
HAIKU_COST_PER_M_IN_CACHED: float = 0.08   # prompt-cache read rate
# Anthropic Batch API: 50% discount on sync rates
ANTHROPIC_BATCH_COST_PER_M_IN: float = 0.40
ANTHROPIC_BATCH_COST_PER_M_OUT: float = 2.00
ANTHROPIC_BATCH_COST_PER_M_IN_CACHED: float = 0.04

# Budget hard limits (USD)
BUDGET_GPT_OSS: float = 4.00
BUDGET_SONNET: float = 2.00

# Active batch statuses — used to gate re-submit without --force
_ACTIVE_BATCH_STATUSES = {"validating", "in_progress", "finalizing"}


# ── System prompt / rubric (cached on both providers) ────────────────────────

RUBRIC_SYSTEM = """\
You are a precise evaluator for coding-agent session traces. Label each \
assistant turn for four waste signals. Return ONLY a valid JSON object -- \
no text outside the JSON.

====================================================================
LABEL DEFINITIONS AND RUBRICS
====================================================================

H1 -- llm_h1_redundant_read  (bool)
A file read is REDUNDANT if and only if ALL three hold:
  1. The same file path was already read in a prior turn of this session.
  2. No write/edit to that file path occurred between the two reads.
  3. No failure, error, or test-fail occurred between the two reads that
     would give legitimate reason to re-read (e.g. "Traceback", "FAILED",
     "AssertionError", "Error:", "exit code 1", "command not found").

POSITIVE (label True):
  Turn 5 reads /src/utils.py → returns content.
  Turns 6-9: pure reasoning, no edits, no errors.
  Turn 10 reads /src/utils.py again → REDUNDANT.

NEGATIVE (label False — do NOT flag these):
  • Turn 5 reads file.py; turn 7 a test FAILS; turn 9 re-reads file.py.
    [Justified by intervening failure]
  • Turn 5 reads file.py; turn 7 edits it; turn 10 re-reads to verify.
    [Justified by intervening write]
  • First read of a file in the session. [Not a duplicate]

--------------------------------------------------------------------
H2 -- llm_h2_duplicate_message  (bool)
The assistant turn sends content nearly identical (>90% char overlap after
whitespace-strip) to a prior assistant turn, with no meaningful new intent.
This captures stuck-loop / copy-paste repetition.

POSITIVE:
  Turn 4:  "Let me check the directory: ls -la"
  Turn 11: "Let me check the directory: ls -la"  ← DUPLICATE

NEGATIVE:
  • Same command with different arguments.
  • Similar phrasing but materially different intent.
  • Short turns < 50 chars that coincidentally overlap.

--------------------------------------------------------------------
H3 -- llm_h3_backtrack  (bool)
Agent reverses or abandons a prior approach. Requires BOTH:
  (a) Explicit abandonment language ("that approach didn't work",
      "let me try differently", "scratch that", "let me start over",
      "I was wrong about", "instead, let me").
  (b) A prior failure or contradiction that prompted the reversal.

Mere exploration or refinement is NOT a backtrack.

POSITIVE:
  Turn 8: tests run → "FAILED: 3 tests".
  Turn 9: "That approach didn't work. Let me try a different strategy."
  ← BACKTRACK (failure at 8 prompted reversal at 9).

NEGATIVE:
  • "Let me also check this other file" — exploration, no failure.
  • "Let me refine the fix" — iterative improvement.
  • First mention of a new approach without a prior failure.

--------------------------------------------------------------------
H4 -- llm_h4_tool_result_used  (bool)
True if the tool result from this turn is visibly incorporated in the
agent's reasoning (current turn text or next assistant turn).
For turns with NO tool call, set True (vacuously satisfied).

POSITIVE:
  Turn 6 reads file.py → "class Foo: def bar(): ...".
  Turn 7: "I can see Foo.bar() needs updating, so I'll edit it."
  ← True (result referenced in reasoning).

NEGATIVE:
  Turn 6 reads file.py → content.
  Turn 7: "Let me run the tests." (no reference to file content)
  ← False (result ignored).

====================================================================
OUTPUT SCHEMA
====================================================================

{
  "session_id": "<id>",
  "per_turn_labels": [
    {
      "turn_index": <int>,
      "llm_h1_redundant_read": <bool>,
      "llm_h1_reason": "<empty string if False, else one-sentence reason>",
      "llm_h2_duplicate_message": <bool>,
      "llm_h2_reason": "<empty string if False, else one-sentence reason>",
      "llm_h3_backtrack": <bool>,
      "llm_h3_reason": "<empty string if False, else one-sentence reason>",
      "llm_h4_tool_result_used": <bool>,
      "llm_h4_reason": "<empty string if False, else one-sentence reason>"
    }
  ],
  "llm_total_waste_pct": <int 0-100>,
  "llm_waste_categories": <list of zero or more:
    "bad_initial_direction" | "premature_commitment" | "tool_thrashing" |
    "context_bloat" | "verbose_reasoning" | "dead_exploration">
}
"""

USER_PROMPT_TEMPLATE = """\
Annotate the following coding-agent session for waste signals.

SESSION METADATA
  session_id : {session_id}
  scaffold   : {scaffold}
  outcome    : {outcome}
  total_turns: {total_turns}
  turns_shown: {turns_shown}

TURN TRANSCRIPT (all turns shown for context; label ONLY assistant/ai turns)
{turn_text}

Apply the rubrics from the system message exactly. Return one entry in \
per_turn_labels for each assistant/ai turn in order. Do not skip any.
"""


# ── Transcript formatting ─────────────────────────────────────────────────────

def _format_turns(session: dict[str, Any]) -> tuple[str, list[int]]:
    """Serialize the session into a prompt transcript. Returns (text, shown_asst_indices)."""
    turns = sorted(session["turns"], key=lambda t: t["turn_index"])

    asst_turns = [
        t for t in turns
        if t["role"] in ("assistant", "ai")
        and (len(t.get("content_text", "")) > 5 or len(t.get("tool_uses", [])) > 0)
    ][:MAX_ASST_TURNS_SHOWN]

    if not asst_turns:
        return "", []

    max_idx = asst_turns[-1]["turn_index"]
    shown_asst_indices = [t["turn_index"] for t in asst_turns]

    lines: list[str] = []
    for t in turns:
        if t["turn_index"] > max_idx + 2:
            break
        role = t["role"].upper()

        if t["role"] == "system":
            lines.append(f"\n=== TURN {t['turn_index']} [SYSTEM] (omitted) ===")
            continue

        content = (t.get("content_text") or "")[:CONTENT_PREVIEW_CHARS]
        tool_uses = t.get("tool_uses", [])

        lines.append(f"\n=== TURN {t['turn_index']} [{role}] ===")
        if content:
            lines.append(content)

        for tu in tool_uses[:4]:
            lines.append(f"  [TOOL: {tu['tool_name']}]")
            inp = tu.get("tool_input")
            if inp:
                inp_str = json.dumps(inp, ensure_ascii=False) if isinstance(inp, dict) else str(inp)
                lines.append(f"  [INPUT: {inp_str[:200]}]")
            res = tu.get("tool_result")
            if res:
                lines.append(f"  [RESULT: {str(res)[:RESULT_PREVIEW_CHARS]}]")

    return "\n".join(lines), shown_asst_indices


def _build_groq_messages(session: dict[str, Any]) -> tuple[list[dict[str, str]], list[int]] | tuple[None, None]:
    """Build the messages list for a Groq chat completion request.

    Returns (messages, shown_asst_indices) or (None, None) if the session has no
    displayable assistant turns.  Extracted so sync (_call_groq) and batch
    (_build_batch_request_line) share identical rubric/prompt construction.
    """
    turn_text, shown_indices = _format_turns(session)
    if not shown_indices:
        return None, None

    prompt = USER_PROMPT_TEMPLATE.format(
        session_id=session["session_id"],
        scaffold=session["scaffold"],
        outcome=session["outcome"]["result"],
        total_turns=session["turn_count"],
        turns_shown=len(shown_indices),
        turn_text=turn_text,
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": RUBRIC_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    return messages, shown_indices


# ── API calls ─────────────────────────────────────────────────────────────────

def _call_groq(
    client: Any,
    session: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any] | None:
    messages, shown_indices = _build_groq_messages(session)
    if shown_indices is None:
        return None

    if dry_run:
        print("=== DRY RUN — Groq prompt (first 3000 chars) ===")
        print(RUBRIC_SYSTEM[:400])
        print("[... rubric continues ...]")
        # Reconstruct prompt text for display
        turn_text, _ = _format_turns(session)
        prompt = USER_PROMPT_TEMPLATE.format(
            session_id=session["session_id"],
            scaffold=session["scaffold"],
            outcome=session["outcome"]["result"],
            total_turns=session["turn_count"],
            turns_shown=len(shown_indices),
            turn_text=turn_text,
        )
        print(prompt[:2600])
        return None

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
                max_tokens=4096,
            )
            raw = resp.choices[0].message.content.strip()
            annotation = json.loads(raw)
            annotation["_model"] = GROQ_MODEL
            annotation["_shown_turn_indices"] = shown_indices
            annotation["_input_tokens"] = resp.usage.prompt_tokens
            annotation["_output_tokens"] = resp.usage.completion_tokens
            return annotation
        except json.JSONDecodeError as e:
            print(f"    JSON parse error (attempt {attempt + 1}/3): {e}")
            if attempt == 2:
                return {"error": "json_parse_failed", "session_id": session["session_id"]}
        except Exception as e:
            err = str(e)
            print(f"    API error (attempt {attempt + 1}/3): {err[:200]}")
            if "context_length_exceeded" in err or "maximum context" in err.lower():
                return {"error": "context_exceeded", "session_id": session["session_id"]}
            # 413 = Groq rate-limit (TPM exceeded); sleep and retry
            is_rate_limit = "413" in err or "too large" in err.lower() or "rate_limit" in err.lower()
            if attempt < 2:
                sleep_s = RATE_LIMIT_SLEEP_SEC if is_rate_limit else 2 ** (attempt + 1)
                print(f"    sleeping {sleep_s:.0f}s before retry…")
                time.sleep(sleep_s)
            else:
                return {"error": err[:200], "session_id": session["session_id"]}
    return None


def _call_sonnet(
    client: Any,
    session: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any] | None:
    turn_text, shown_indices = _format_turns(session)
    if not shown_indices:
        return None

    prompt = USER_PROMPT_TEMPLATE.format(
        session_id=session["session_id"],
        scaffold=session["scaffold"],
        outcome=session["outcome"]["result"],
        total_turns=session["turn_count"],
        turns_shown=len(shown_indices),
        turn_text=turn_text,
    )

    if dry_run:
        print("=== DRY RUN — Sonnet IAA prompt (first 2000 chars) ===")
        print(prompt[:2000])
        return None

    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=SONNET_MODEL,
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": RUBRIC_SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1] if len(parts) > 1 else raw
                if raw.startswith("json"):
                    raw = raw[4:]
            annotation = json.loads(raw)
            annotation["_model"] = SONNET_MODEL
            annotation["_shown_turn_indices"] = shown_indices
            annotation["_input_tokens"] = msg.usage.input_tokens
            annotation["_output_tokens"] = msg.usage.output_tokens
            annotation["_cached_input_tokens"] = getattr(msg.usage, "cache_read_input_tokens", 0)
            return annotation
        except json.JSONDecodeError as e:
            print(f"    JSON parse error (attempt {attempt + 1}/3): {e}")
            if attempt == 2:
                return {"error": "json_parse_failed", "session_id": session["session_id"]}
        except Exception as e:
            print(f"    API error (attempt {attempt + 1}/3): {str(e)[:200]}")
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
            else:
                return {"error": str(e)[:200], "session_id": session["session_id"]}
    return None


# ── Cost tracking ─────────────────────────────────────────────────────────────

def _estimate_cost(annotation: dict[str, Any], model: str) -> float:
    in_t = annotation.get("_input_tokens", 0)
    out_t = annotation.get("_output_tokens", 0)
    if model == GROQ_MODEL:
        return in_t * GROQ_COST_PER_M_IN / 1e6 + out_t * GROQ_COST_PER_M_OUT / 1e6
    cached_t = annotation.get("_cached_input_tokens", 0)
    uncached = max(0, in_t - cached_t)
    return (
        uncached * SONNET_COST_PER_M_IN / 1e6
        + cached_t * SONNET_COST_PER_M_IN_CACHED / 1e6
        + out_t * SONNET_COST_PER_M_OUT / 1e6
    )


def _estimate_cost_batch(input_tokens: int, output_tokens: int) -> float:
    """Estimate cost for a Groq Batch API request (50% discount vs sync).

    Uses GROQ_BATCH_COST_PER_M_IN / OUT rather than the sync rates.
    """
    return input_tokens * GROQ_BATCH_COST_PER_M_IN / 1e6 + output_tokens * GROQ_BATCH_COST_PER_M_OUT / 1e6


def _log_cost(
    session_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    cost_usd: float,
    mode: str = "sync",
    provider: str | None = None,
) -> None:
    """Append one cost-log entry to COST_LOG (append-only JSONL).

    Args:
        session_id: The session being annotated.
        model: Model identifier string.
        input_tokens: Prompt token count.
        output_tokens: Completion token count.
        cached_tokens: Cached prompt tokens (Anthropic only; 0 for Groq).
        cost_usd: Estimated cost in USD.
        mode: "sync" (default) or "batch". Written to the log entry for traceability.
        provider: Optional provider name (e.g. "anthropic"). Omitted when None.
    """
    entry: dict[str, Any] = {
        "session_id": session_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "cost_estimate_usd": round(cost_usd, 6),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if mode != "sync":
        entry["mode"] = mode
    if provider is not None:
        entry["provider"] = provider
    with COST_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── IAA computation ───────────────────────────────────────────────────────────

def _cohen_kappa(a_labels: list[int], b_labels: list[int]) -> dict[str, Any]:
    n = len(a_labels)
    if n == 0:
        return {"kappa": None, "n_turns": 0}
    po = sum(x == y for x, y in zip(a_labels, b_labels)) / n
    pa = sum(a_labels) / n
    pb = sum(b_labels) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    kappa = (po - pe) / (1 - pe) if pe < 1.0 else 1.0
    return {
        "kappa": round(kappa, 3),
        "po": round(po, 3),
        "pe": round(pe, 3),
        "n_turns": n,
        "gpt_oss_pos_rate": round(pa, 3),
        "sonnet_pos_rate": round(pb, 3),
        "agreement_level": (
            "substantial" if kappa >= 0.6
            else "moderate" if kappa >= 0.4
            else "fair" if kappa >= 0.2
            else "poor"
        ),
        "below_threshold": kappa < 0.6,
    }


def compute_iaa(gpt_dir: pathlib.Path, son_dir: pathlib.Path) -> dict[str, Any]:
    gpt_files = {f.stem: f for f in gpt_dir.glob("*.json")}
    son_files = {f.stem: f for f in son_dir.glob("*.json")}
    overlap = sorted(set(gpt_files.keys()) & set(son_files.keys()))

    if len(overlap) < 5:
        return {"error": f"insufficient overlap: {len(overlap)} sessions"}

    label_fields = [
        ("H1_redundant_read", "llm_h1_redundant_read"),
        ("H2_duplicate_message", "llm_h2_duplicate_message"),
        ("H3_backtrack", "llm_h3_backtrack"),
        ("H4_tool_result_used", "llm_h4_tool_result_used"),
    ]
    results: dict[str, Any] = {}

    for label_name, field in label_fields:
        g_vals: list[int] = []
        s_vals: list[int] = []
        for sid in overlap:
            g_ann = json.loads(gpt_files[sid].read_text(encoding="utf-8"))
            s_ann = json.loads(son_files[sid].read_text(encoding="utf-8"))
            if "error" in g_ann or "error" in s_ann:
                continue
            g_by_turn = {t["turn_index"]: t for t in g_ann.get("per_turn_labels", [])}
            s_by_turn = {t["turn_index"]: t for t in s_ann.get("per_turn_labels", [])}
            for ti in sorted(set(g_by_turn) & set(s_by_turn)):
                g_vals.append(int(bool(g_by_turn[ti].get(field, False))))
                s_vals.append(int(bool(s_by_turn[ti].get(field, False))))

        results[label_name] = _cohen_kappa(g_vals, s_vals)

    valid_kappas = [v["kappa"] for v in results.values() if v.get("kappa") is not None]
    below = [k for k, v in results.items() if v.get("below_threshold", False)]
    return {
        "overlap_sessions": len(overlap),
        "per_label": results,
        "mean_kappa": round(sum(valid_kappas) / max(1, len(valid_kappas)), 3),
        "labels_below_threshold": below,
    }


# ── Batch API helpers ─────────────────────────────────────────────────────────

def _build_batch_request_line(session: dict[str, Any]) -> dict[str, Any] | None:
    """Build a single JSONL request line for the Groq Batch API.

    Returns a dict matching the OpenAI batch request format, or None if the
    session has no displayable assistant turns.  The messages payload is built
    via _build_groq_messages so the rubric is byte-identical to sync calls.
    """
    messages, shown_indices = _build_groq_messages(session)
    if messages is None or shown_indices is None:
        return None

    return {
        "custom_id": session["session_id"],
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": GROQ_MODEL,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": 4096,
        },
    }


def _read_batch_jobs_log() -> list[dict[str, Any]]:
    """Read all lines from BATCH_JOBS_LOG; returns empty list if file missing."""
    if not BATCH_JOBS_LOG.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in BATCH_JOBS_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def _latest_status_for_batch(entries: list[dict[str, Any]], batch_id: str) -> str | None:
    """Return the most-recently-logged status_last_seen for a given batch_id."""
    status = None
    for entry in entries:
        if entry.get("batch_id") == batch_id:
            status = entry.get("status_last_seen", status)
    return status


def _append_batch_jobs_log(entry: dict[str, Any]) -> None:
    """Append one line to BATCH_JOBS_LOG (append-only)."""
    BATCH_JOBS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with BATCH_JOBS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _append_skipped_log(entry: dict[str, Any]) -> None:
    """Append one error record to SKIPPED_LOG (append-only)."""
    SKIPPED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SKIPPED_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _submit_batch(
    client: Any,
    sessions: list[dict[str, Any]],
    batch_dir: pathlib.Path = BATCH_REQUESTS_DIR,
) -> dict[str, Any]:
    """Build a batch JSONL, upload it, create the batch, and log the job.

    Args:
        client: OpenAI-compat client pointed at Groq base URL.
        sessions: List of session dicts to annotate via the batch API.
        batch_dir: Directory where the JSONL request file is written.

    Returns:
        A dict with batch metadata: batch_id, submitted_at, n_requests,
        session_ids, input_file_id, status_last_seen, filename.
    """
    batch_dir.mkdir(parents=True, exist_ok=True)
    BATCH_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)

    submitted_at = datetime.now(UTC)
    ts = submitted_at.strftime("%Y%m%dT%H%M%SZ")
    filename = batch_dir / f"{ts}.jsonl"

    # Build JSONL lines, skipping sessions with no assistant turns
    lines: list[str] = []
    included_ids: list[str] = []
    skipped_no_turns: list[str] = []
    for s in sessions:
        req = _build_batch_request_line(s)
        if req is None:
            skipped_no_turns.append(s["session_id"])
        else:
            lines.append(json.dumps(req, ensure_ascii=False))
            included_ids.append(s["session_id"])

    if skipped_no_turns:
        print(f"  Note: {len(skipped_no_turns)} sessions skipped (no assistant turns): {skipped_no_turns}")

    if not lines:
        raise ValueError("No valid sessions to submit — all sessions had no assistant turns.")

    filename.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Wrote {len(lines)} requests to {filename}")

    # Upload the file
    with filename.open("rb") as fh:
        file_obj = client.files.create(file=fh, purpose="batch")
    input_file_id: str = file_obj.id
    print(f"  Uploaded file: {input_file_id}")

    # Create the batch
    batch = client.batches.create(
        input_file_id=input_file_id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    batch_id: str = batch.id
    initial_status: str = getattr(batch, "status", "submitted")

    job_entry: dict[str, Any] = {
        "batch_id": batch_id,
        "submitted_at": submitted_at.isoformat(),
        "n_requests": len(lines),
        "session_ids": included_ids,
        "input_file_id": input_file_id,
        "status_last_seen": initial_status,
        "filename": str(filename),
    }
    _append_batch_jobs_log(job_entry)

    return job_entry


def _submit_anthropic_batch(
    client: Any,
    sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Submit missing sessions to the Anthropic Message Batches API.

    Uses claude-haiku-4-5 with cache_control: ephemeral on the rubric prefix.
    Returns a job-metadata dict logged to BATCH_JOBS_LOG.
    """
    submitted_at = datetime.now(UTC)

    requests_list: list[Any] = []
    included_ids: list[str] = []
    skipped_no_turns: list[str] = []

    for s in sessions:
        turn_text, shown_indices = _format_turns(s)
        if not shown_indices:
            skipped_no_turns.append(s["session_id"])
            continue

        user_prompt = USER_PROMPT_TEMPLATE.format(
            session_id=s["session_id"],
            scaffold=s["scaffold"],
            outcome=s["outcome"]["result"],
            total_turns=s["turn_count"],
            turns_shown=len(shown_indices),
            turn_text=turn_text,
        )

        requests_list.append({
            "custom_id": s["session_id"],
            "params": {
                "model": HAIKU_MODEL,
                "max_tokens": 8192,
                "system": [
                    {
                        "type": "text",
                        "text": RUBRIC_SYSTEM,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "messages": [{"role": "user", "content": user_prompt}],
            },
        })
        included_ids.append(s["session_id"])

    if skipped_no_turns:
        print(f"  Note: {len(skipped_no_turns)} sessions skipped (no assistant turns): {skipped_no_turns}")

    if not requests_list:
        raise ValueError("No valid sessions to submit — all sessions had no assistant turns.")

    print(f"  Submitting {len(requests_list)} requests to Anthropic Message Batches API…")
    batch = client.messages.batches.create(requests=requests_list)
    batch_id: str = batch.id

    job_entry: dict[str, Any] = {
        "batch_id": batch_id,
        "provider": "anthropic",
        "submitted_at": submitted_at.isoformat(),
        "n_requests": len(requests_list),
        "session_ids": included_ids,
        "status_last_seen": batch.processing_status,
    }
    _append_batch_jobs_log(job_entry)

    return job_entry


def _poll_anthropic_batch(client: Any, batch_id: str) -> int:
    """Retrieve Anthropic batch status; download and parse results when ended.

    Returns 0 on success, 1 on retrieval error, 2 if still in progress.
    """
    try:
        batch = client.messages.batches.retrieve(batch_id)
    except Exception as e:
        print(f"ERROR: Could not retrieve batch {batch_id}: {e}", file=sys.stderr)
        return 1

    status: str = batch.processing_status
    req_counts = batch.request_counts
    print(
        f"  Batch {batch_id}: status={status}  "
        f"processing={req_counts.processing}  "
        f"succeeded={req_counts.succeeded}  "
        f"errored={req_counts.errored}"
    )

    if status != "ended":
        print("  Batch not yet complete. Re-run batch-poll later.")
        _append_batch_jobs_log({
            "batch_id": batch_id,
            "provider": "anthropic",
            "status_last_seen": status,
            "polled_at": datetime.now(UTC).isoformat(),
        })
        return 2

    # Batch ended — download and process results
    now = datetime.now(UTC)
    print("  Batch ended. Downloading results…")

    completed_count = 0
    errored_count = 0
    skipped_count = 0

    for result in client.messages.batches.results(batch_id):
        sid: str = result.custom_id
        out_path = GPT_OSS_DIR / f"{sid}.json"

        if out_path.exists():
            skipped_count += 1
            continue

        if result.result.type != "succeeded":
            print(f"  WARNING: {sid} result type={result.result.type}; skipping.")
            errored_count += 1
            continue

        msg = result.result.message
        if not msg.content:
            print(f"  WARNING: {sid} has empty content; skipping.")
            errored_count += 1
            continue

        raw_content: str = msg.content[0].text.strip()
        # Haiku wraps JSON in ```json ... ``` fences; strip them before parsing.
        if raw_content.startswith("```"):
            raw_content = raw_content.split("\n", 1)[-1]  # drop the opening ```json line
            if raw_content.endswith("```"):
                raw_content = raw_content[: raw_content.rfind("```")].rstrip()
        try:
            annotation = json.loads(raw_content)
        except json.JSONDecodeError as e:
            print(f"  WARNING: JSON parse error for {sid}: {e}")
            errored_count += 1
            continue

        usage = msg.usage
        in_t: int = usage.input_tokens
        out_t: int = usage.output_tokens
        cached_t: int = getattr(usage, "cache_read_input_tokens", 0)

        _, shown_indices = _format_turns(
            next((s for s in _get_sessions_cache() if s["session_id"] == sid), {})
        )

        annotation["_model"] = HAIKU_MODEL
        annotation["labeler_model"] = HAIKU_MODEL
        annotation["_shown_turn_indices"] = shown_indices if shown_indices else []
        annotation["_input_tokens"] = in_t
        annotation["_output_tokens"] = out_t
        annotation["_cached_input_tokens"] = cached_t

        out_path.write_text(json.dumps(annotation, indent=2, ensure_ascii=False), encoding="utf-8")

        uncached = max(0, in_t - cached_t)
        cost = (
            uncached * ANTHROPIC_BATCH_COST_PER_M_IN / 1e6
            + cached_t * ANTHROPIC_BATCH_COST_PER_M_IN_CACHED / 1e6
            + out_t * ANTHROPIC_BATCH_COST_PER_M_OUT / 1e6
        )
        _log_cost(sid, HAIKU_MODEL, in_t, out_t, cached_t, cost, mode="anthropic-batch", provider="anthropic")
        completed_count += 1

    _append_batch_jobs_log({
        "batch_id": batch_id,
        "provider": "anthropic",
        "status_last_seen": "ended",
        "polled_at": now.isoformat(),
    })
    print(
        f"  {completed_count} completed, {errored_count} errored, "
        f"{skipped_count} skipped (already existed)."
    )
    return 0


def _poll_batch(client: Any, batch_id: str) -> int:
    """Retrieve batch status and process results if completed.

    Args:
        client: OpenAI-compat client pointed at Groq base URL.
        batch_id: The batch ID returned by the Groq Batch API.

    Returns:
        0 on success or still-pending status; non-zero on terminal failure.
    """
    try:
        batch = client.batches.retrieve(batch_id)
    except Exception as e:
        print(f"ERROR: Could not retrieve batch {batch_id}: {e}", file=sys.stderr)
        return 1

    status: str = getattr(batch, "status", "unknown")

    # Find submitted_at from jobs log for elapsed time calculation
    entries = _read_batch_jobs_log()
    submitted_at_str: str | None = None
    for entry in entries:
        if entry.get("batch_id") == batch_id:
            submitted_at_str = entry.get("submitted_at")

    now = datetime.now(UTC)
    elapsed_str = "unknown"
    elapsed_hours: float = 0.0
    if submitted_at_str:
        try:
            submitted_at = datetime.fromisoformat(submitted_at_str)
            elapsed = now - submitted_at
            elapsed_hours = elapsed.total_seconds() / 3600
            h = int(elapsed.total_seconds() // 3600)
            m = int((elapsed.total_seconds() % 3600) // 60)
            elapsed_str = f"{h}h {m}m"
        except ValueError:
            pass
    else:
        print(
            f"  WARNING: batch_id {batch_id} not found in {BATCH_JOBS_LOG}. "
            "Proceeding without elapsed time."
        )

    if status in _ACTIVE_BATCH_STATUSES:
        req_counts = getattr(batch, "request_counts", None)
        counts_str = ""
        if req_counts is not None:
            completed = getattr(req_counts, "completed", "?")
            total = getattr(req_counts, "total", "?")
            failed = getattr(req_counts, "failed", "?")
            counts_str = f"  requests: {completed}/{total} completed, {failed} failed"

        print(f"  batch_id : {batch_id}")
        print(f"  status   : {status}")
        print(f"  elapsed  : {elapsed_str}")
        if counts_str:
            print(counts_str)

        if elapsed_hours > 24.0:
            print(
                "  WARNING: batch exceeded 24h completion window. "
                "Consider cancellation via Groq dashboard or implement batch-cancel."
            )

        _append_batch_jobs_log({
            "batch_id": batch_id,
            "status_last_seen": status,
            "polled_at": now.isoformat(),
        })
        return 0

    if status == "completed":
        output_file_id: str | None = getattr(batch, "output_file_id", None)
        error_file_id: str | None = getattr(batch, "error_file_id", None)

        completed_count = 0
        errored_count = 0
        skipped_count = 0

        if output_file_id:
            BATCH_RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
            raw_bytes: bytes = client.files.content(output_file_id).read()
            response_file = BATCH_RESPONSES_DIR / f"{batch_id}.jsonl"
            response_file.write_bytes(raw_bytes)
            print(f"  Downloaded results to {response_file}")

            for line in raw_bytes.decode("utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    resp_obj = json.loads(line)
                except json.JSONDecodeError:
                    print(f"  WARNING: could not parse response line: {line[:100]}")
                    continue

                sid: str = resp_obj.get("custom_id", "")
                out_path = GPT_OSS_DIR / f"{sid}.json"

                if out_path.exists():
                    skipped_count += 1
                    continue

                # Extract the response body — OpenAI batch format nests it under "response"
                response_wrapper = resp_obj.get("response", {})
                resp_body = response_wrapper.get("body", {})
                choices = resp_body.get("choices", [])
                usage = resp_body.get("usage", {})

                if not choices:
                    print(f"  WARNING: no choices for session {sid}; skipping.")
                    errored_count += 1
                    continue

                raw_content: str = choices[0].get("message", {}).get("content", "").strip()
                try:
                    annotation = json.loads(raw_content)
                except json.JSONDecodeError as e:
                    print(f"  WARNING: JSON parse error for {sid}: {e}")
                    errored_count += 1
                    continue

                in_t: int = usage.get("prompt_tokens", 0)
                out_t: int = usage.get("completion_tokens", 0)

                # Reconstruct shown_indices by re-running the formatter (no stored value in batch)
                _, shown_indices = _format_turns(
                    next((s for s in _get_sessions_cache() if s["session_id"] == sid), {})
                ) if sid else (None, [])

                annotation["_model"] = GROQ_MODEL
                annotation["_shown_turn_indices"] = shown_indices if shown_indices else []
                annotation["_input_tokens"] = in_t
                annotation["_output_tokens"] = out_t

                out_path.write_text(json.dumps(annotation, indent=2, ensure_ascii=False), encoding="utf-8")

                cost = _estimate_cost_batch(in_t, out_t)
                _log_cost(sid, GROQ_MODEL, in_t, out_t, 0, cost, mode="batch")
                completed_count += 1

        if error_file_id:
            err_bytes: bytes = client.files.content(error_file_id).read()
            for line in err_bytes.decode("utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    err_obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                sid = err_obj.get("custom_id", "unknown")
                err_body = err_obj.get("response", {}).get("body", {})
                err_detail = err_body.get("error", {})
                err_type = err_detail.get("type", "unknown_error")
                err_msg = err_detail.get("message", str(err_body)[:200])

                _append_skipped_log({
                    "session_id": sid,
                    "reason": f"{err_type}: {err_msg}",
                    "projected_tokens": 0,
                    "batch_id": batch_id,
                    "timestamp": now.isoformat(),
                })
                errored_count += 1

        _append_batch_jobs_log({
            "batch_id": batch_id,
            "status_last_seen": "completed",
            "polled_at": now.isoformat(),
        })
        print(f"  {completed_count} completed, {errored_count} errored, {skipped_count} skipped (already existed).")
        return 0

    # Terminal failure states
    terminal_states = {"failed", "expired", "cancelled", "cancelling"}
    if status in terminal_states:
        error_info = getattr(batch, "errors", None) or getattr(batch, "error", None)
        print(
            f"ERROR: batch {batch_id} in terminal state '{status}'.\n"
            f"  error info: {error_info}",
            file=sys.stderr,
        )
        _append_batch_jobs_log({
            "batch_id": batch_id,
            "status_last_seen": status,
            "polled_at": now.isoformat(),
        })
        return 2

    # Unknown status — treat as non-fatal
    print(f"  WARNING: unrecognized batch status '{status}' for {batch_id}.")
    _append_batch_jobs_log({
        "batch_id": batch_id,
        "status_last_seen": status,
        "polled_at": now.isoformat(),
    })
    return 0


# Module-level sessions cache so _poll_batch can reconstruct shown_indices
# without re-reading disk for each response line.  Populated in main().
_SESSIONS_CACHE: list[dict[str, Any]] = []


def _get_sessions_cache() -> list[dict[str, Any]]:
    """Return the module-level sessions list (populated by main before any poll)."""
    return _SESSIONS_CACHE


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main(args: argparse.Namespace) -> None:  # noqa: C901
    global _SESSIONS_CACHE  # noqa: PLW0603 — needed so _poll_batch can access sessions

    groq_key = os.environ.get("GROQ_API_KEY")
    anthr_key = os.environ.get("ANTHROPIC_API_KEY")

    need_groq = args.mode in ("preflight", "full", "bulk-only", "batch-submit", "batch-poll") and not getattr(args, "dry_run", False)
    need_sonnet = args.mode in ("full", "iaa-only") and not getattr(args, "dry_run", False)
    need_anthr_batch = args.mode in ("anthropic-batch-submit", "anthropic-batch-poll")

    if need_groq and not groq_key:
        print("ERROR: GROQ_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    if (need_sonnet or need_anthr_batch) and not anthr_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    # batch-poll / anthropic-batch-poll requires --batch-id
    if args.mode in ("batch-poll", "anthropic-batch-poll") and not args.batch_id:
        print("ERROR: --mode batch-poll requires --batch-id <id>", file=sys.stderr)
        sys.exit(1)

    groq_client: Any = None
    anthr_client: Any = None

    if need_groq:
        from openai import OpenAI  # type: ignore[import]
        groq_client = OpenAI(api_key=groq_key, base_url="https://api.groq.com/openai/v1")

    if need_sonnet or need_anthr_batch:
        import anthropic  # type: ignore[import]
        anthr_client = anthropic.Anthropic(api_key=anthr_key)

    # Load corpus
    sessions: list[dict[str, Any]] = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(TRACES_DIR.glob("*.json"))
    ]
    print(f"Loaded {len(sessions)} sessions.")

    # Populate module-level cache for use by _poll_batch response parsing
    _SESSIONS_CACHE = sessions

    rng = random.Random(SEED)
    iaa_ids: set[str] = set(rng.sample([s["session_id"] for s in sessions], k=IAA_N))

    total_run_cost: float = 0.0
    skip_count: int = 0

    # ── Batch submit ─────────────────────────────────────────────────────────
    if args.mode == "batch-submit":
        # Guard: refuse if an active batch exists (unless --force)
        if not args.force:
            jobs = _read_batch_jobs_log()
            # Find most recent batch_id and its last-seen status
            seen_ids: dict[str, str] = {}
            for entry in jobs:
                bid = entry.get("batch_id")
                st = entry.get("status_last_seen")
                if bid and st:
                    seen_ids[bid] = st  # later entries win

            for bid, st in seen_ids.items():
                if st in _ACTIVE_BATCH_STATUSES:
                    # Query live status
                    try:
                        live_batch = groq_client.batches.retrieve(bid)
                        live_status: str = getattr(live_batch, "status", st)
                    except Exception:
                        live_status = st

                    if live_status in _ACTIVE_BATCH_STATUSES:
                        print(
                            f"ERROR: active batch already exists: {bid} (status={live_status}).\n"
                            f"  Resume with:\n"
                            f"    python scripts/01_annotate_corpus.py --mode batch-poll --batch-id {bid}\n"
                            f"  Or re-submit with --force to create a new batch anyway.",
                            file=sys.stderr,
                        )
                        sys.exit(1)

        # Compute missing sessions
        missing = [s for s in sessions if not (GPT_OSS_DIR / f"{s['session_id']}.json").exists()]
        print(f"  Missing annotations: {len(missing)} sessions.")

        if not missing:
            print("  All sessions already annotated. Nothing to submit.")
            sys.exit(0)

        # Apply --limit: sort by session_id for determinism, then sample
        if args.limit is not None and args.limit < len(missing):
            missing_sorted = sorted(missing, key=lambda s: s["session_id"])
            limit_rng = random.Random(SEED)
            missing = limit_rng.sample(missing_sorted, k=args.limit)
            print(f"  Limiting to {len(missing)} sessions (--limit {args.limit}, seed={SEED}).")

        print(f"  Submitting {len(missing)} sessions to Groq Batch API…")
        job = _submit_batch(groq_client, missing)

        print()
        print("=" * 60)
        print("BATCH SUBMITTED")
        print(f"  batch_id   : {job['batch_id']}")
        print(f"  n_requests : {job['n_requests']}")
        print(f"  submitted_at: {job['submitted_at']}")
        print(f"  status     : {job['status_last_seen']}")
        print()
        print("RESUME COMMAND (record this externally):")
        print(f"  python scripts/01_annotate_corpus.py --mode batch-poll --batch-id {job['batch_id']}")
        print("=" * 60)
        print()

        # First poll immediately after submit (MODIFICATION B part 1)
        print("Polling immediately after submit…")
        poll_rc = _poll_batch(groq_client, job["batch_id"])
        if poll_rc != 0:
            sys.exit(poll_rc)
        sys.exit(0)

    # ── Batch poll ───────────────────────────────────────────────────────────
    if args.mode == "batch-poll":
        rc = _poll_batch(groq_client, args.batch_id)
        sys.exit(rc)

    # ── Anthropic Batch submit ────────────────────────────────────────────────
    if args.mode == "anthropic-batch-submit":
        missing = [s for s in sessions if not (GPT_OSS_DIR / f"{s['session_id']}.json").exists()]
        print(f"  Missing annotations: {len(missing)} sessions.")

        if not missing:
            print("  All sessions already annotated. Nothing to submit.")
            sys.exit(0)

        if args.limit is not None and args.limit < len(missing):
            missing_sorted = sorted(missing, key=lambda s: s["session_id"])
            limit_rng = random.Random(SEED)
            missing = limit_rng.sample(missing_sorted, k=args.limit)
            print(f"  Limiting to {len(missing)} sessions (--limit {args.limit}, seed={SEED}).")

        job = _submit_anthropic_batch(anthr_client, missing)

        print()
        print("=" * 60)
        print("ANTHROPIC BATCH SUBMITTED")
        print(f"  batch_id    : {job['batch_id']}")
        print(f"  n_requests  : {job['n_requests']}")
        print(f"  submitted_at: {job['submitted_at']}")
        print(f"  status      : {job['status_last_seen']}")
        print()
        print("RESUME COMMAND (record this externally):")
        print(f"  python scripts/01_annotate_corpus.py --mode anthropic-batch-poll --batch-id {job['batch_id']}")
        print("=" * 60)
        sys.exit(0)

    # ── Anthropic Batch poll ──────────────────────────────────────────────────
    if args.mode == "anthropic-batch-poll":
        rc = _poll_anthropic_batch(anthr_client, args.batch_id)
        sys.exit(rc)

    # ── Pre-flight ───────────────────────────────────────────────────────────
    if args.mode in ("preflight", "full"):
        pf_pool = [s for s in sessions if (GPT_OSS_DIR / f"{s['session_id']}.json").exists() is False]
        pf_sessions = rng.sample(pf_pool, k=min(PREFLIGHT_N, len(pf_pool)))

        print(f"\nPRE-FLIGHT: {GROQ_MODEL} on {len(pf_sessions)} sessions…")
        pf_costs: list[float] = []

        for i, s in enumerate(pf_sessions):
            sid = s["session_id"]
            print(f"  [{i + 1}/{len(pf_sessions)}] {sid}…", end="", flush=True)
            result = _call_groq(groq_client, s, dry_run=args.dry_run)
            if args.dry_run:
                return
            if result and "error" not in result:
                out = GPT_OSS_DIR / f"{sid}.json"
                out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                in_t = result.get("_input_tokens", 0)
                out_t = result.get("_output_tokens", 0)
                cost = _estimate_cost(result, GROQ_MODEL)
                pf_costs.append(cost)
                total_run_cost += cost
                _log_cost(sid, GROQ_MODEL, in_t, out_t, 0, cost)
                print(f" {in_t}in/{out_t}out  ${cost:.4f}")
                if not args.dry_run:
                    time.sleep(INTER_REQUEST_SLEEP_SEC)
            elif result and result.get("error") == "context_exceeded":
                print(" SKIP (>131k context)")
                skip_count += 1
            else:
                print(f" ERROR: {(result or {}).get('error', 'unknown')}")

        if pf_costs:
            avg = sum(pf_costs) / len(pf_costs)
            remaining = len(sessions) - len(pf_sessions)
            projected_gpt = avg * (remaining + len(pf_sessions))
            # Sonnet costs roughly 5–7× per token vs Groq; IAA_N = 25 sessions
            projected_sonnet = avg * 6.0 * IAA_N
            print(f"\n  avg cost/session   : ${avg:.4f}")
            print(f"  projected GPT-OSS  : ${projected_gpt:.2f} ({len(sessions)} sessions)")
            print(f"  projected Sonnet   : ${projected_sonnet:.2f} ({IAA_N} sessions, est 6× cost)")
            print(f"  TOTAL PROJECTED    : ${projected_gpt + projected_sonnet:.2f}")

            if projected_gpt > BUDGET_GPT_OSS:
                print(
                    f"\nHALT: GPT-OSS projection ${projected_gpt:.2f} > ${BUDGET_GPT_OSS} budget. "
                    "Do not proceed without sign-off."
                )
                return
            if projected_sonnet > BUDGET_SONNET:
                print(
                    f"\nHALT: Sonnet projection ${projected_sonnet:.2f} > ${BUDGET_SONNET} budget. "
                    "Do not proceed without sign-off."
                )
                return
            print("  Cost within budget. Proceeding.\n")

        if args.mode == "preflight":
            print("Pre-flight complete. Run with --mode full to annotate all sessions.")
            return

    # ── Bulk GPT-OSS annotation ──────────────────────────────────────────────
    if args.mode in ("full", "bulk-only"):
        print(f"\nBULK: {GROQ_MODEL} — {len(sessions)} sessions…")
        for i, s in enumerate(sessions):
            sid = s["session_id"]
            out = GPT_OSS_DIR / f"{sid}.json"
            if out.exists():
                print(f"  [{i + 1}/{len(sessions)}] {sid} — skip (exists)")
                continue
            label = f"{s['scaffold']}/{s['outcome']['result']}"
            print(f"  [{i + 1}/{len(sessions)}] {sid} ({label})…", end="", flush=True)

            result = _call_groq(groq_client, s, dry_run=args.dry_run)
            if args.dry_run:
                return

            if result and "error" not in result:
                out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                in_t = result.get("_input_tokens", 0)
                out_t = result.get("_output_tokens", 0)
                cost = _estimate_cost(result, GROQ_MODEL)
                total_run_cost += cost
                _log_cost(sid, GROQ_MODEL, in_t, out_t, 0, cost)
                print(f" {in_t}in/{out_t}out  ${cost:.4f}")
                time.sleep(INTER_REQUEST_SLEEP_SEC)
            elif result and result.get("error") == "context_exceeded":
                print(" SKIP (>131k context)")
                out.write_text(json.dumps(result, indent=2), encoding="utf-8")
                skip_count += 1
            else:
                err = (result or {}).get("error", "unknown")
                print(f" ERROR: {err}")
                skip_count += 1

        done = sum(1 for f in GPT_OSS_DIR.glob("*.json"))
        print(f"\nGPT-OSS complete: {done} sessions, {skip_count} skipped.")

    # ── Sonnet IAA pass ──────────────────────────────────────────────────────
    if args.mode in ("full", "iaa-only"):
        iaa_sessions = [s for s in sessions if s["session_id"] in iaa_ids]
        print(f"\nIAA: {SONNET_MODEL} — {len(iaa_sessions)} sessions…")
        for i, s in enumerate(iaa_sessions):
            sid = s["session_id"]
            out = SONNET_IAA_DIR / f"{sid}.json"
            if out.exists():
                print(f"  [{i + 1}/{len(iaa_sessions)}] {sid} — skip (exists)")
                continue
            print(f"  [{i + 1}/{len(iaa_sessions)}] {sid}…", end="", flush=True)

            result = _call_sonnet(anthr_client, s, dry_run=args.dry_run)
            if args.dry_run:
                return

            if result and "error" not in result:
                out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                in_t = result.get("_input_tokens", 0)
                out_t = result.get("_output_tokens", 0)
                cached_t = result.get("_cached_input_tokens", 0)
                cost = _estimate_cost(result, SONNET_MODEL)
                total_run_cost += cost
                _log_cost(sid, SONNET_MODEL, in_t, out_t, cached_t, cost)
                print(f" {in_t}in/{out_t}out (cached:{cached_t})  ${cost:.4f}")
                time.sleep(3)  # Anthropic has generous rate limits; short pause is enough
            else:
                err = (result or {}).get("error", "unknown")
                print(f" ERROR: {err}")

    # ── IAA kappa ─────────────────────────────────────────────────────────────
    if args.mode in ("full", "iaa-only"):
        print("\nComputing Cohen's kappa (GPT-OSS vs Sonnet)…")
        iaa = compute_iaa(GPT_OSS_DIR, SONNET_IAA_DIR)
        iaa_path = ANNOT_DIR / "iaa_phaseA1.json"
        iaa_path.write_text(json.dumps(iaa, indent=2), encoding="utf-8")
        print(json.dumps(iaa, indent=2))

        below = iaa.get("labels_below_threshold", [])
        if below:
            print(
                f"\nACTION REQUIRED: kappa < 0.6 on {below}. "
                "Hand-annotate 10 disputed turns per label and propose rubric fix."
            )

    print(f"\nTotal cost this run: ${total_run_cost:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase A.1 corpus annotation")
    parser.add_argument(
        "--mode",
        choices=[
            "preflight", "full", "bulk-only", "iaa-only",
            "batch-submit", "batch-poll",
            "anthropic-batch-submit", "anthropic-batch-poll",
        ],
        default="full",
        help=(
            "preflight=5-session cost check only; "
            "full=preflight then all+IAA; "
            "bulk-only=GPT-OSS only; "
            "iaa-only=Sonnet IAA + kappa only; "
            "batch-submit=submit missing sessions via Groq Batch API; "
            "batch-poll=poll/download a previously submitted batch; "
            "anthropic-batch-submit=submit missing sessions via Anthropic Message Batches API; "
            "anthropic-batch-poll=poll/download a previously submitted Anthropic batch"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print prompt only, no API calls")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Cap the number of missing sessions submitted in batch-submit mode",
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        metavar="ID",
        help="Batch ID to poll (required for --mode batch-poll)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow batch-submit even if an active batch exists in batch_jobs.jsonl",
    )
    _args = parser.parse_args()
    main(_args)
