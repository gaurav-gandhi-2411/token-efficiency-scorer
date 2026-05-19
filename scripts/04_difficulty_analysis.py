"""
04_difficulty_analysis.py

Tests four structural difficulty proxies against ground-truth resolve labels.
Reports R² (logistic), point-biserial r, and AUC for each proxy, plus
pairwise Pearson correlations between proxies.

Proxies tested (all derivable without an LLM):
  P1 - patch_lines_total:  lines_added + lines_removed in outcome patch_diff
  P2 - files_changed:      number of distinct files in outcome patch_diff
  P3 - session_turn_count: total turns in the trajectory
  P4 - desc_length_tokens: whitespace-split token count of first user turn
                            (crude but zero-cost proxy for task complexity)

Outputs:
  data/validation-corpus/difficulty/difficulty_analysis.json
  data/validation-corpus/difficulty/proxy_correlations.json

Usage:
    python scripts/04_difficulty_analysis.py
"""
from __future__ import annotations

import json
import math
import pathlib
import re
from statistics import mean, stdev

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TRACES_DIR = REPO_ROOT / "data" / "validation-corpus" / "traces_normalized"
TAXONOMY_PATH = REPO_ROOT / "data" / "validation-corpus" / "taxonomy" / "task_taxonomy.json"
OUT_DIR = REPO_ROOT / "data" / "validation-corpus" / "difficulty"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Proxy extractors ──────────────────────────────────────────────────────────

def _patch_metrics(patch_diff: str | None) -> tuple[int, int]:
    """Return (lines_total, files_changed)."""
    if not patch_diff:
        return 0, 0
    added = patch_diff.count("\n+") - patch_diff.count("\n+++")
    removed = patch_diff.count("\n-") - patch_diff.count("\n---")
    files = len(re.findall(r"^diff --git", patch_diff, re.MULTILINE))
    return added + removed, files


def _first_user_token_count(turns: list[dict]) -> int:
    """Whitespace-split token count of the first non-empty user turn."""
    for t in turns:
        if t["role"] in ("user",) and len(t["content_text"]) > 50:
            return len(t["content_text"].split())
    return 0


# ── Statistics helpers ────────────────────────────────────────────────────────

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _point_biserial_r(binary_y: list[int], continuous_x: list[float]) -> float:
    """Point-biserial correlation between a 0/1 label and a continuous predictor."""
    n = len(binary_y)
    if n < 3:
        return 0.0
    n1 = sum(binary_y)
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return 0.0
    x1 = _mean([x for y, x in zip(binary_y, continuous_x) if y == 1])
    x0 = _mean([x for y, x in zip(binary_y, continuous_x) if y == 0])
    sx = stdev(continuous_x) if len(continuous_x) > 1 else 1.0
    if sx == 0:
        return 0.0
    return (x1 - x0) / sx * math.sqrt(n1 * n0 / n**2)


def _auc_from_pairs(binary_y: list[int], scores: list[float]) -> float:
    """Compute AUC (Wilcoxon-Mann-Whitney) for binary label vs. scalar score."""
    pos_scores = [s for y, s in zip(binary_y, scores) if y == 1]
    neg_scores = [s for y, s in zip(binary_y, scores) if y == 0]
    if not pos_scores or not neg_scores:
        return 0.5
    n_pos = len(pos_scores)
    n_neg = len(neg_scores)
    concordant = sum(
        (1 if p > n else 0.5 if p == n else 0)
        for p in pos_scores
        for n in neg_scores
    )
    return concordant / (n_pos * n_neg)


def _logistic_r2(binary_y: list[int], x_raw: list[float]) -> float:
    """McFadden pseudo-R² for logistic regression with a single predictor.
    Uses scipy if available; falls back to a closed-form approximation.
    """
    n = len(binary_y)
    if n < 10:
        return 0.0
    # Null log-likelihood: constant baseline model
    p_null = sum(binary_y) / n
    if p_null == 0 or p_null == 1:
        return 0.0
    ll_null = n * (p_null * math.log(p_null) + (1 - p_null) * math.log(1 - p_null))

    # Scale x to [0, 1] for numerical stability
    x_min = min(x_raw)
    x_max = max(x_raw)
    if x_max == x_min:
        return 0.0
    x = [(v - x_min) / (x_max - x_min) for v in x_raw]

    # Simple gradient-descent logistic regression: θ = [bias, coef]
    bias, coef = 0.0, 0.0
    lr = 0.1
    for _ in range(500):
        db = dc = 0.0
        for yi, xi in zip(binary_y, x):
            z = bias + coef * xi
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            err = yi - p
            db += err
            dc += err * xi
        bias += lr * db / n
        coef += lr * dc / n

    # Model log-likelihood
    ll_model = 0.0
    for yi, xi in zip(binary_y, x):
        z = bias + coef * xi
        p = max(1e-9, min(1 - 1e-9, 1 / (1 + math.exp(-max(-30, min(30, z))))))
        ll_model += yi * math.log(p) + (1 - yi) * math.log(1 - p)

    if ll_null == 0:
        return 0.0
    return round(max(0.0, 1 - ll_model / ll_null), 4)


