"""
judge_agreement.py — Cross-model judge agreement analysis.

Compares verdicts from two LLM judges:
  - Qwen3-30B-A3B  (data/pool_judge_scores.jsonl)
  - Gemma3-27B     (data/pool_judge_scores_m2.jsonl)

Computes standard agreement metrics (exact match, adjacent match, weighted
Cohen's kappa, Spearman rho), directional divergence analysis, waste-flagging
disagreement cases, gate overlap, and reverse-gate analysis.

Writes data/judge_agreement.json and prints a human-readable summary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent

VERDICT_ORDER: list[str] = ["MUCH_WORSE", "WORSE", "SIMILAR", "BETTER", "MUCH_BETTER"]
VERDICT_TO_INT: dict[str, int] = {v: i for i, v in enumerate(VERDICT_ORDER)}

KAPPA_LABELS: list[tuple[float, str]] = [
    (0.2, "slight"),
    (0.4, "fair"),
    (0.6, "moderate"),
    (0.8, "substantial"),
    (float("inf"), "near-perfect"),
]

# Waste categories that represent flagged inefficiency
WASTE_FLAG_CATEGORIES: frozenset[str] = frozenset({"failed_retry", "redundant_read"})


def kappa_interpretation(kappa: float) -> str:
    """Map a kappa value to its standard verbal label."""
    for threshold, label in KAPPA_LABELS:
        if kappa < threshold:
            return label
    return "near-perfect"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file and return a list of dicts. Returns empty list if file missing."""
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(
                    f"WARNING: skipping malformed JSON at {path}:{lineno} — {exc}", file=sys.stderr
                )
    return records


