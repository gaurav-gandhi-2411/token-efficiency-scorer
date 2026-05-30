"""
objective_proxy.py — Target A: deterministic objective efficiency proxy.

Formula:
  objective_efficiency_proxy = 0.25 * resolved_score
                              + 0.50 * (1 - percentile_rank(p25_token_ratio))
                              + 0.25 * (1 - percentile_rank(turn_ratio))

  where turn_ratio = turn_count / domain_median_turns

All ranks are within the 191 annotated sessions only. No API calls.
Outputs: data/objective_proxy.jsonl, config/p25_refs.yaml
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.stats import rankdata

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

LAYER1_PATH = ROOT / "data" / "layer1_outputs.jsonl"
OUTPUT_PATH = ROOT / "data" / "objective_proxy.jsonl"
REFS_PATH = ROOT / "config" / "p25_refs.yaml"
TAXONOMY_PATH = ROOT / "data" / "validation-corpus" / "taxonomy" / "task_taxonomy.json"

SEED = 42

W_RESOLVED = 0.25
W_P25 = 0.50
W_TURN = 0.25

TURN_RATIO_MIN = 0.1
TURN_RATIO_MAX = 10.0


def _load_records() -> list[dict[str, Any]]:
    """Load all annotated records from layer1_outputs.jsonl (labeler_model != 'missing')."""
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


def _percentile_ranks(values: np.ndarray) -> np.ndarray:
    """Return average-method percentile ranks in [0, 1] for each value in the array."""
    n = len(values)
    return rankdata(values, method="average") / n


def main() -> None:
    """Compute and write the objective efficiency proxy for all annotated sessions."""
    records = _load_records()
    n = len(records)
    print(f"Loaded {n} annotated sessions.")

    scaffold_map = _load_scaffold_map()

    # ---- Per-domain statistics -----------------------------------------------
    domain_tokens: dict[str, list[float]] = {}
    domain_turns: dict[str, list[int]] = {}
    for rec in records:
        did = rec["domain_id"]
        # p25_ref_tokens = total_tokens / p25_token_ratio
        domain_tokens.setdefault(did, []).append(rec["total_tokens"] / rec["p25_token_ratio"])
        domain_turns.setdefault(did, []).append(rec["turn_count"])

    domain_p25_tokens: dict[str, float] = {
        did: float(np.mean(vals)) for did, vals in domain_tokens.items()
    }
    domain_median_turns: dict[str, float] = {
        did: float(np.median(vals)) for did, vals in domain_turns.items()
    }

    # Corpus-wide fallbacks
    all_ref_tokens = [rec["total_tokens"] / rec["p25_token_ratio"] for rec in records]
    corpus_wide_p25_tokens = float(np.mean(all_ref_tokens))
    corpus_wide_median_turns = float(np.median([rec["turn_count"] for rec in records]))

    # ---- Per-session raw vectors (N=191) ------------------------------------
    p25_ratios = np.array([rec["p25_token_ratio"] for rec in records], dtype=float)
    raw_turn_ratios = np.array(
        [
            rec["turn_count"] / domain_median_turns.get(rec["domain_id"], corpus_wide_median_turns)
            for rec in records
        ],
        dtype=float,
    )
    turn_ratios = np.clip(raw_turn_ratios, TURN_RATIO_MIN, TURN_RATIO_MAX)

    # ---- Percentile ranks (lower ratio → lower rank → higher inverted score) -
    p25_ranks = _percentile_ranks(p25_ratios)
    turn_ranks = _percentile_ranks(turn_ratios)

    # ---- Build output records -----------------------------------------------
    output_records: list[dict[str, Any]] = []
    for i, rec in enumerate(records):
        sid = rec["session_id"]
        domain_id = rec["domain_id"]
        resolved = bool(rec["test_outcome"])
        scaffold = scaffold_map.get(sid, "unknown")

        resolved_score = 1.0 if resolved else 0.0
        p25_score = float(1.0 - p25_ranks[i])
        turn_score = float(1.0 - turn_ranks[i])
        proxy = W_RESOLVED * resolved_score + W_P25 * p25_score + W_TURN * turn_score

        output_records.append(
            {
                "session_id": sid,
                "scaffold": scaffold,
                "domain_id": domain_id,
                "resolved": resolved,
                "p25_token_ratio": rec["p25_token_ratio"],
                "p25_ref_tokens": round(rec["total_tokens"] / rec["p25_token_ratio"], 2),
                "turn_count": rec["turn_count"],
                "domain_median_turns": round(
                    domain_median_turns.get(domain_id, corpus_wide_median_turns), 2
                ),
                "turn_ratio": round(float(turn_ratios[i]), 4),
                "resolved_score": resolved_score,
                "p25_score": round(p25_score, 6),
                "turn_score": round(turn_score, 6),
                "objective_efficiency_proxy": round(proxy, 6),
            }
        )

    # ---- Write JSONL output -------------------------------------------------
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        for row in output_records:
            fh.write(json.dumps(row) + "\n")
    print(f"Wrote {len(output_records)} records to {OUTPUT_PATH}")

    # ---- Write YAML refs ----------------------------------------------------
    refs: dict[str, Any] = {
        "corpus_wide_p25_tokens": round(corpus_wide_p25_tokens, 2),
        "corpus_wide_median_turns": round(corpus_wide_median_turns, 2),
        "domains": {
            did: {
                "p25_tokens": round(domain_p25_tokens[did], 2),
                "median_turns": round(domain_median_turns[did], 2),
            }
            for did in sorted(domain_p25_tokens.keys())
        },
    }
    REFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REFS_PATH.write_text(
        yaml.dump(refs, default_flow_style=False, sort_keys=False), encoding="utf-8"
    )
    print(f"Wrote p25 refs to {REFS_PATH}")

    # ---- Summary ------------------------------------------------------------
    proxy_arr = np.array([r["objective_efficiency_proxy"] for r in output_records])
    print()
    print("=" * 60)
    print(f"SUMMARY: {n} sessions processed")
    print(f"Formula weights: resolved={W_RESOLVED}, p25={W_P25}, turn={W_TURN}")
    print()
    print("Per-scaffold mean proxy score:")
    scaffold_groups: dict[str, list[float]] = {}
    for row in output_records:
        scaffold_groups.setdefault(row["scaffold"], []).append(row["objective_efficiency_proxy"])
    for sc, vals in sorted(scaffold_groups.items()):
        print(f"  {sc:<22}: {np.mean(vals):.4f}  (n={len(vals)})")
    print()
    print("Proxy score distribution:")
    for label, pct in [("min", 0), ("p25", 25), ("median", 50), ("p75", 75), ("max", 100)]:
        print(f"  {label:<8}: {np.percentile(proxy_arr, pct):.4f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