def _pearson_r(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(xs, ys))
    denom = math.sqrt(
        sum((xi - mx) ** 2 for xi in xs) * sum((yi - my) ** 2 for yi in ys)
    )
    return round(num / denom, 4) if denom else 0.0


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    sessions = {
        f.stem.split(".")[0]: json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(TRACES_DIR.glob("*.json"))
    }
    taxonomy = {r["session_id"]: r for r in json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))}

    rows: list[dict] = []
    for sid, s in sessions.items():
        resolved = int(s["outcome"]["result"] in ("pass", "resolved", True, "true", "1"))
        patch_lines, files_changed = _patch_metrics(s["outcome"].get("patch_diff"))
        turn_count = s["turn_count"]
        desc_tokens = _first_user_token_count(s["turns"])
        tok = s.get("session_token_totals") or {}
        total_tokens = tok.get("total", 0) or 0

        rows.append({
            "session_id": sid,
            "resolved": resolved,
            "scaffold": s["scaffold"],
            "domain": taxonomy.get(sid, {}).get("domain", "unknown"),
            "p1_patch_lines": patch_lines,
            "p2_files_changed": files_changed,
            "p3_turn_count": turn_count,
            "p4_desc_tokens": desc_tokens,
            "total_tokens": total_tokens,
        })

    n = len(rows)
    y = [r["resolved"] for r in rows]
    proxies = [
        ("P1_patch_lines",  "Patch lines total (add+del)",  [r["p1_patch_lines"] for r in rows]),
        ("P2_files_changed","Files changed in patch",        [r["p2_files_changed"] for r in rows]),
        ("P3_turn_count",   "Session turn count",            [r["p3_turn_count"] for r in rows]),
        ("P4_desc_tokens",  "Task description length (words)", [r["p4_desc_tokens"] for r in rows]),
    ]

    print(f"Difficulty analysis on {n} sessions  "
          f"({sum(y)} resolved = {sum(y)/n:.1%})\n")
    print(f"{'Proxy':<28} {'r_pb':>6} {'AUC':>6} {'R²':>6}  Note")
    print("-" * 65)

    proxy_results = []
    for name, label, x in proxies:
        rpb = round(_point_biserial_r(y, x), 3)
        auc = round(_auc_from_pairs(y, x), 3)
        r2 = _logistic_r2(y, x)
        # Higher score = "harder" or "easier"?
        # Resolved sessions tend to have larger patches (if they solved it).
        # Turn count should be higher for harder (unresolved) sessions.
        note = "higher = resolved" if name in ("P1_patch_lines", "P2_files_changed") else "higher = harder"
        print(f"  {label:<26} {rpb:>6.3f}  {auc:>5.3f}  {r2:>6.4f}  {note}")
        proxy_results.append({"name": name, "label": label, "r_pb": rpb, "auc": auc, "mcfadden_r2": r2})

    # Pairwise Pearson correlations between proxies
    print("\nPairwise Pearson r between proxies:")
    proxy_vectors = {name: x for name, _, x in proxies}
    pairs = []
    pnames = [p[0] for p in proxies]
    for i, a in enumerate(pnames):
        for b in pnames[i + 1:]:
            r = _pearson_r(proxy_vectors[a], proxy_vectors[b])
            print(f"  {a} × {b}: r={r:.3f}")
            pairs.append({"a": a, "b": b, "pearson_r": r})

    # Per-domain resolve rates (using taxonomy)
    from collections import defaultdict
    domain_stats: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "resolved": 0})
    for r in rows:
        domain_stats[r["domain"]]["n"] += 1
        domain_stats[r["domain"]]["resolved"] += r["resolved"]
    print("\nResolve rate by domain:")
    for d, st in sorted(domain_stats.items(), key=lambda x: -x[1]["resolved"] / max(x[1]["n"], 1)):
        rate = st["resolved"] / st["n"]
        print(f"  {d:<25} {st['n']:>3} sessions  {rate:.0%} resolved")

    # Write outputs
    result = {
        "n_sessions": n,
        "n_resolved": sum(y),
        "resolve_rate": round(sum(y) / n, 3),
        "proxy_results": proxy_results,
        "pairwise_correlations": pairs,
        "rows": rows,
    }
    out_path = OUT_DIR / "difficulty_analysis.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    corr_path = OUT_DIR / "proxy_correlations.json"
    corr_path.write_text(json.dumps(pairs, indent=2), encoding="utf-8")

    print(f"\nDifficulty analysis written to {out_path}")


if __name__ == "__main__":
    main()