def inner_join(
    qwen_records: list[dict[str, Any]],
    gemma_records: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    Inner-join Qwen and Gemma records on session_id.

    Returns list of (qwen_record, gemma_record) tuples for matched sessions.
    Logs count differences to stderr.
    """
    qwen_by_id: dict[str, dict[str, Any]] = {r["session_id"]: r for r in qwen_records}
    gemma_by_id: dict[str, dict[str, Any]] = {r["session_id"]: r for r in gemma_records}

    qwen_ids = set(qwen_by_id)
    gemma_ids = set(gemma_by_id)

    matched_ids = qwen_ids & gemma_ids
    only_qwen = qwen_ids - gemma_ids
    only_gemma = gemma_ids - qwen_ids

    if only_qwen:
        print(
            f"INFO: {len(only_qwen)} session(s) present only in Qwen scores (not in Gemma)",
            file=sys.stderr,
        )
    if only_gemma:
        print(
            f"INFO: {len(only_gemma)} session(s) present only in Gemma scores (not in Qwen)",
            file=sys.stderr,
        )

    return [(qwen_by_id[sid], gemma_by_id[sid]) for sid in sorted(matched_ids)]


def verdict_dist_zeroed() -> dict[str, int]:
    """Return a zeroed verdict distribution dict in canonical order."""
    return {v: 0 for v in VERDICT_ORDER}


def compute_agreement(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    qwen_total: int,
    gemma_total: int,
    turn_map: dict[str, int] = {},  # noqa: B006
) -> dict[str, Any]:
    """
    Compute all agreement metrics from matched (qwen, gemma) record pairs.

    Parameters
    ----------
    pairs:
        Inner-joined record tuples.
    qwen_total:
        Total records in the Qwen file (for n reporting).
    gemma_total:
        Total records in the Gemma file (for n reporting).
    turn_map:
        Mapping from session_id to turn_count, loaded from pool_adapted.jsonl.
        Used to populate the ``turns`` field in waste_disagreements entries.

    Returns
    -------
    dict with all output fields matching the spec JSON schema.
    """
    n_matched = len(pairs)

    # Verdict distributions over matched sessions only
    verdict_dist_qwen: dict[str, int] = verdict_dist_zeroed()
    verdict_dist_gemma: dict[str, int] = verdict_dist_zeroed()

    # Per-level breakdown: for each Qwen verdict, how did Gemma distribute?
    per_level: dict[str, dict[str, Any]] = {}
    for v in VERDICT_ORDER:
        per_level[v] = {"n": 0, "gemma_dist": verdict_dist_zeroed()}

    # Ordinal arrays for kappa / spearman
    qwen_ords: list[int] = []
    gemma_ords: list[int] = []
    qwen_scores: list[float] = []
    gemma_scores: list[float] = []

    exact_match_count = 0
    adjacent_match_count = 0

    # Directional divergence (on disagreements only)
    disagreement_directions: list[int] = []

    # Qwen-negative slice (WORSE or MUCH_WORSE)
    qneg_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    # Gate overlap: Qwen MUCH_BETTER
    n_qwen_much_better = 0
    n_gemma_strict_overlap = 0
    n_gemma_top2_overlap = 0

    # Reverse gate: Qwen WORSE or MUCH_WORSE
    n_qwen_bad = 0
    n_gemma_also_bad = 0
    n_gemma_lenient_on_bad = 0

    # Waste disagreements
    waste_disagreements: list[dict[str, Any]] = []

    for qrec, grec in pairs:
        qv = qrec["verdict"]
        gv = grec["verdict"]

        # Guard: unknown verdict labels
        if qv not in VERDICT_TO_INT or gv not in VERDICT_TO_INT:
            print(
                f"WARNING: unknown verdict qwen={qv!r} gemma={gv!r} for session "
                f"{qrec['session_id']} — skipping",
                file=sys.stderr,
            )
            continue

        qi = VERDICT_TO_INT[qv]
        gi = VERDICT_TO_INT[gv]

        verdict_dist_qwen[qv] += 1
        verdict_dist_gemma[gv] += 1
        per_level[qv]["n"] += 1
        per_level[qv]["gemma_dist"][gv] += 1

        qwen_ords.append(qi)
        gemma_ords.append(gi)
        qwen_scores.append(float(qrec.get("judge_score", 0.0)))
        gemma_scores.append(float(grec.get("judge_score", 0.0)))

        if qi == gi:
            exact_match_count += 1
        if abs(qi - gi) <= 1:
            adjacent_match_count += 1

        if qi != gi:
            # positive = Gemma more lenient (rates UP), negative = harsher
            direction = gi - qi
            disagreement_directions.append(direction)

        # Gate overlap
        if qv == "MUCH_BETTER":
            n_qwen_much_better += 1
            if gv == "MUCH_BETTER":
                n_gemma_strict_overlap += 1
            if gv in ("MUCH_BETTER", "BETTER"):
                n_gemma_top2_overlap += 1

        # Reverse gate
        if qv in ("WORSE", "MUCH_WORSE"):
            n_qwen_bad += 1
            qneg_pairs.append((qrec, grec))
            if gv in ("WORSE", "MUCH_WORSE"):
                n_gemma_also_bad += 1
            else:
                n_gemma_lenient_on_bad += 1

        # Waste disagreement: Qwen flags waste + rates bad, Gemma rates good
        qwen_waste = set(qrec.get("waste_categories") or [])
        flagged_waste = bool(qwen_waste & WASTE_FLAG_CATEGORIES)
        if flagged_waste and qv in ("WORSE", "MUCH_WORSE") and gv in ("BETTER", "MUCH_BETTER"):
            waste_disagreements.append(
                {
                    "session_id": qrec["session_id"],
                    "turns": turn_map.get(qrec["session_id"]),
                    "qwen_verdict": qv,
                    "qwen_waste_categories": list(qwen_waste),
                    "gemma_verdict": gv,
                    "qwen_reasoning": qrec.get("reasoning", ""),
                    "gemma_reasoning": grec.get("reasoning", ""),
                }
            )

    # ---------- compute scalar metrics ----------

    exact_match_pct = exact_match_count / n_matched if n_matched else 0.0
    adjacent_match_pct = adjacent_match_count / n_matched if n_matched else 0.0

    # Weighted Cohen's kappa
    weighted_kappa: float = 0.0
    kappa_label = "N/A (too few samples)"
    if n_matched >= 2 and len(set(qwen_ords)) > 1:
        try:
            from sklearn.metrics import cohen_kappa_score  # type: ignore[import-untyped]

            weighted_kappa = float(cohen_kappa_score(qwen_ords, gemma_ords, weights="quadratic"))
            kappa_label = kappa_interpretation(weighted_kappa)
        except Exception as exc:
            print(f"WARNING: kappa computation failed — {exc}", file=sys.stderr)
            kappa_label = f"error: {exc}"
    else:
        print(
            f"INFO: n_matched={n_matched}, skipping kappa (need ≥2 samples with variance)",
            file=sys.stderr,
        )

    # Spearman rho on judge_score fields
    spearman_rho: float = 0.0
    spearman_p: float = 1.0
    if n_matched >= 3:
        try:
            from scipy.stats import spearmanr  # type: ignore[import-untyped]

            result = spearmanr(qwen_scores, gemma_scores)
            spearman_rho = float(result.statistic)
            spearman_p = float(result.pvalue)
        except Exception as exc:
            print(f"WARNING: Spearman computation failed — {exc}", file=sys.stderr)

    # ---------- directional analysis ----------

    n_disagreements = len(disagreement_directions)
    mean_direction = sum(disagreement_directions) / n_disagreements if n_disagreements else 0.0
    n_gemma_lenient = sum(1 for d in disagreement_directions if d > 0)
    n_gemma_harsher = sum(1 for d in disagreement_directions if d < 0)
    pct_gemma_lenient = n_gemma_lenient / n_disagreements if n_disagreements else 0.0
    pct_gemma_harsher = n_gemma_harsher / n_disagreements if n_disagreements else 0.0

    # Qwen-negative slice
    qneg_n = len(qneg_pairs)
    qneg_also_neg = 0
    qneg_gemma_lenient = 0
    qneg_directions: list[int] = []
    for qrec, grec in qneg_pairs:
        qv = qrec["verdict"]
        gv = grec["verdict"]
        qi = VERDICT_TO_INT[qv]
        gi = VERDICT_TO_INT[gv]
        if gv in ("WORSE", "MUCH_WORSE"):
            qneg_also_neg += 1
        else:
            qneg_gemma_lenient += 1
        if qi != gi:
            qneg_directions.append(gi - qi)

    qneg_mean_direction = sum(qneg_directions) / len(qneg_directions) if qneg_directions else 0.0

    # Gate overlap percentages
    pct_strict = n_gemma_strict_overlap / n_qwen_much_better if n_qwen_much_better else 0.0
    pct_top2 = n_gemma_top2_overlap / n_qwen_much_better if n_qwen_much_better else 0.0

    # Reverse gate percentages
    pct_gemma_agrees_bad = n_gemma_also_bad / n_qwen_bad if n_qwen_bad else 0.0
    pct_gemma_lenient_on_bad = n_gemma_lenient_on_bad / n_qwen_bad if n_qwen_bad else 0.0

    # ---------- assemble output ----------

    return {
        "n": max(qwen_total, gemma_total),
        "n_matched": n_matched,
        "exact_match_pct": round(exact_match_pct, 4),
        "adjacent_match_pct": round(adjacent_match_pct, 4),
        "weighted_kappa": round(weighted_kappa, 4),
        "kappa_interpretation": kappa_label,
        "spearman_rho": round(spearman_rho, 4),
        "spearman_p": round(spearman_p, 6),
        "per_level": per_level,
        "directional": {
            "n_disagreements": n_disagreements,
            "mean_direction": round(mean_direction, 4),
            "pct_gemma_lenient": round(pct_gemma_lenient, 4),
            "pct_gemma_harsher": round(pct_gemma_harsher, 4),
            "qwen_negative_slice": {
                "n": qneg_n,
                "gemma_also_negative_n": qneg_also_neg,
                "gemma_also_negative_pct": round(qneg_also_neg / qneg_n if qneg_n else 0.0, 4),
                "gemma_lenient_n": qneg_gemma_lenient,
                "gemma_lenient_pct": round(qneg_gemma_lenient / qneg_n if qneg_n else 0.0, 4),
                "mean_direction": round(qneg_mean_direction, 4),
            },
        },
        "waste_disagreements": waste_disagreements,
        "n_waste_disagreements": len(waste_disagreements),
        "gate_overlap": {
            "n_qwen_much_better": n_qwen_much_better,
            "n_gemma_strict_overlap": n_gemma_strict_overlap,
            "pct_strict": round(pct_strict, 4),
            "n_gemma_top2_overlap": n_gemma_top2_overlap,
            "pct_top2": round(pct_top2, 4),
        },
        "reverse_gate": {
            "n_qwen_bad": n_qwen_bad,
            "n_gemma_also_bad": n_gemma_also_bad,
            "pct_gemma_agrees_bad": round(pct_gemma_agrees_bad, 4),
            "n_gemma_lenient_on_bad": n_gemma_lenient_on_bad,
            "pct_gemma_lenient_on_bad": round(pct_gemma_lenient_on_bad, 4),
        },
        "verdict_dist_qwen": verdict_dist_qwen,
        "verdict_dist_gemma": verdict_dist_gemma,
    }


def print_summary(result: dict[str, Any]) -> None:
    """Print a human-readable summary of all headline metrics to stdout."""
    sep = "-" * 60
    print(sep)
    print("Judge Agreement Summary")
    print(sep)
    print(f"Sessions (total):  {result['n']}")
    print(f"Matched sessions:  {result['n_matched']}")
    print()
    print(f"Exact match:       {result['exact_match_pct']:.1%}")
    print(f"Adjacent match:    {result['adjacent_match_pct']:.1%}")
    print(f"Weighted kappa:    {result['weighted_kappa']:.4f} ({result['kappa_interpretation']})")
    print(f"Spearman rho:      {result['spearman_rho']:.4f}  (p={result['spearman_p']:.4g})")
    print()

    print("Verdict distributions (matched sessions):")
    print(f"  {'Level':<12} {'Qwen':>6} {'Gemma':>6}")
    for v in VERDICT_ORDER:
        qn = result["verdict_dist_qwen"].get(v, 0)
        gn = result["verdict_dist_gemma"].get(v, 0)
        print(f"  {v:<12} {qn:>6} {gn:>6}")
    print()

    d = result["directional"]
    print("Directional divergence (on disagreements):")
    print(f"  N disagreements:  {d['n_disagreements']}")
    print(f"  Mean direction:   {d['mean_direction']:+.3f}  (+ = Gemma more lenient)")
    print(f"  Gemma lenient:    {d['pct_gemma_lenient']:.1%}")
    print(f"  Gemma harsher:    {d['pct_gemma_harsher']:.1%}")
    print()

    qneg = d["qwen_negative_slice"]
    print("Qwen-negative slice (Qwen rated WORSE/MUCH_WORSE):")
    print(f"  N sessions:       {qneg['n']}")
    print(
        f"  Gemma also neg:   {qneg['gemma_also_negative_n']} ({qneg['gemma_also_negative_pct']:.1%})"
    )
    print(f"  Gemma lenient:    {qneg['gemma_lenient_n']} ({qneg['gemma_lenient_pct']:.1%})")
    print(f"  Mean direction:   {qneg['mean_direction']:+.3f}")
    print()

    go = result["gate_overlap"]
    print("Gate overlap (Qwen MUCH_BETTER):")
    print(f"  N Qwen MUCH_BETTER:      {go['n_qwen_much_better']}")
    print(f"  Gemma strict overlap:    {go['n_gemma_strict_overlap']} ({go['pct_strict']:.1%})")
    print(f"  Gemma top-2 overlap:     {go['n_gemma_top2_overlap']} ({go['pct_top2']:.1%})")
    print()

    rg = result["reverse_gate"]
    print("Reverse gate (Qwen WORSE/MUCH_WORSE):")
    print(f"  N Qwen bad:              {rg['n_qwen_bad']}")
    print(f"  Gemma also bad:          {rg['n_gemma_also_bad']} ({rg['pct_gemma_agrees_bad']:.1%})")
    print(
        f"  Gemma lenient on bad:    {rg['n_gemma_lenient_on_bad']} ({rg['pct_gemma_lenient_on_bad']:.1%})"
    )
    print()

    print(f"Waste disagreements:  {result['n_waste_disagreements']}")
    print(sep)


def print_waste_details(result: dict[str, Any]) -> None:
    """Print full waste disagreement details to stdout."""
    items = result["waste_disagreements"]
    if not items:
        print("No waste disagreements found.")
        return
    print(f"\n=== Waste Disagreements ({len(items)}) ===\n")
    for i, item in enumerate(items, start=1):
        print(f"[{i}] session_id: {item['session_id']}")
        print(f"    turns:       {item['turns']}")
        print(f"    Qwen:        {item['qwen_verdict']}  waste={item['qwen_waste_categories']}")
        print(f"    Gemma:       {item['gemma_verdict']}")
        print(f"    Qwen reason: {item['qwen_reasoning'][:200]}")
        print(f"    Gemma reason:{item['gemma_reasoning'][:200]}")
        print()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Compute cross-model judge agreement metrics between Qwen and Gemma verdicts."
    )
    parser.add_argument(
        "--qwen-path",
        default=str(ROOT / "data" / "pool_judge_scores.jsonl"),
        help="Path to Qwen judge scores JSONL (default: data/pool_judge_scores.jsonl)",
    )
    parser.add_argument(
        "--gemma-path",
        default=str(ROOT / "data" / "pool_judge_scores_m2.jsonl"),
        help="Path to Gemma judge scores JSONL (default: data/pool_judge_scores_m2.jsonl)",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "judge_agreement.json"),
        help="Output path for agreement JSON (default: data/judge_agreement.json)",
    )
    parser.add_argument(
        "--pool-path",
        default=str(ROOT / "data" / "corpus_pool" / "pool_adapted.jsonl"),
        help="Path to pool_adapted.jsonl for turn count lookup (default: data/corpus_pool/pool_adapted.jsonl)",
    )
    parser.add_argument(
        "--print-waste-details",
        action="store_true",
        help="Print each waste disagreement session with full reasoning to stdout",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point."""
    args = parse_args(argv)

    qwen_path = Path(args.qwen_path)
    gemma_path = Path(args.gemma_path)
    output_path = Path(args.output)

    print(f"Loading Qwen scores from:  {qwen_path}", file=sys.stderr)
    print(f"Loading Gemma scores from: {gemma_path}", file=sys.stderr)

    qwen_records = load_jsonl(qwen_path)
    gemma_records = load_jsonl(gemma_path)

    print(
        f"Loaded {len(qwen_records)} Qwen records, {len(gemma_records)} Gemma records",
        file=sys.stderr,
    )

    if not qwen_records and not gemma_records:
        print("ERROR: both score files are empty or missing — nothing to compute", file=sys.stderr)
        sys.exit(1)

    pairs = inner_join(qwen_records, gemma_records)
    print(f"Inner-joined on session_id: {len(pairs)} matched sessions", file=sys.stderr)

    if len(pairs) < 2:
        print(
            f"WARNING: only {len(pairs)} matched session(s) — agreement metrics will be "
            "minimal/degenerate but no crash",
            file=sys.stderr,
        )

    pool_path = Path(args.pool_path)
    turn_map: dict[str, int] = {}
    if pool_path.exists():
        for rec in load_jsonl(pool_path):
            sid = rec.get("session_id", "")
            tc = rec.get("digest", {}).get("turn_count", 0)
            if sid:
                turn_map[sid] = tc
        print(f"Loaded turn counts for {len(turn_map)} sessions from {pool_path}", file=sys.stderr)

    result = compute_agreement(pairs, len(qwen_records), len(gemma_records), turn_map=turn_map)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print(f"Wrote {output_path}", file=sys.stderr)

    print_summary(result)

    if args.print_waste_details:
        print_waste_details(result)


if __name__ == "__main__":
    main()
