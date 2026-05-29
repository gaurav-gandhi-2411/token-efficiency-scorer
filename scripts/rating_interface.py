"""Human rating interface for the token-efficiency calibration study.

Usage:
    python scripts/rating_interface.py              # normal rating session
    python scripts/rating_interface.py --dry-run    # print sample table, then exit
    python scripts/rating_interface.py --preview SESSION_ID  # print one digest, then exit

Critical rating principle: efficiency is rated CONDITIONAL on the task, NOT task
success. A failed session can be efficient if the agent worked methodically; a
resolved session can be wasteful if it thrashed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Path bootstrap — must precede any token_efficiency imports
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from token_efficiency.layer1_features import (  # noqa: E402
    LayerOneFeatures,
    compute_domain_p25_baselines,
    extract_features,
    load_corpus,
)
from token_efficiency.trace_digest import build_digest, digest_to_text  # noqa: E402

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------
TAXONOMY_PATH = ROOT / "data" / "validation-corpus" / "taxonomy" / "task_taxonomy.json"
ANNOTATIONS_DIR = ROOT / "data" / "validation-corpus" / "annotations" / "gpt_oss"
TRACES_DIR = ROOT / "data" / "validation-corpus" / "traces_normalized"
SAMPLE_PATH = ROOT / "data" / "gold" / "sample.json"
RATINGS_PATH = ROOT / "data" / "gold" / "human_ratings.jsonl"

# ---------------------------------------------------------------------------
# Stratified allocation
# ---------------------------------------------------------------------------
_ALLOCATION: list[tuple[str, bool, int]] = [
    ("swe_agent", True, 8),
    ("swe_agent", False, 8),
    ("openhands_nebius", True, 7),
    ("openhands_nebius", False, 7),
    ("openhands_swegym", True, 5),
    ("openhands_swegym", False, 5),
]


# ---------------------------------------------------------------------------
# Sample selection helpers
# ---------------------------------------------------------------------------


def _evenly_spaced(items: list[LayerOneFeatures], n: int) -> list[LayerOneFeatures]:
    """Return n evenly-spaced elements from items (deterministic, no randomness).

    If len(items) <= n, returns all items.
    """
    if len(items) <= n:
        return list(items)
    return [items[int(i * len(items) / n)] for i in range(n)]


def _build_sample(corpus: list[LayerOneFeatures]) -> list[dict[str, Any]]:
    """Select 40 sessions via stratified evenly-spaced sampling.

    Filters out sessions where labeler_model == "missing" before sampling.
    Within each stratum (scaffold × resolved), sorts by total_tokens ascending,
    then picks n_target sessions at evenly-spaced indices.

    Returns a list of sample dicts (without token_bucket, added later).
    """
    # Index corpus by (scaffold, resolved) for O(1) group lookup.
    groups: dict[tuple[str, bool], list[LayerOneFeatures]] = {}
    for feat in corpus:
        if feat.labeler_model == "missing":
            continue
        key = (feat.scaffold, feat.test_outcome)
        groups.setdefault(key, []).append(feat)

    sample_rows: list[dict[str, Any]] = []

    for scaffold, resolved, n_target in _ALLOCATION:
        key = (scaffold, resolved)
        group = groups.get(key, [])
        # Sort by total_tokens ascending (deterministic tie-break: stable sort).
        group_sorted = sorted(group, key=lambda f: f.total_tokens)
        selected = _evenly_spaced(group_sorted, n_target)

        for feat in selected:
            domain_p25_ratio = round(feat.p25_token_ratio, 3)
            sample_rows.append(
                {
                    "session_id": feat.session_id,
                    "domain": feat.domain_id,
                    "resolved": feat.test_outcome,
                    "scaffold": feat.scaffold,
                    "total_tokens": feat.total_tokens,
                    "output_tokens_available": feat.output_tokens_available,
                    "labeler_model": feat.labeler_model,
                    "p25_token_ratio": domain_p25_ratio,
                    "token_bucket": "",  # filled in below
                }
            )

    # Compute token_bucket across the 40-session sample itself.
    tokens = [s["total_tokens"] for s in sample_rows]
    if tokens:
        p25, p50, p75 = np.percentile(tokens, [25, 50, 75])
        for s in sample_rows:
            t = s["total_tokens"]
            s["token_bucket"] = (
                "low" if t <= p25 else ("mid_low" if t <= p50 else ("mid_high" if t <= p75 else "high"))
            )

    return sample_rows


def _post_check_sample(sample: list[dict[str, Any]]) -> None:
    """Log warnings for sample quality issues (no hard failures)."""
    n = len(sample)
    if n != 40:
        print(f"[WARNING] Sample size is {n}, expected 40", file=sys.stderr)

    domain_counts: Counter[str] = Counter(s["domain"] for s in sample)
    for domain, count in domain_counts.items():
        if count > 16:
            print(f"[WARNING] Domain {domain} has {count} sessions (>16)", file=sys.stderr)

    gpt_oss_count = sum(1 for s in sample if "openai" in s["labeler_model"].lower())
    print(f"[INFO] Labeler gpt-oss count: {gpt_oss_count}", file=sys.stderr)


def _load_or_build_sample(corpus: list[LayerOneFeatures]) -> list[dict[str, Any]]:
    """Load sample.json if it exists, otherwise build and persist it."""
    if SAMPLE_PATH.exists():
        sample: list[dict[str, Any]] = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        return sample

    sample = _build_sample(corpus)
    _post_check_sample(sample)

    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_PATH.write_text(json.dumps(sample, indent=2), encoding="utf-8")
    return sample


# ---------------------------------------------------------------------------
# Confirmation table
# ---------------------------------------------------------------------------


def _print_confirmation_table(sample: list[dict[str, Any]]) -> None:
    """Print the stratified gold sample confirmation table."""
    n = len(sample)

    scaffold_counts: Counter[str] = Counter(s["scaffold"] for s in sample)
    res_count = sum(1 for s in sample if s["resolved"])
    unres_count = n - res_count
    domain_counts: Counter[str] = Counter(s["domain"] for s in sample)
    tokens_list = [s["total_tokens"] for s in sample]
    tokens_arr = np.array(tokens_list)
    tok_min = int(tokens_arr.min())
    tok_p25 = int(np.percentile(tokens_arr, 25))
    tok_median = int(np.median(tokens_arr))
    tok_p75 = int(np.percentile(tokens_arr, 75))
    tok_max = int(tokens_arr.max())
    otok_avail = sum(1 for s in sample if s["output_tokens_available"])
    otok_unavail = n - otok_avail
    gpt_oss_n = sum(1 for s in sample if "openai" in s["labeler_model"].lower())
    haiku_n = n - gpt_oss_n

    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print(f"║  STRATIFIED GOLD SAMPLE — {n} sessions{' ' * (43 - len(str(n)))}║")
    print("╚══════════════════════════════════════════════════════════════════════╝")

    swe = scaffold_counts.get("swe_agent", 0)
    neb = scaffold_counts.get("openhands_nebius", 0)
    gym = scaffold_counts.get("openhands_swegym", 0)
    print(f"Scaffold:  swe_agent={swe}  openhands_nebius={neb}  openhands_swegym={gym}")
    print(f"Resolved:  resolved={res_count}  unresolved={unres_count}")

    # Domains sorted by count descending.
    domain_parts = "  ".join(
        f"{d}={c}" for d, c in sorted(domain_counts.items(), key=lambda x: -x[1])
    )
    print(f"Domains:   {domain_parts}")
    print(
        f"Tokens:    min={tok_min}  p25={tok_p25}  median={tok_median}"
        f"  p75={tok_p75}  max={tok_max}"
    )
    print(f"OTokens:   available={otok_avail}  unavailable={otok_unavail}")
    print(f"Labeler:   gpt-oss={gpt_oss_n}  haiku={haiku_n}")
    print()

    # Table header.
    hdr = (
        f" {'#':>3}   {'session_id':<18} {'scaffold':<16} {'domain':<14}"
        f"   {'Res':<3}   {'Tokens':>6}  {'Bucket':<8}  {'OTok':<4}  {'Labeler'}"
    )
    sep = "─" * 3 + "  " + "─" * 17 + "  " + "─" * 15 + "  " + "─" * 13 + "   " + "─" * 3 + "   " + "─" * 6 + "  " + "─" * 8 + "  " + "─" * 4 + "  " + "─" * 8
    print(hdr)
    print(sep)

    for idx, s in enumerate(sample, start=1):
        sid_short = s["session_id"][:16]
        res_str = "Y" if s["resolved"] else "N"
        otok_str = "Y" if s["output_tokens_available"] else "N"
        labeler_str = "gpt-oss" if "openai" in s["labeler_model"].lower() else "haiku"
        row = (
            f" {idx:>3}   {sid_short:<18} {s['scaffold']:<16} {s['domain']:<14}"
            f"   {res_str:<3}   {s['total_tokens']:>6}  {s['token_bucket']:<8}  {otok_str:<4}  {labeler_str}"
        )
        print(row)

    print()


# ---------------------------------------------------------------------------
# Session digest loader
# ---------------------------------------------------------------------------


def _load_session_digest(
    session_id: str,
    taxonomy_index: dict[str, Any],
    domain_p25: dict[str, float],
) -> str:
    """Load raw data and return full digest_to_text — no truncation.

    Args:
        session_id:     Canonical session identifier.
        taxonomy_index: Mapping of session_id → taxonomy row dict.
        domain_p25:     Pre-computed domain → p25 baseline mapping.

    Returns:
        Full human-readable digest string.
    """
    ann_path = ANNOTATIONS_DIR / (session_id + ".json")
    trace_path = TRACES_DIR / (session_id + ".json")
    row = taxonomy_index[session_id]
    ann: dict[str, Any] | None = (
        json.loads(ann_path.read_text(encoding="utf-8")) if ann_path.exists() else None
    )
    trace: dict[str, Any] | None = (
        json.loads(trace_path.read_text(encoding="utf-8")) if trace_path.exists() else None
    )
    feat = extract_features(session_id, row, ann, trace, domain_p25)
    digest = build_digest(session_id, feat, trace, ann)
    return digest_to_text(digest)


# ---------------------------------------------------------------------------
# Ratings persistence helpers
# ---------------------------------------------------------------------------


def _load_rated_ids() -> set[str]:
    """Return the set of session_ids already present in human_ratings.jsonl."""
    if not RATINGS_PATH.exists():
        return set()
    rated: set[str] = set()
    for line in RATINGS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            obj = json.loads(line)
            rated.add(obj["session_id"])
    return rated


def _append_rating(
    session_id: str,
    domain: str,
    resolved: bool,
    scaffold: str,
    efficiency_rating: int,
    note: str,
) -> None:
    """Append one rating record to human_ratings.jsonl."""
    RATINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "session_id": session_id,
        "domain": domain,
        "resolved": resolved,
        "scaffold": scaffold,
        "efficiency_rating": efficiency_rating,
        "note": note,
        "rated_at": datetime.now(UTC).isoformat(),
        "rater": "consultant",
    }
    with RATINGS_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Rating loop
# ---------------------------------------------------------------------------

_RATING_RUBRIC = """\
──────────────────────────────────────────────────────────────────────
EFFICIENCY RATING  (rate the TRAJECTORY, not the outcome)
  1 = Very wasteful — redundant loops, thrashing, repeated failures
  2 = Mostly wasteful
  3 = Average
  4 = Mostly efficient
  5 = Very efficient — direct, minimal unnecessary steps
