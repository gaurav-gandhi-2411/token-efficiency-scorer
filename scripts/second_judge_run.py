"""
second_judge_run.py — Gemma3-27B second judge for cross-model corroboration.

Scores the same 143-session pool already scored by Qwen3-30B-A3B, using IDENTICAL
prompt + schema. This tests whether verdicts are model-robust (corroboration, not
validation — that distinction is the caller's concern).

Parity guarantee: JUDGE_SYSTEM_PROMPT, _JUDGE_USER_TEMPLATE, JUDGE_OUTPUT_SCHEMA
are byte-for-byte copies from layer2_judge.py as of the B3 branch commit. Import
from layer2_judge.py is deliberately avoided so the parity is version-locked at
copy time — if Qwen's prompt drifts, Gemma's stays fixed here until an explicit
re-sync.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from token_efficiency.trace_digest import SessionDigest, TurnDigest, digest_to_text  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
POOL_PATH = ROOT / "data" / "corpus_pool" / "pool_adapted.jsonl"
QWEN_SCORES_PATH = ROOT / "data" / "pool_judge_scores.jsonl"
OUTPUT_PATH = ROOT / "data" / "pool_judge_scores_m2.jsonl"

SEED = 42

# ---------------------------------------------------------------------------
# num_predict constants — only divergence from Qwen inference config.
#
# QWEN_NUM_PREDICT = 6144  Qwen3 needs headroom for the think-chain even
#                          with /no_think (suppression is not 100% reliable on
#                          very long contexts). 6144 is the B2 production value.
#
# GEMMA_NUM_PREDICT = 2048 Gemma 3 has no think chain; the JSON response is
#                          ~150 tokens. 2048 is ample — no need for 6144.
#                          The model sees IDENTICAL prompt content; only the
#                          generation budget changes.
# ---------------------------------------------------------------------------
QWEN_NUM_PREDICT = 6144
GEMMA_NUM_PREDICT = 2048

# ---------------------------------------------------------------------------
# Structured output schema for Ollama constrained decoding
# PARITY: byte-for-byte copy from layer2_judge.py
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
# PARITY: byte-for-byte copy from layer2_judge.py
# ---------------------------------------------------------------------------
VERDICT_TO_FLOAT: dict[str, float] = {
    "MUCH_BETTER": 1.00,
    "BETTER": 0.75,
    "SIMILAR": 0.50,
    "WORSE": 0.25,
    "MUCH_WORSE": 0.00,
}

# ---------------------------------------------------------------------------
# Prompts (v3 — trajectory quality only; Layer-1 scalars stripped from prompt)
# PARITY: byte-for-byte copy from layer2_judge.py
# ---------------------------------------------------------------------------
JUDGE_SYSTEM_PROMPT = """\
You are a trajectory quality judge for AI coding agent sessions.
Your sole job: assess how purposefully and directly the agent navigated the task.
Do NOT assess token efficiency, task success, or code quality.
Rate TRAJECTORY BEHAVIOR ONLY.
Respond with ONLY valid JSON — no text outside the JSON.
"""

# /no_think is placed at the START of the user message (not system prompt) because
# Qwen3 only honours the thinking-suppression directive when it appears in a user turn.
# It is kept here for exact prompt parity with layer2_judge.py even though Gemma 3
# does not have a think chain — the token is inert on Gemma.
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

# ---------------------------------------------------------------------------
# Hardcoded validation session IDs (in order)
# Format: (session_id, expected_turn_count, qwen_verdict)
# ---------------------------------------------------------------------------
VALIDATE_SESSIONS: list[tuple[str, int, str]] = [
    ("d57f0f0e-56aa-4d3a-9637-98719c8dfe47", 18, "MUCH_BETTER"),
    ("201e333b-e619-4d6c-a3e1-e688aa734f94", 24, "WORSE"),
    ("799749e9-5922-46a1-8590-8e14af0ac990", 93, "MUCH_BETTER"),
    ("78bd2719-781f-4fbc-b802-9809eabbd4e6", 470, "WORSE"),
    ("5b65dd50-5ba3-44cc-bea1-4e4c301f2a7e", 551, "BETTER"),
]


# ---------------------------------------------------------------------------
# Shared digest helper
# PARITY: byte-for-byte copy from layer2_judge.py
# ---------------------------------------------------------------------------


def _reconstruct_digest(d: dict[str, Any]) -> SessionDigest:
    """Reconstruct a SessionDigest from the plain dict stored in pool_adapted.jsonl.

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


