"""
investigate_findings.py — three post-scoring investigations.

Investigation 1: scaffold split (nebius 0.926 vs swegym 0.150)
Investigation 2: p25 inversion (lean sessions scoring WORSE/MUCH_WORSE)
Investigation 3: resolved collinearity (point-biserial correlation)
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent

JUDGE_PATH = ROOT / "data" / "judge_scores.jsonl"
LAYER1_PATH = ROOT / "data" / "layer1_outputs.jsonl"
CAL_PATH = ROOT / "data" / "calibration_sample.json"

VERDICT_SCORE = {
    "MUCH_BETTER": 1.00,
    "BETTER": 0.75,
    "SIMILAR": 0.50,
    "WORSE": 0.25,
    "MUCH_WORSE": 0.00,
}

# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_judge() -> dict[str, dict]:
    rows = {}
    for line in JUDGE_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["session_id"]] = r
    return rows

def load_layer1() -> dict[str, dict]:
    rows = {}
    for line in LAYER1_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["session_id"]] = r
    return rows

def load_cal() -> dict[str, dict]:
    data = json.loads(CAL_PATH.read_text(encoding="utf-8"))
    return {r["session_id"]: r for r in data}

# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

def mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else float("nan")

def stdev(vals: list[float]) -> float:
    if len(vals) < 2:
        return float("nan")
    m = mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))

def point_biserial(binary: list[int], continuous: list[float]) -> float:
    """Point-biserial correlation between a 0/1 variable and a continuous one."""
    n = len(binary)
    if n < 3:
        return float("nan")
    n1 = sum(binary)
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    m1 = mean([c for b, c in zip(binary, continuous) if b == 1])
    m0 = mean([c for b, c in zip(binary, continuous) if b == 0])
    s = stdev(continuous)
    if s == 0:
        return float("nan")
    return (m1 - m0) / s * math.sqrt(n1 * n0 / (n * n))

def spearman(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation."""
    n = len(x)
    if n < 3:
        return float("nan")
    def ranks(vals: list[float]) -> list[float]:
        sorted_vals = sorted(enumerate(vals), key=lambda t: t[1])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and sorted_vals[j + 1][1] == sorted_vals[j][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[sorted_vals[k][0]] = avg_rank
            i = j + 1
        return r
    rx, ry = ranks(x), ranks(y)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
    return 1 - 6 * d2 / (n * (n * n - 1))

# ---------------------------------------------------------------------------
# Build merged records
# ---------------------------------------------------------------------------

def build_merged(judge: dict, cal: dict, l1: dict) -> list[dict]:
    records = []
    for sid, jr in judge.items():
        cr = cal.get(sid, {})
        l1r = l1.get(sid, {})
        verdict = jr.get("verdict", "")
        score = VERDICT_SCORE.get(verdict, float("nan"))
        records.append({
            "session_id": sid,
            "verdict": verdict,
            "score": score,
            "reasoning": jr.get("reasoning", ""),
            "waste_categories": jr.get("waste_categories", []),
            "confidence": jr.get("confidence", 0.0),
            "scaffold": jr.get("scaffold") or cr.get("scaffold", "unknown"),
            "domain_id": jr.get("domain_id") or cr.get("domain_id", "unknown"),
            "resolved": cr.get("resolved"),
            "h2": cr.get("h2_duplicate_count"),
            "turn_count": cr.get("turn_count") or l1r.get("turn_count"),
            "p25_ratio": l1r.get("p25_token_ratio"),
        })
    return records


# ---------------------------------------------------------------------------
# Investigation 1 — scaffold split
# ---------------------------------------------------------------------------

def investigation_1(records: list[dict]) -> None:
    print("\n" + "=" * 90)
    print("INVESTIGATION 1 — SCAFFOLD SPLIT: nebius vs swegym")
    print("=" * 90)

    nebius = [r for r in records if r["scaffold"] == "openhands_nebius"]
    swegym = [r for r in records if r["scaffold"] == "openhands_swegym"]

    # Behavioral stats comparison
    def stats(group: list[dict], label: str) -> None:
        n = len(group)
        scores = [r["score"] for r in group if not math.isnan(r["score"])]
        h2_vals = [r["h2"] for r in group if r["h2"] is not None]
        tc_vals = [r["turn_count"] for r in group if r["turn_count"] is not None]
        res_vals = [r["resolved"] for r in group if r["resolved"] is not None]
        resolved_rate = sum(1 for v in res_vals if v) / len(res_vals) if res_vals else float("nan")
        print(f"\n  {label}  (N={n})")
        print(f"    mean_score:    {mean(scores):.3f}")
        print(f"    mean H2:       {mean(h2_vals):.1f}  (stdev {stdev(h2_vals):.1f})")
        print(f"    mean turns:    {mean(tc_vals):.1f}  (stdev {stdev(tc_vals):.1f})")
        print(f"    resolved rate: {resolved_rate:.1%}  ({sum(1 for v in res_vals if v)}/{len(res_vals)})")
        verdict_counts = defaultdict(int)
        for r in group:
            verdict_counts[r["verdict"]] += 1
        for v in ["MUCH_BETTER", "BETTER", "SIMILAR", "WORSE", "MUCH_WORSE"]:
            print(f"    {v:<12}: {verdict_counts[v]}")

    stats(nebius, "openhands_nebius")
    stats(swegym, "openhands_swegym")

    # Sample reasoning: 3 nebius MUCH_BETTER, 3 swegym MUCH_WORSE
    nebius_mb = [r for r in nebius if r["verdict"] == "MUCH_BETTER"][:3]
    swegym_mw = [r for r in swegym if r["verdict"] == "MUCH_WORSE"][:3]

    print("\n" + "-" * 90)
    print("  3 nebius MUCH_BETTER — judge reasoning")
    print("-" * 90)
    for r in nebius_mb:
        print(f"\n  Session: {r['session_id']}  turns={r['turn_count']}  H2={r['h2']}  "
              f"resolved={r['resolved']}  confidence={r['confidence']:.2f}")
        print(f"  waste: {r['waste_categories']}")
        print(f"  REASONING: {r['reasoning']}")

    print("\n" + "-" * 90)
    print("  3 swegym MUCH_WORSE — judge reasoning")
    print("-" * 90)
    for r in swegym_mw:
        print(f"\n  Session: {r['session_id']}  turns={r['turn_count']}  H2={r['h2']}  "
              f"resolved={r['resolved']}  confidence={r['confidence']:.2f}")
        print(f"  waste: {r['waste_categories']}")
        print(f"  REASONING: {r['reasoning']}")


# ---------------------------------------------------------------------------
# Investigation 2 — p25 inversion
# ---------------------------------------------------------------------------

def investigation_2(records: list[dict]) -> None:
    print("\n" + "=" * 90)
    print("INVESTIGATION 2 — p25 INVERSION: lean sessions scoring WORSE/MUCH_WORSE")
    print("=" * 90)

    lean_bad = [
        r for r in records
        if r["p25_ratio"] is not None
        and r["p25_ratio"] < 1.0
        and r["verdict"] in ("WORSE", "MUCH_WORSE")
    ]

    print(f"\n  Lean (<1.0 p25_ratio) sessions scoring WORSE/MUCH_WORSE: {len(lean_bad)}")

    # Also show lean MUCH_BETTER for contrast
    lean_good = [
        r for r in records
        if r["p25_ratio"] is not None
        and r["p25_ratio"] < 1.0
        and r["verdict"] in ("MUCH_BETTER", "BETTER")
    ]
    print(f"  Lean sessions scoring MUCH_BETTER/BETTER: {len(lean_good)}")

    print("\n" + "-" * 90)
    print(f"  5 lean WORSE/MUCH_WORSE sessions — judge reasoning")
    print("-" * 90)
    for r in lean_bad[:5]:
        print(f"\n  Session: {r['session_id']}  turns={r['turn_count']}  "
              f"p25_ratio={r['p25_ratio']:.3f}  H2={r['h2']}  "
              f"resolved={r['resolved']}  verdict={r['verdict']}  conf={r['confidence']:.2f}")
        print(f"  waste: {r['waste_categories']}")
        print(f"  REASONING: {r['reasoning']}")

    if lean_good:
        print("\n" + "-" * 90)
        print(f"  3 lean MUCH_BETTER/BETTER sessions — for contrast")
        print("-" * 90)
        for r in lean_good[:3]:
            print(f"\n  Session: {r['session_id']}  turns={r['turn_count']}  "
                  f"p25_ratio={r['p25_ratio']:.3f}  H2={r['h2']}  "
                  f"resolved={r['resolved']}  verdict={r['verdict']}  conf={r['confidence']:.2f}")
            print(f"  waste: {r['waste_categories']}")
            print(f"  REASONING: {r['reasoning']}")


# ---------------------------------------------------------------------------
# Investigation 3 — resolved collinearity
# ---------------------------------------------------------------------------

def investigation_3(records: list[dict]) -> None:
    print("\n" + "=" * 90)
    print("INVESTIGATION 3 — RESOLVED COLLINEARITY")
    print("=" * 90)

    scored = [r for r in records if r["resolved"] is not None and not math.isnan(r["score"])]
    binary = [1 if r["resolved"] else 0 for r in scored]
    scores = [r["score"] for r in scored]

    rpb = point_biserial(binary, scores)
    rsp = spearman([float(b) for b in binary], scores)

    n_res = sum(binary)
    n_unres = len(binary) - n_res
    mean_res = mean([s for b, s in zip(binary, scores) if b == 1])
    mean_unres = mean([s for b, s in zip(binary, scores) if b == 0])

    print(f"\n  N with resolved label: {len(scored)}  (resolved={n_res}, unresolved={n_unres})")
    print(f"  Mean score resolved:   {mean_res:.3f}")
    print(f"  Mean score unresolved: {mean_unres:.3f}")
    print(f"  Gap:                   {mean_res - mean_unres:.3f}")
    print(f"\n  Point-biserial r:      {rpb:.3f}")
    print(f"  Spearman rho:          {rsp:.3f}")

    if abs(rpb) > 0.7:
        verdict = "HIGH — judge substantially predicts resolution; collinearity risk in composite formula."
    elif abs(rpb) > 0.5:
        verdict = "MODERATE — meaningful correlation; worth monitoring but not blocking."
    elif abs(rpb) > 0.3:
        verdict = "LOW-MODERATE — present but not dominant."
    else:
        verdict = "LOW — minimal collinearity, judge and outcome term are largely independent."
    print(f"\n  VERDICT: {verdict}")

    # Cross-tab
    print("\n  Cross-tab: resolved × verdict")
    print(f"  {'Verdict':<14}  {'resolved=T':>10}  {'resolved=F':>10}")
    print(f"  {'-'*14}  {'-'*10}  {'-'*10}")
    res_T = [r for r in scored if r["resolved"]]
    res_F = [r for r in scored if not r["resolved"]]
    for v in ["MUCH_BETTER", "BETTER", "SIMILAR", "WORSE", "MUCH_WORSE"]:
        ct = sum(1 for r in res_T if r["verdict"] == v)
        cf = sum(1 for r in res_F if r["verdict"] == v)
        print(f"  {v:<14}  {ct:>10}  {cf:>10}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    judge = load_judge()
    cal = load_cal()
    l1 = load_layer1()
    records = build_merged(judge, cal, l1)

    print(f"Loaded {len(records)} scored sessions.")

    investigation_1(records)
    investigation_2(records)
    investigation_3(records)

    print("\n" + "=" * 90)
    print("END OF INVESTIGATIONS")
    print("=" * 90 + "\n")


if __name__ == "__main__":
    main()
