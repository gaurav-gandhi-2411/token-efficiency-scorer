"""
llm_rater.py — Target B: LLM provisional rating via Anthropic Batch API.

Model: claude-sonnet-4-6.
Modes:
  --mode preflight (default): 5 sessions sync, print per-session cost + projected spend
  --mode batch-submit: submit 191-session Anthropic Message Batch
  --mode batch-poll --batch-id <id>: poll status, download when complete

Output: data/llm_provisional_ratings.jsonl
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from token_efficiency.trace_digest import SessionDigest, TurnDigest, digest_to_text  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
LAYER1_PATH = ROOT / "data" / "layer1_outputs.jsonl"
RATINGS_PATH = ROOT / "data" / "llm_provisional_ratings.jsonl"
COST_LOG = ROOT / "data" / "cost-log.jsonl"
BATCH_JOBS_LOG = ROOT / "data" / "batch_jobs.jsonl"

# ---------------------------------------------------------------------------
# Pricing constants (USD per million tokens)
# ---------------------------------------------------------------------------
SONNET_MODEL = "claude-sonnet-4-6"
SONNET_BATCH_COST_PER_M_IN: float = 1.50
SONNET_BATCH_COST_PER_M_OUT: float = 7.50
SONNET_SYNC_COST_PER_M_IN: float = 3.00
SONNET_SYNC_COST_PER_M_OUT: float = 15.00

BUDGET_USD: float = 4.00
PREFLIGHT_N: int = 5
FULL_N: int = 191

# ---------------------------------------------------------------------------
# Prompts — exact wording from rating_interface.py
# ---------------------------------------------------------------------------
RATER_SYSTEM_PROMPT = """\
You are evaluating the trajectory efficiency of an AI coding agent session.

CRITICAL PRINCIPLE: Efficiency is rated CONDITIONAL on the task, NOT task success.
A failed session can be efficient if the agent worked methodically and directly.
A resolved session can be wasteful if the agent thrashed, repeated steps, or backtracked.
Rate the TRAJECTORY, not the outcome.

Respond with ONLY valid JSON — no text outside the JSON.
Output schema:
{
  "llm_provisional_rating": <integer 1 through 5>,
  "justification": "<one sentence citing specific observed trajectory behavior>"
}
"""

USER_PROMPT_TEMPLATE = """\
{digest_text}

──────────────────────────────────────────────────────────────────────
EFFICIENCY RATING  (rate the TRAJECTORY, not the outcome)
  1 = Very wasteful — redundant loops, thrashing, repeated failures
  2 = Mostly wasteful
  3 = Average
  4 = Mostly efficient
  5 = Very efficient — direct, minimal unnecessary steps