"""


def _run_rating_loop(
    sample: list[dict[str, Any]],
    taxonomy_index: dict[str, Any],
    domain_p25: dict[str, float],
) -> None:
    """Iterate through unrated sessions in sample order, prompting for ratings."""
    rated_ids = _load_rated_ids()
    total = len(sample)

    try:
        for pos, s in enumerate(sample, start=1):
            sid = s["session_id"]
            if sid in rated_ids:
                continue

            otok_label = "available" if s["output_tokens_available"] else "unavailable"
            print()
            print("══════════════════════════════════════════════════════════════════════")
            print(
                f"Session {pos} / {total}  │  scaffold: {s['scaffold']}  │  OTokens: {otok_label}"
            )
            print("══════════════════════════════════════════════════════════════════════")
            print()

            # Full digest — no truncation.
            digest_text = _load_session_digest(sid, taxonomy_index, domain_p25)
            print(digest_text)

            print()
            print(_RATING_RUBRIC)

            # Validated rating input.
            while True:
                raw = input("Efficiency rating [1-5]: ").strip()
                if raw in ("1", "2", "3", "4", "5"):
                    break
                print("  Please enter 1, 2, 3, 4, or 5.")

            note = input("Note (optional, press Enter to skip): ").strip()

            _append_rating(
                session_id=sid,
                domain=s["domain"],
                resolved=s["resolved"],
                scaffold=s["scaffold"],
                efficiency_rating=int(raw),
                note=note,
            )
            rated_ids.add(sid)

            # Count completed sessions (those in rated_ids that are in sample).
            n_done = sum(1 for row in sample if row["session_id"] in rated_ids)
            print("──────────────────────────────────────────────────────────────────────")
            print(f"Saved. Progress: {n_done} / {total} complete.")

    except KeyboardInterrupt:
        print("\nRating interrupted. Progress saved.")
        sys.exit(0)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Human rating interface for token-efficiency calibration study."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate/load sample, print confirmation table, then exit.",
    )
    parser.add_argument(
        "--preview",
        metavar="SESSION_ID",
        default=None,
        help="Print full digest for SESSION_ID then exit (smoke test).",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point for the rating interface."""
    # Ensure UTF-8 output on Windows terminals that default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = _parse_args()

    # Load taxonomy once.
    taxonomy: list[dict[str, Any]] = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    taxonomy_index: dict[str, Any] = {row["session_id"]: row for row in taxonomy}
    domain_p25: dict[str, float] = compute_domain_p25_baselines(taxonomy)

    # --preview: load and print one digest, then exit.
    if args.preview is not None:
        sid = args.preview
        if sid not in taxonomy_index:
            print(f"[ERROR] Session '{sid}' not found in taxonomy.", file=sys.stderr)
            sys.exit(1)
        print(_load_session_digest(sid, taxonomy_index, domain_p25))
        sys.exit(0)

    # Load or build stratified sample.
    corpus = load_corpus(TAXONOMY_PATH, ANNOTATIONS_DIR, TRACES_DIR)
    sample = _load_or_build_sample(corpus)

    # Print confirmation table (always).
    _print_confirmation_table(sample)

    # --dry-run: exit after table, no prompt.
    if args.dry_run:
        sys.exit(0)

    # Ask user to confirm.
    try:
        answer = input("Confirm this sample and proceed to rating? [y/N]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nExiting. Re-run to start rating.")
        sys.exit(0)

    if answer not in ("y", "Y"):
        print("Exiting. Re-run to start rating.")
        sys.exit(0)

    # Run the rating loop.
    _run_rating_loop(sample, taxonomy_index, domain_p25)


if __name__ == "__main__":
    main()
