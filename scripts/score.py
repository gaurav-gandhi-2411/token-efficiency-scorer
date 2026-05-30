"""
score.py — Compose final efficiency_score per session from Layer 1 + judge scores.

Formula (§6.1 of research/05-architecture-pivot.md):
  outcome_score   = 1.0 if test_outcome else 0.0
  judge_score     = verdict_float from judge_scores.jsonl (neutral 0.5 if missing)
  h2_score        = 1.0 - h2_duplicate_count / max(turn_count, 1), clamped [0, 1]
  p25_token_ratio = from layer1_outputs.jsonl, clamped [0.3, 5.0]
  difficulty_norm = 1.0 / DOMAIN_RESOLVE_RATE.get(domain_id, CORPUS_MEAN_RESOLVE_RATE)

  composite_quality = 0.50 * outcome_score + 0.35 * judge_score + 0.15 * h2_score
  efficiency_score  = composite_quality / (p25_token_ratio * difficulty_norm)

Outputs: data/efficiency_scores.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from token_efficiency.layer1_features import (  # noqa: E402
    CORPUS_MEAN_RESOLVE_RATE,
    DOMAIN_RESOLVE_RATE,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
LAYER1_PATH = ROOT / "data" / "layer1_outputs.jsonl"
JUDGE_PATH = ROOT / "data" / "judge_scores.jsonl"
OUTPUT_PATH = ROOT / "data" / "efficiency_scores.jsonl"
TAXONOMY_PATH = ROOT / "data" / "validation-corpus" / "taxonomy" / "task_taxonomy.json"

# ---------------------------------------------------------------------------
# Formula constants
# ---------------------------------------------------------------------------
W_OUTCOME: float = 0.50
W_JUDGE: float = 0.35
W_H2: float = 0.15

P25_RATIO_MIN: float = 0.3
P25_RATIO_MAX: float = 5.0

JUDGE_NEUTRAL: float = 0.5
LOW_CONFIDENCE_THRESHOLD: float = 0.3


def _load_layer1() -> list[dict[str, Any]]:
    """Load all annotated records from layer1_outputs.jsonl."""
    rows: list[dict[str, Any]] = []
    with LAYER1_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return [r for r in rows if r.get("labeler_model", "missing") != "missing"]


def _load_judge_scores() -> dict[str, dict[str, Any]]:
    """Load judge_scores.jsonl into session_id -> record mapping."""
    if not JUDGE_PATH.exists():
        return {}
    scores: dict[str, dict[str, Any]] = {}
    for line in JUDGE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rec = json.loads(line)
                scores[rec["session_id"]] = rec
            except (json.JSONDecodeError, KeyError):
                pass
    return scores


def _load_scaffold_map() -> dict[str, str]:
    """Build session_id -> scaffold mapping from task_taxonomy.json."""
    taxonomy: list[dict[str, Any]] = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    return {row["session_id"]: str(row.get("scaffold", "unknown")) for row in taxonomy}


def _compute_h2_score(h2_duplicate_count: int, turn_count: int) -> float:
    """Compute H2 score: 1 - h2_duplicates / turns, clamped to [0, 1]."""
    raw = 1.0 - h2_duplicate_count / max(turn_count, 1)
    return float(max(0.0, min(1.0, raw)))


def _compute_efficiency_score(
    outcome_score: float,
    judge_score: float,
    h2_score: float,
    p25_token_ratio: float,
    domain_id: str,
) -> tuple[float, float, float]:
    """Compute composite_quality, difficulty_norm, and efficiency_score.

    Returns (composite_quality, difficulty_norm, efficiency_score).
    """
    composite = W_OUTCOME * outcome_score + W_JUDGE * judge_score + W_H2 * h2_score
    clamped_ratio = max(P25_RATIO_MIN, min(P25_RATIO_MAX, p25_token_ratio))
    resolve_rate = DOMAIN_RESOLVE_RATE.get(domain_id, CORPUS_MEAN_RESOLVE_RATE)
    # Guard: resolve_rate should never be 0, but clamp defensively.
    difficulty_norm = 1.0 / max(resolve_rate, 0.01)
    efficiency = composite / (clamped_ratio * difficulty_norm)
    return composite, difficulty_norm, efficiency


def main() -> None:
    """Compute efficiency scores for all annotated sessions and write output."""
    layer1 = _load_layer1()
    judge_scores = _load_judge_scores()
    scaffold_map = _load_scaffold_map()

    print(f"Layer 1 sessions: {len(layer1)}")
    print(f"Judge scores available: {len(judge_scores)}")

    output_records: list[dict[str, Any]] = []

    for rec in layer1:
        sid = rec["session_id"]
        domain_id = rec["domain_id"]
        scaffold = scaffold_map.get(sid, "unknown")

        outcome_score = 1.0 if rec["test_outcome"] else 0.0
        h2_score = _compute_h2_score(rec["h2_duplicate_count"], rec["turn_count"])

        judge_rec = judge_scores.get(sid)
        has_judge = judge_rec is not None
        if has_judge:
            judge_score = float(judge_rec["judge_score"])
            confidence = float(judge_rec.get("confidence", 1.0))
            low_confidence = confidence < LOW_CONFIDENCE_THRESHOLD
        else:
            judge_score = JUDGE_NEUTRAL
            low_confidence = True

        reliability = "LOW" if (low_confidence or not has_judge) else "OK"

        composite, difficulty_norm, efficiency = _compute_efficiency_score(
            outcome_score, judge_score, h2_score, rec["p25_token_ratio"], domain_id
        )

        output_records.append(
            {
                "session_id": sid,
                "scaffold": scaffold,
                "domain_id": domain_id,
                "outcome_score": outcome_score,
                "judge_score": round(judge_score, 4),
                "h2_score": round(h2_score, 4),
                "composite_quality": round(composite, 4),
                "p25_token_ratio": round(
                    max(P25_RATIO_MIN, min(P25_RATIO_MAX, rec["p25_token_ratio"])), 4
                ),
                "difficulty_norm": round(difficulty_norm, 4),
                "efficiency_score": round(efficiency, 4),
                "has_judge": has_judge,
                "reliability": reliability,
            }
        )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        for row in output_records:
            fh.write(json.dumps(row) + "\n")
    print(f"Wrote {len(output_records)} records to {OUTPUT_PATH}")

    # ---- Summary table -------------------------------------------------------
    scores_arr = np.array([r["efficiency_score"] for r in output_records])
    print()
    print("=" * 60)
    print(f"SUMMARY: {len(output_records)} sessions scored")
    print(
        f"  mean={np.mean(scores_arr):.4f}  median={np.median(scores_arr):.4f}  "
        f"std={np.std(scores_arr):.4f}"
    )
    print()
    print("Per-scaffold efficiency score:")
    scaffold_groups: dict[str, list[float]] = {}
    for row in output_records:
        scaffold_groups.setdefault(row["scaffold"], []).append(row["efficiency_score"])
    for sc, vals in sorted(scaffold_groups.items()):
        arr = np.array(vals)
        print(
            f"  {sc:<22}: mean={np.mean(arr):.4f}  median={np.median(arr):.4f}  (n={len(vals)})"
        )
    n_low = sum(1 for r in output_records if r["reliability"] == "LOW")
    print(f"\n  Reliability LOW: {n_low}/{len(output_records)} sessions")
    print("=" * 60)


if __name__ == "__main__":
    main()
