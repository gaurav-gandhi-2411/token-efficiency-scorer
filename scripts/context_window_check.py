"""
context_window_check.py -- Check that all 191 session digests fit inside Qwen3-8B's
context window when combined with the judge prompt scaffolding and output budget.

Run: python scripts/context_window_check.py
Does NOT modify any files. Does NOT submit anything.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from token_efficiency.layer1_features import (  # noqa: E402
    CORPUS_MEAN_RESOLVE_RATE,
    DOMAIN_RESOLVE_RATE,
)
from token_efficiency.trace_digest import SessionDigest, TurnDigest, digest_to_text  # noqa: E402

# ---------------------------------------------------------------------------
# Judge prompt template (mirrored verbatim from layer2_judge.py)
# ---------------------------------------------------------------------------

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
# Context window budget constants
# ---------------------------------------------------------------------------
QWEN3_8B_CTX_LIMIT: int = 32768  # tokens (conservative Ollama default)
OUTPUT_BUDGET: int = 300  # tokens reserved for judge JSON output
SYSTEM_OVERHEAD: int = 80  # tokens for system prompt
EFFECTIVE_INPUT_BUDGET: int = QWEN3_8B_CTX_LIMIT - OUTPUT_BUDGET - SYSTEM_OVERHEAD

# ---------------------------------------------------------------------------
# Helpers (mirrored from layer2_judge.py)
# ---------------------------------------------------------------------------


def _reconstruct_digest(d: dict[str, Any]) -> SessionDigest:
    """Reconstruct SessionDigest from plain dict stored in layer1_outputs.jsonl."""
    turns = [TurnDigest(**t) for t in d["turns"]]
    fields = {k: v for k, v in d.items() if k != "turns"}
    fields.setdefault("output_tokens_available", False)
    return SessionDigest(**fields, turns=turns)


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


def estimate_tokens_char(text: str) -> int:
    """chars / 3.5 — conservative; Qwen BPE is denser than tiktoken."""
    return int(len(text) / 3.5)


def estimate_tokens_word(text: str) -> int:
    """Word-count lower bound on token count."""
    return len(text.split())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run context-window check and print report to stdout."""
    layer1_path = ROOT / "data" / "layer1_outputs.jsonl"
    refs_path = ROOT / "config" / "p25_refs.yaml"

    refs: dict[str, Any] = yaml.safe_load(refs_path.read_text(encoding="utf-8"))

    # Load records — same filter as layer2_judge.py
    records: list[dict[str, Any]] = []
    with layer1_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("labeler_model", "missing") == "missing":
                continue
            if "digest" not in row:
                continue
            records.append(row)

    print(f"Records passing filter (has labeler_model + digest): {len(records)}")

    # Build all prompts and measure sizes
    results: list[tuple[str, int, int, int]] = []
    errors: list[str] = []

    for rec in records:
        try:
            full_prompt = _build_user_prompt(rec, refs)
            char_count = len(full_prompt)
            tok_char = estimate_tokens_char(full_prompt)
            tok_word = estimate_tokens_word(full_prompt)
            results.append((rec["session_id"], char_count, tok_char, tok_word))
        except Exception as exc:
            errors.append(f"{rec.get('session_id', '?')}: {exc}")

    if errors:
        print(f"\nWARNING: {len(errors)} prompts failed to build:")
        for err in errors:
            print(f"  {err}")

    # Sort by char_count descending
    results.sort(key=lambda x: x[1], reverse=True)

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    sep = "=" * 72
    print(f"\n{sep}")
    print("CONTEXT-WINDOW CHECK  --  Qwen3-8B / layer2_judge.py")
    print(sep)

    print(f"\nTotal sessions evaluated:  {len(results)}")

    print("\nBudget summary:")
    print(f"  Qwen3-8B context limit (conservative Ollama):  {QWEN3_8B_CTX_LIMIT:,} tokens")
    print(f"  Output budget (judge JSON response):           {OUTPUT_BUDGET:,} tokens")
    print(f"  System prompt overhead:                        {SYSTEM_OVERHEAD:,} tokens")
    print(f"  Effective input budget:                        {EFFECTIVE_INPUT_BUDGET:,} tokens")

    print("\nTop 5 sessions by full-prompt character length:")
    header = f"  {'Rank':<5} {'session_id':<52} {'chars':>8} {'tok/3.5':>8} {'tok/word':>9}"
    print(header)
    print(f"  {'-' * 5} {'-' * 52} {'-' * 8} {'-' * 8} {'-' * 9}")
    for rank, (sid, chars, tok_c, tok_w) in enumerate(results[:5], start=1):
        fits_flag = "" if tok_c <= EFFECTIVE_INPUT_BUDGET else " !"
        print(f"  {rank:<5} {sid:<52} {chars:>8,} {tok_c:>8,} {tok_w:>9,}{fits_flag}")

    # Largest prompt detail
    largest_sid, largest_chars, largest_tok_char, largest_tok_word = results[0]

    # Digest-only sizes for the largest session
    largest_rec = next(r for r in records if r["session_id"] == largest_sid)
    digest_obj = _reconstruct_digest(largest_rec["digest"])
    digest_text_only = digest_to_text(digest_obj, show_stats=False)
    digest_chars = len(digest_text_only)
    digest_tok_char = estimate_tokens_char(digest_text_only)
    digest_tok_word = estimate_tokens_word(digest_text_only)

    print("\nLargest full prompt detail:")
    print(f"  Session ID:                         {largest_sid}")
    print(f"  Full prompt character count:        {largest_chars:,}")
    print(f"  Full prompt tokens (chars/3.5):     {largest_tok_char:,}")
    print(f"  Full prompt tokens (word count):    {largest_tok_word:,}")
    print(f"  TRAJECTORY section char count:      {digest_chars:,}")
    print(f"  TRAJECTORY section tok (chars/3.5): {digest_tok_char:,}")
    print(f"  TRAJECTORY section tok (word cnt):  {digest_tok_word:,}")

    # Fit verdict
    margin_char = EFFECTIVE_INPUT_BUDGET - largest_tok_char
    margin_word = EFFECTIVE_INPUT_BUDGET - largest_tok_word
    fits_char = largest_tok_char <= EFFECTIVE_INPUT_BUDGET
    fits_word = largest_tok_word <= EFFECTIVE_INPUT_BUDGET

    print(f"\nFit check (largest prompt vs effective input budget {EFFECTIVE_INPUT_BUDGET:,}):")
    print(f"  Estimate (chars/3.5): {largest_tok_char:,} tokens  |  margin = {margin_char:+,}")
    print(f"  Estimate (word cnt):  {largest_tok_word:,} tokens  |  margin = {margin_word:+,}")

    if fits_char and fits_word:
        verdict_str = "FITS"
        print(f"\nVERDICT: {verdict_str}")
        print(
            f"  The largest prompt ({largest_tok_char:,} tok by chars/3.5) fits inside the "
            f"{EFFECTIVE_INPUT_BUDGET:,}-token effective input budget "
            f"with a margin of {margin_char:,} tokens."
        )
    elif fits_word and not fits_char:
        print("\nVERDICT: BORDERLINE")
        print(
            f"  Word-count estimate ({largest_tok_word:,}) is under budget, but "
            f"conservative chars/3.5 estimate ({largest_tok_char:,}) exceeds budget "
            f"by {abs(margin_char):,} tokens. Verify with actual Qwen tokenizer."
        )
    else:
        verdict_str = "DOES NOT FIT"
        print(f"\nVERDICT: {verdict_str}")
        print(
            f"  The largest prompt ({largest_tok_char:,} tok by chars/3.5) EXCEEDS the "
            f"{EFFECTIVE_INPUT_BUDGET:,}-token effective input budget "
            f"by {abs(margin_char):,} tokens."
        )

    # Distribution stats
    all_tok_char = [r[2] for r in results]
    n = len(all_tok_char)
    sorted_tok = sorted(all_tok_char)
    print(f"\nDistribution (full-prompt tokens, chars/3.5) across {n} sessions:")
    print(f"  Min:    {sorted_tok[0]:,}")
    print(f"  Median: {sorted_tok[n // 2]:,}")
    print(f"  P90:    {sorted_tok[int(n * 0.90)]:,}")
    print(f"  P95:    {sorted_tok[int(n * 0.95)]:,}")
    print(f"  P99:    {sorted_tok[int(n * 0.99)]:,}")
    print(f"  Max:    {sorted_tok[-1]:,}")

    over_budget = sum(1 for t in all_tok_char if t > EFFECTIVE_INPUT_BUDGET)
    print(
        f"\nSessions exceeding effective input budget "
        f"({EFFECTIVE_INPUT_BUDGET:,} tokens): {over_budget}/{n}"
    )

    print(f"\n{sep}\n")


if __name__ == "__main__":
    main()
