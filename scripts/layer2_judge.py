"""
layer2_judge.py — Layer 2: reference-based pointwise efficiency judge via Qwen3-8B/Ollama.

Reads layer1_outputs.jsonl and config/p25_refs.yaml, calls Qwen3-8B locally for each
session, and writes judge_scores.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from token_efficiency.layer1_features import (  # noqa: E402
    CORPUS_MEAN_RESOLVE_RATE,
    DOMAIN_RESOLVE_RATE,
)
from token_efficiency.trace_digest import SessionDigest, TurnDigest, digest_to_text  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
LAYER1_PATH = ROOT / "data" / "layer1_outputs.jsonl"
REFS_PATH = ROOT / "config" / "p25_refs.yaml"
OUTPUT_PATH = ROOT / "data" / "judge_scores.jsonl"
TAXONOMY_PATH = ROOT / "data" / "validation-corpus" / "taxonomy" / "task_taxonomy.json"

SEED = 42

# ---------------------------------------------------------------------------
# Structured output schema for Ollama constrained decoding
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

# ---------------------------------------------------------------------------
# Verdict mapping
# ---------------------------------------------------------------------------
VERDICT_TO_FLOAT: dict[str, float] = {
    "MUCH_BETTER": 1.00,
    "BETTER": 0.75,
    "SIMILAR": 0.50,
    "WORSE": 0.25,
    "MUCH_WORSE": 0.00,
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
JUDGE_SYSTEM_PROMPT = """\
You are a reference-based pointwise efficiency judge for AI coding agent sessions.
Rate each session's efficiency RELATIVE TO THE REFERENCE STANDARD provided.
Respond with ONLY valid JSON — no text outside the JSON.
"""

_JUDGE_USER_TEMPLATE = """\
TASK: {task_description}

DOMAIN: {domain}
REFERENCE STANDARD: A p25-efficient {domain} session uses approximately {p25_ref_tokens} total \
tokens and {median_turns} median turns. Domain baseline resolve rate: {resolve_rate:.0%}.
Reference-level sessions are characterized by: direct file edits without repeated re-reads,
no failed retries of identical commands, no repeated assistant outputs, and tool results
that influence the next action.

SESSION UNDER EVALUATION:
  Total tokens: {total_tokens} ({p25_token_ratio:.2f}x the p25 reference)
  Turn count: {turn_count}
  Cache hit rate: {cache_hit_rate:.0%}
  Duplicate turns (H2): {h2_duplicate_count}

TRAJECTORY (same view as reference raters):
{digest_text}

EVALUATION CRITERIA (apply all five in this fixed order):
C1. Token economy: how close to the p25 efficient baseline is total token spend?
C2. Turn economy: are turns advancing task state vs exploratory or redundant?
C3. Trajectory coherence: does the agent avoid unanchored backtracking and exact retries?
C4. Tool utilization: are tool results integrated into the next action or reasoning?
C5. Context discipline: does the agent avoid unnecessary re-reads and verbose tool outputs?