def _load_pool(path: Path) -> list[dict[str, Any]]:
    """Load all records from pool_adapted.jsonl."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_qwen_session_ids(path: Path) -> set[str]:
    """Return the set of session_ids that Qwen has already scored."""
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rec = json.loads(line)
                ids.add(rec["session_id"])
            except (json.JSONDecodeError, KeyError):
                pass
    return ids


def _load_existing_m2(path: Path) -> dict[str, dict[str, Any]]:
    """Load existing Gemma scores into a session_id -> record dict."""
    if not path.exists():
        return {}
    scores: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rec = json.loads(line)
                scores[rec["session_id"]] = rec
            except (json.JSONDecodeError, KeyError):
                pass
    return scores


def _load_qwen_verdicts(path: Path) -> dict[str, str]:
    """Return session_id -> Qwen verdict mapping for summary tables."""
    if not path.exists():
        return {}
    verdicts: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rec = json.loads(line)
                verdicts[rec["session_id"]] = rec.get("verdict", "?")
            except (json.JSONDecodeError, KeyError):
                pass
    return verdicts


# ---------------------------------------------------------------------------
# Ollama call
# ---------------------------------------------------------------------------


def _call_ollama(
    user_prompt: str,
    ollama_url: str,
    ollama_model: str,
    num_predict: int = GEMMA_NUM_PREDICT,
) -> tuple[dict[str, Any] | None, bool, str]:
    """Send a single judge request to Ollama via api/chat.

    Returns (parsed_result, json_valid, done_reason) where:
    - json_valid records whether the raw response parsed as valid JSON on the
      first try (no post-processing).
    - done_reason is the Ollama finish reason from the final streaming chunk.
      Typical values: "stop" (normal completion), "length" (hit num_predict
      limit — JSON may be truncated), "" (unknown).

    Uses api/chat (not api/generate) for /no_think parity with Qwen config.
    Streaming mode avoids read-timeout on long-context sessions.
    num_predict=2048 (vs Qwen's 6144): Gemma has no think chain; 2048 is ample
    for the ~150-token JSON response. Model sees IDENTICAL prompt content.

    timeout connect/write: short; read: 1800s for large-session prefill.
    """
    import json as _json  # noqa: PLC0415

    try:
        response_text = ""
        done_reason = ""
        with httpx.stream(
            "POST",
            f"{ollama_url}/api/chat",
            json={
                "model": ollama_model,
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": True,
                "format": JUDGE_OUTPUT_SCHEMA,
                "options": {
                    "temperature": 0,
                    "seed": SEED,
                    "num_ctx": 32768,
                    "num_predict": num_predict,
                },
            },
            timeout=httpx.Timeout(connect=30.0, read=1800.0, write=30.0, pool=30.0),
        ) as stream_resp:
            stream_resp.raise_for_status()
            for line in stream_resp.iter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                response_text += chunk.get("message", {}).get("content", "")
                if chunk.get("done"):
                    done_reason = chunk.get("done_reason", "")
                    break
        parsed = _json.loads(response_text)
        return parsed, True, done_reason
    except httpx.HTTPError as e:
        print(f"  HTTP error: {e}", file=sys.stderr)
        return None, False, ""
    except _json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}", file=sys.stderr)
        return None, False, ""
    except Exception as e:
        print(f"  Unexpected error: {e}", file=sys.stderr)
        return None, False, ""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_user_prompt(rec: dict[str, Any]) -> str:
    """Build the judge user prompt for a single session."""
    digest = _reconstruct_digest(rec["digest"])
    digest_text = digest_to_text(digest, show_stats=False)
    task_description = digest.task_description[:400]

    return _JUDGE_USER_TEMPLATE.format(
        task_description=task_description,
        domain=rec.get("domain_id", "unknown"),
        digest_text=digest_text,
    )


# ---------------------------------------------------------------------------
# Session scorer
# ---------------------------------------------------------------------------

_CONF_STRING_MAP: dict[str, float] = {
    "very_low": 0.1,
    "low": 0.3,
    "medium": 0.5,
    "high": 0.75,
    "very_high": 0.95,
}


def _score_session(
    rec: dict[str, Any],
    ollama_url: str,
    ollama_model: str,
    num_predict: int = GEMMA_NUM_PREDICT,
) -> dict[str, Any] | None:
    """Score a single session; return output record (including json_valid) or None on failure."""
    user_prompt = _build_user_prompt(rec)
    result, json_valid, done_reason = _call_ollama(user_prompt, ollama_url, ollama_model, num_predict)
    if result is None:
        return None

    verdict = str(result.get("verdict", "")).upper().strip()
    if verdict not in VERDICT_TO_FLOAT:
        print(f"  WARNING: unknown verdict {verdict!r}", file=sys.stderr)
        return None

    judge_score = VERDICT_TO_FLOAT[verdict]

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
        "scaffold": "unknown",
        "domain_id": rec.get("domain_id", "unknown"),
        "model": ollama_model,
        "num_predict": num_predict,
        "json_valid": json_valid,
        "done_reason": done_reason,
    }


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def mode_verify_parity(args: argparse.Namespace) -> None:
    """Print prompt/schema/config parity summary. No inference. Exit 0."""
    sep = "-" * 70

    print(sep)
    print("JUDGE_SYSTEM_PROMPT (first 500 chars):")
    print(JUDGE_SYSTEM_PROMPT[:500])

    print(sep)
    print("_JUDGE_USER_TEMPLATE (first 500 chars):")
    print(_JUDGE_USER_TEMPLATE[:500])

    print(sep)
    print("JUDGE_OUTPUT_SCHEMA (first 500 chars of JSON):")
    schema_str = json.dumps(JUDGE_OUTPUT_SCHEMA, indent=2)
    print(schema_str[:500])

    print(sep)
    print("Inference params:")
    params = {
        "model": args.model,
        "temperature": 0,
        "seed": SEED,
        "num_ctx": 32768,
        "num_predict": GEMMA_NUM_PREDICT,
        "ollama_url": args.ollama_url,
    }
    for k, v in params.items():
        print(f"  {k}: {v}")

    print(sep)
    print(f"QWEN_NUM_PREDICT  = {QWEN_NUM_PREDICT}")
    print(f"GEMMA_NUM_PREDICT = {GEMMA_NUM_PREDICT}")
    print()
    print("ONLY model name and num_predict differ from Qwen config.")
    print("  Qwen  model:       qwen3:30b-a3b   num_predict: 6144")
    print(f"  Gemma model:       {args.model}     num_predict: {GEMMA_NUM_PREDICT}")
    print(sep)
    print("verify-parity: OK")


def mode_validate(args: argparse.Namespace) -> None:
    """Score exactly 5 hardcoded sessions and print a summary table."""
    pool_by_id: dict[str, dict[str, Any]] = {
        r["session_id"]: r for r in _load_pool(POOL_PATH)
    }
    existing_m2 = _load_existing_m2(OUTPUT_PATH)
    qwen_verdicts = _load_qwen_verdicts(QWEN_SCORES_PATH)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_m2: dict[str, dict[str, Any]] = dict(existing_m2)

    results: list[dict[str, Any]] = []
    wall_start = time.monotonic()

    for n, (sid, expected_turns, qwen_verdict) in enumerate(VALIDATE_SESSIONS, start=1):
        rec = pool_by_id.get(sid)
        if rec is None:
            print(f"  [{n}/5] {sid} | NOT FOUND IN POOL — skipping", file=sys.stderr)
            continue

        actual_turns = rec.get("turn_count", 0)

        if not args.force and sid in existing_m2:
            scored = existing_m2[sid]
            elapsed = 0.0
            cached = True
        else:
            t0 = time.monotonic()
            scored = _score_session(rec, args.ollama_url, args.model, GEMMA_NUM_PREDICT)
            elapsed = time.monotonic() - t0
            cached = False

        if scored is None:
            print(
                f"  [{n}/5] {sid} | turns={actual_turns} | Qwen={qwen_verdict} | "
                f"Gemma=FAILED | {elapsed:.0f}s | json_valid=False | done_reason="
            )
            print("        Reasoning: (failed — no response)")
            results.append({
                "session_id": sid,
                "turns": actual_turns,
                "qwen": qwen_verdict,
                "gemma": "FAILED",
                "conf": None,
                "json_valid": False,
                "done_reason": "",
                "reasoning": "",
            })
            continue

        if not cached:
            all_m2[sid] = scored
            with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
                for row in all_m2.values():
                    fh.write(json.dumps(row) + "\n")

        suffix = " [cached]" if cached else f" {elapsed:.0f}s"
        done_reason = scored.get("done_reason", "")
        reasoning = scored.get("reasoning", "")
        print(
            f"  [{n}/5] {sid} | turns={actual_turns} | "
            f"Qwen={qwen_verdict} | Gemma={scored['verdict']} | "
            f"conf={scored['confidence']:.2f} |{suffix} | "
            f"json_valid={scored['json_valid']} | done_reason={done_reason}"
        )
        print(f"        Reasoning: {reasoning}")
        results.append({
            "session_id": sid,
            "turns": actual_turns,
            "qwen": qwen_verdict,
            "gemma": scored["verdict"],
            "conf": scored["confidence"],
            "json_valid": scored["json_valid"],
            "done_reason": done_reason,
            "reasoning": reasoning,
        })

    wall_elapsed = time.monotonic() - wall_start
    print()
    print(f"Wall time: {wall_elapsed:.1f}s")
    print()

    # Summary table
    col_w = 38
    print(
        f"{'session_id':<{col_w}} {'turns':>5}  {'Qwen':<12} {'Gemma':<12} "
        f"{'conf':>5}  {'done_reason':<12} json_valid"
    )
    print("-" * (col_w + 65))
    for r in results:
        conf_str = f"{r['conf']:.2f}" if r["conf"] is not None else " n/a"
        done_reason = r.get("done_reason", "")
        reasoning = r.get("reasoning", "")
        print(
            f"{r['session_id']:<{col_w}} {r['turns']:>5}  "
            f"{r['qwen']:<12} {r['gemma']:<12} {conf_str:>5}  "
            f"{done_reason:<12} {r['json_valid']}"
        )
        if reasoning:
            print(f"  Reasoning: {reasoning}")
        else:
            print("  Reasoning: (failed — no response)")
        print()

    print("VALIDATION DONE. Review verdicts above before triggering --mode run.")


def mode_run(args: argparse.Namespace) -> None:
    """Score all 143 Qwen-scored sessions not yet in pool_judge_scores_m2.jsonl."""
    pool_records = _load_pool(POOL_PATH)
    qwen_ids = _load_qwen_session_ids(QWEN_SCORES_PATH)
    existing_m2 = _load_existing_m2(OUTPUT_PATH)

    # Filter: must be in pool, must be in Qwen set, must not be already scored (unless --force)
    candidates = [
        r for r in pool_records
        if r["session_id"] in qwen_ids
        and (args.force or r["session_id"] not in existing_m2)
    ]

    if args.max_turns is not None:
        candidates = [r for r in candidates if r.get("turn_count", 0) <= args.max_turns]

    total = len(candidates)
    already = len(existing_m2) if not args.force else 0
    print(f"Sessions to score: {total} (skipping {already} already in m2)")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    all_m2: dict[str, dict[str, Any]] = {} if args.force else dict(existing_m2)

    completed = 0
    wall_start = time.monotonic()

    for i, rec in enumerate(candidates, start=1):
        sid = rec["session_id"]
        turns = rec.get("turn_count", 0)
        t0 = time.monotonic()
        print(f"  [{i}/{total}] {sid} (turns={turns})...", end="", flush=True)

        scored = _score_session(rec, args.ollama_url, args.model, GEMMA_NUM_PREDICT)
        elapsed = time.monotonic() - t0

        if scored is None:
            print(f" FAILED ({elapsed:.1f}s)")
            continue

        all_m2[sid] = scored
        print(
            f" {scored['verdict']} conf={scored['confidence']:.2f} "
            f"json_valid={scored['json_valid']}  {elapsed:.1f}s"
        )
        completed += 1

        with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
            for row in all_m2.values():
                fh.write(json.dumps(row) + "\n")

    wall_elapsed = time.monotonic() - wall_start
    print(f"\nDone: {completed}/{total} scored in {wall_elapsed:.1f}s.")
    print(f"Output: {OUTPUT_PATH}")

    # Verdict distribution
    from collections import Counter  # noqa: PLC0415

    verdicts = [r.get("verdict", "") for r in all_m2.values()]
    counts: Counter[str] = Counter(verdicts)
    total_scored = len(all_m2)
    print(f"\nVerdict distribution ({total_scored} sessions in m2):")
    for v in ["MUCH_BETTER", "BETTER", "SIMILAR", "WORSE", "MUCH_WORSE"]:
        n = counts.get(v, 0)
        pct = n / total_scored * 100 if total_scored else 0
        bar = "#" * (n // max(1, total_scored // 40))
        print(f"  {v:<12} {n:>4}  ({pct:5.1f}%)  {bar}")
    mb_b = counts.get("MUCH_BETTER", 0) + counts.get("BETTER", 0)
    print(f"\nCandidate gate (MUCH_BETTER+BETTER): {mb_b}/{total_scored} = "
          f"{mb_b / total_scored * 100:.1f}%")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Gemma3-27B second judge — cross-model corroboration for the 143-session pool."
    )
    parser.add_argument(
        "--mode",
        choices=["verify-parity", "validate", "run"],
        default="verify-parity",
        help=(
            "verify-parity: print prompt/schema/config parity summary, no inference (default); "
            "validate: score 5 hardcoded sessions; "
            "run: score all 143 Qwen-scored sessions not yet in m2 output."
        ),
    )
    parser.add_argument("--model", default="gemma3:27b", metavar="MODEL")
    parser.add_argument(
        "--ollama-url", default="http://localhost:11434", metavar="URL"
    )
    parser.add_argument("--force", action="store_true", help="Re-score already-scored sessions.")
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        metavar="N",
        help="Skip sessions with turn_count > N (default: None; all 143 are <=551).",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    args = _parse_args()

    if args.mode == "verify-parity":
        mode_verify_parity(args)
    elif args.mode == "validate":
        mode_validate(args)
    elif args.mode == "run":
        mode_run(args)
    else:
        print(f"Unknown mode: {args.mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