"""


# ---------------------------------------------------------------------------
# Shared digest helper
# ---------------------------------------------------------------------------


def _reconstruct_digest(d: dict[str, Any]) -> SessionDigest:
    """Reconstruct a SessionDigest from the plain dict stored in layer1_outputs.jsonl."""
    turns = [TurnDigest(**t) for t in d["turns"]]
    return SessionDigest(**{k: v for k, v in d.items() if k != "turns"}, turns=turns)


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


def _load_existing_ratings() -> set[str]:
    """Return session_ids already in llm_provisional_ratings.jsonl."""
    if not RATINGS_PATH.exists():
        return set()
    rated: set[str] = set()
    for line in RATINGS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rated.add(json.loads(line)["session_id"])
    return rated


def _cumulative_spend() -> float:
    """Sum cost_estimate_usd from cost-log.jsonl."""
    if not COST_LOG.exists():
        return 0.0
    total = 0.0
    for line in COST_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                total += float(json.loads(line).get("cost_estimate_usd", 0.0))
    return total


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------


def _log_cost(
    session_id: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    mode: str = "sync",
) -> None:
    """Append one cost entry to data/cost-log.jsonl."""
    entry: dict[str, Any] = {
        "session_id": session_id,
        "model": SONNET_MODEL,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": 0,
        "cost_estimate_usd": round(cost_usd, 6),
        "source": "llm_rater",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if mode != "sync":
        entry["mode"] = mode
    COST_LOG.parent.mkdir(parents=True, exist_ok=True)
    with COST_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _append_rating(session_id: str, rating: int, justification: str) -> None:
    """Append one rating record to llm_provisional_ratings.jsonl."""
    record = {
        "session_id": session_id,
        "llm_provisional_rating": rating,
        "justification": justification,
        "source": "llm_provisional",
        "model": SONNET_MODEL,
    }
    RATINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RATINGS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _append_batch_jobs_log(entry: dict[str, Any]) -> None:
    """Append one line to data/batch_jobs.jsonl."""
    BATCH_JOBS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with BATCH_JOBS_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_user_prompt(rec: dict[str, Any]) -> str:
    """Build the user prompt for a single session."""
    digest = _reconstruct_digest(rec["digest"])
    digest_text = digest_to_text(digest, show_stats=False)
    return USER_PROMPT_TEMPLATE.format(digest_text=digest_text)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def _run_preflight(client: Any, records: list[dict[str, Any]]) -> None:
    """Run 5 sessions synchronously, print costs, check budget."""
    pf_records = records[:PREFLIGHT_N]
    pf_costs: list[float] = []

    for i, rec in enumerate(pf_records):
        sid = rec["session_id"]
        user_prompt = _build_user_prompt(rec)
        print(f"  [{i + 1}/{PREFLIGHT_N}] {sid}...", end="", flush=True)

        try:
            msg = client.messages.create(
                model=SONNET_MODEL,
                max_tokens=512,
                system=RATER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            print(f" ERROR: {str(e)[:120]}")
            continue

        raw = msg.content[0].text.strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            print(f" ERROR: JSON parse failed: {raw[:80]}")
            continue

        rating = result.get("llm_provisional_rating")
        if not isinstance(rating, int) or rating < 1 or rating > 5:
            print(f" ERROR: invalid schema — rating={rating!r}")
            continue

        in_t: int = msg.usage.input_tokens
        out_t: int = msg.usage.output_tokens
        cost = in_t * SONNET_SYNC_COST_PER_M_IN / 1e6 + out_t * SONNET_SYNC_COST_PER_M_OUT / 1e6
        pf_costs.append(cost)
        _log_cost(sid, in_t, out_t, cost, mode="sync")
        _append_rating(sid, rating, result.get("justification", ""))
        print(f" rating={rating}  {in_t}in/{out_t}out  ${cost:.4f}")

    if not pf_costs:
        print("ERROR: no preflight sessions completed.")
        return

    avg = sum(pf_costs) / len(pf_costs)
    projected = avg * FULL_N
    cumulative = _cumulative_spend()
    remaining = BUDGET_USD - cumulative

    print()
    print(f"Preflight cost: ${sum(pf_costs):.4f} ({len(pf_costs)} sessions).")
    print(f"Projected full-run cost: ${projected:.2f} ({FULL_N} sessions).")
    print(f"Cumulative spend so far: ${cumulative:.4f}.")
    print(f"Remaining budget: ${remaining:.4f}.")

    if cumulative + projected > BUDGET_USD:
        print(
            f"WARNING: projected cumulative ${cumulative + projected:.2f} "
            f"would exceed ${BUDGET_USD:.2f} budget."
        )


def _run_batch_submit(client: Any, records: list[dict[str, Any]]) -> None:
    """Submit all 191 sessions as an Anthropic Message Batch."""
    submitted_at = datetime.now(UTC)

    requests_list: list[dict[str, Any]] = []
    for rec in records:
        user_prompt = _build_user_prompt(rec)
        requests_list.append(
            {
                "custom_id": rec["session_id"],
                "params": {
                    "model": SONNET_MODEL,
                    "max_tokens": 512,
                    "system": RATER_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            }
        )

    print(f"Submitting {len(requests_list)} requests to Anthropic Message Batches API...")
    batch = client.messages.batches.create(requests=requests_list)
    batch_id: str = batch.id

    job_entry: dict[str, Any] = {
        "batch_id": batch_id,
        "provider": "anthropic",
        "purpose": "llm_rater",
        "submitted_at": submitted_at.isoformat(),
        "n_requests": len(requests_list),
        "session_ids": [rec["session_id"] for rec in records],
        "status_last_seen": batch.processing_status,
    }
    _append_batch_jobs_log(job_entry)

    print()
    print("=" * 60)
    print("BATCH SUBMITTED")
    print(f"  batch_id   : {batch_id}")
    print(f"  n_requests : {len(requests_list)}")
    print(f"  status     : {batch.processing_status}")
    print()
    print("RESUME COMMAND:")
    print(f"  python scripts/llm_rater.py --mode batch-poll --batch-id {batch_id}")
    print("=" * 60)


def _run_batch_poll(client: Any, batch_id: str) -> None:
    """Poll batch status and download results when complete."""
    try:
        batch = client.messages.batches.retrieve(batch_id)
    except Exception as e:
        print(f"ERROR: Could not retrieve batch {batch_id}: {e}", file=sys.stderr)
        sys.exit(1)

    status: str = batch.processing_status
    rc = batch.request_counts
    print(
        f"  Batch {batch_id}: status={status}  "
        f"processing={rc.processing}  succeeded={rc.succeeded}  errored={rc.errored}"
    )

    _append_batch_jobs_log(
        {
            "batch_id": batch_id,
            "provider": "anthropic",
            "purpose": "llm_rater",
            "status_last_seen": status,
            "polled_at": datetime.now(UTC).isoformat(),
        }
    )

    if status != "ended":
        print("  Batch not yet complete. Re-run batch-poll later.")
        return

    print("  Batch ended. Downloading results...")
    existing_ids = _load_existing_ratings()
    completed = 0
    errored = 0
    skipped = 0

    for result in client.messages.batches.results(batch_id):
        sid: str = result.custom_id

        if sid in existing_ids:
            skipped += 1
            continue

        if result.result.type != "succeeded":
            print(f"  WARNING: {sid} result type={result.result.type}; skipping.")
            errored += 1
            continue

        msg = result.result.message
        if not msg.content:
            print(f"  WARNING: {sid} empty content; skipping.")
            errored += 1
            continue

        raw = msg.content[0].text.strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"  WARNING: JSON parse error for {sid}: {e}")
            errored += 1
            continue

        rating = parsed.get("llm_provisional_rating")
        if not isinstance(rating, int) or rating < 1 or rating > 5:
            print(f"  WARNING: invalid rating for {sid}: {rating!r}")
            errored += 1
            continue

        in_t: int = msg.usage.input_tokens
        out_t: int = msg.usage.output_tokens
        cost = (
            in_t * SONNET_BATCH_COST_PER_M_IN / 1e6
            + out_t * SONNET_BATCH_COST_PER_M_OUT / 1e6
        )
        _log_cost(sid, in_t, out_t, cost, mode="anthropic-batch")
        _append_rating(sid, rating, parsed.get("justification", ""))
        existing_ids.add(sid)
        completed += 1

    print(f"  {completed} completed, {errored} errored, {skipped} skipped (already existed).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="LLM provisional rater via Anthropic Batch API.")
    parser.add_argument(
        "--mode",
        choices=["preflight", "batch-submit", "batch-poll"],
        default="preflight",
        help="preflight=5-session sync check; batch-submit=submit all 191; batch-poll=download",
    )
    parser.add_argument("--batch-id", default=None, metavar="ID", help="Batch ID for batch-poll")
    return parser.parse_args()


def main() -> None:
    """Entry point."""
    import os

    args = _parse_args()

    anthr_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthr_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    if args.mode == "batch-poll" and not args.batch_id:
        print("ERROR: --mode batch-poll requires --batch-id <id>", file=sys.stderr)
        sys.exit(1)

    import anthropic  # type: ignore[import]

    client = anthropic.Anthropic(api_key=anthr_key)

    records = _load_records()
    print(f"Loaded {len(records)} annotated sessions.")

    if args.mode == "preflight":
        _run_preflight(client, records)
    elif args.mode == "batch-submit":
        _run_batch_submit(client, records)
    elif args.mode == "batch-poll":
        _run_batch_poll(client, args.batch_id)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