Rate the session's efficiency RELATIVE TO THE REFERENCE STANDARD above.
Respond with ONLY valid JSON:
{{
  "verdict": "<MUCH_BETTER|BETTER|SIMILAR|WORSE|MUCH_WORSE>",
  "waste_categories": ["<subset of: redundant_read, failed_retry, context_bloat,
    trajectory_drift, duplicate_output>"],
  "confidence": <0.0 to 1.0; use < 0.5 for ambiguous sessions>,
  "reasoning": "<1-2 sentences citing specific turn numbers>"
}}
"""


# ---------------------------------------------------------------------------
# Shared digest helper
# ---------------------------------------------------------------------------


def _reconstruct_digest(d: dict[str, Any]) -> SessionDigest:
    """Reconstruct a SessionDigest from the plain dict stored in layer1_outputs.jsonl.

    Handles records generated before output_tokens_available was added to SessionDigest
    by defaulting the field to False when absent (safe: swe_agent sessions lack it).
    """
    turns = [TurnDigest(**t) for t in d["turns"]]
    fields = {k: v for k, v in d.items() if k != "turns"}
    fields.setdefault("output_tokens_available", False)
    return SessionDigest(**fields, turns=turns)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_records() -> list[dict[str, Any]]:
    """Load all annotated records from layer1_outputs.jsonl."""
    rows: list[dict[str, Any]] = []
    with LAYER1_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return [r for r in rows if r.get("labeler_model", "missing") != "missing"]


def _load_scaffold_map() -> dict[str, str]:
    """Build session_id -> scaffold mapping from task_taxonomy.json."""
    taxonomy: list[dict[str, Any]] = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    return {row["session_id"]: str(row.get("scaffold", "unknown")) for row in taxonomy}


def _load_refs() -> dict[str, Any]:
    """Load p25_refs.yaml; raise FileNotFoundError if missing (run objective_proxy.py first)."""
    return yaml.safe_load(REFS_PATH.read_text(encoding="utf-8"))


def _load_existing_scores() -> dict[str, dict[str, Any]]:
    """Load existing judge_scores.jsonl into a session_id -> record dict."""
    if not OUTPUT_PATH.exists():
        return {}
    scores: dict[str, dict[str, Any]] = {}
    for line in OUTPUT_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rec = json.loads(line)
                scores[rec["session_id"]] = rec
            except (json.JSONDecodeError, KeyError):
                pass
    return scores


# ---------------------------------------------------------------------------
# Ollama call
# ---------------------------------------------------------------------------


def _call_ollama(
    user_prompt: str,
    ollama_url: str,
    ollama_model: str,
) -> dict[str, Any] | None:
    """Send a single judge request to Ollama; return parsed JSON or None on error."""
    try:
        response = httpx.post(
            f"{ollama_url}/api/chat",
            json={
                "model": ollama_model,
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "format": JUDGE_OUTPUT_SCHEMA,
                "options": {"temperature": 0, "seed": SEED},
            },
            timeout=180.0,
        )
        response.raise_for_status()
        raw = response.json()["message"]["content"]
        return json.loads(raw)
    except httpx.HTTPError as e:
        print(f"  HTTP error: {e}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Unexpected error: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_user_prompt(rec: dict[str, Any], refs: dict[str, Any]) -> str:
    """Build the judge user prompt for a single session."""
    digest = _reconstruct_digest(rec["digest"])
    digest_text = digest_to_text(digest, show_stats=False)

    domain_id = rec["domain_id"]
    domain_refs = refs.get("domains", {}).get(domain_id, {})
    p25_ref_tokens = int(domain_refs.get("p25_tokens", refs.get("corpus_wide_p25_tokens", 0)))
    median_turns = int(domain_refs.get("median_turns", refs.get("corpus_wide_median_turns", 0)))
    resolve_rate = DOMAIN_RESOLVE_RATE.get(domain_id, CORPUS_MEAN_RESOLVE_RATE)

    task_description = digest.task_description[:400]

    return _JUDGE_USER_TEMPLATE.format(
        task_description=task_description,
        domain=domain_id,
        p25_ref_tokens=p25_ref_tokens,
        median_turns=median_turns,
        resolve_rate=resolve_rate,
        total_tokens=rec["total_tokens"],
        p25_token_ratio=rec["p25_token_ratio"],
        turn_count=rec["turn_count"],
        cache_hit_rate=rec["cache_hit_rate"],
        h2_duplicate_count=rec["h2_duplicate_count"],
        digest_text=digest_text,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Layer 2 judge via Qwen3-8B/Ollama.")
    parser.add_argument(
        "--mode",
        choices=["full", "session"],
        default="full",
        help="full=all annotated sessions; session=single session by --session-id",
    )
    parser.add_argument("--session-id", default=None, metavar="ID")
    parser.add_argument("--model", default="qwen3:8b", metavar="MODEL")
    parser.add_argument(
        "--ollama-url", default="http://localhost:11434", metavar="URL"
    )
    parser.add_argument("--force", action="store_true", help="Re-score already-scored sessions.")
    parser.add_argument("--limit", type=int, default=None, metavar="N")
    return parser.parse_args()


def _score_session(
    rec: dict[str, Any],
    refs: dict[str, Any],
    scaffold_map: dict[str, str],
    ollama_url: str,
    ollama_model: str,
) -> dict[str, Any] | None:
    """Score a single session; return output record or None on failure."""
    user_prompt = _build_user_prompt(rec, refs)
    result = _call_ollama(user_prompt, ollama_url, ollama_model)
    if result is None:
        return None

    verdict = str(result.get("verdict", "")).upper().strip()
    if verdict not in VERDICT_TO_FLOAT:
        print(f"  WARNING: unknown verdict {verdict!r}", file=sys.stderr)
        return None

    judge_score = VERDICT_TO_FLOAT[verdict]

    # Defensive confidence parsing: model may return a string like "very_low" instead of a float.
    _CONF_STRING_MAP: dict[str, float] = {
        "very_low": 0.1,
        "low": 0.3,
        "medium": 0.5,
        "high": 0.75,
        "very_high": 0.95,
    }
    raw_conf = result.get("confidence", 0.5)
    try:
        confidence = float(raw_conf)
    except (TypeError, ValueError):
        confidence = _CONF_STRING_MAP.get(str(raw_conf).lower().strip(), 0.5)

    return {
        "session_id": rec["session_id"],
        "judge_score": judge_score,
        "verdict": verdict,
        "waste_categories": result.get("waste_categories", []),
        "confidence": confidence,
        "reasoning": str(result.get("reasoning", "")),
        "scaffold": scaffold_map.get(rec["session_id"], "unknown"),
        "domain_id": rec["domain_id"],
        "model": ollama_model,
    }


def main() -> None:
    """Entry point."""
    args = _parse_args()

    refs = _load_refs()
    records = _load_records()
    scaffold_map = _load_scaffold_map()
    existing = _load_existing_scores() if not args.force else {}

    if args.mode == "session":
        if not args.session_id:
            print("ERROR: --mode session requires --session-id", file=sys.stderr)
            sys.exit(1)
        candidates = [r for r in records if r["session_id"] == args.session_id]
        if not candidates:
            print(f"ERROR: session {args.session_id!r} not found.", file=sys.stderr)
            sys.exit(1)
        to_score = candidates
    else:
        to_score = [r for r in records if r["session_id"] not in existing]

    if args.limit is not None:
        to_score = to_score[: args.limit]

    total = len(to_score)
    print(f"Sessions to score: {total} (skipping {len(existing)} already scored)")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    completed = 0
    start_all = time.monotonic()

    # Load existing scores to re-write file intact
    all_scores: dict[str, dict[str, Any]] = dict(existing)

    for i, rec in enumerate(to_score, start=1):
        sid = rec["session_id"]
        t0 = time.monotonic()
        print(f"  [{i}/{total}] {sid}...", end="", flush=True)

        scored = _score_session(rec, refs, scaffold_map, args.ollama_url, args.model)
        elapsed = time.monotonic() - t0

        if scored is None:
            print(f" FAILED ({elapsed:.1f}s)")
            continue

        all_scores[sid] = scored
        print(
            f" {scored['verdict']} (confidence {scored['confidence']:.2f})  {elapsed:.1f}s"
        )
        completed += 1

        # Write incrementally so partial results survive interruption
        with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
            for row in all_scores.values():
                fh.write(json.dumps(row) + "\n")

    total_elapsed = time.monotonic() - start_all
    print(
        f"\nDone: {completed}/{total} scored in {total_elapsed:.1f}s. "
        f"Output: {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
