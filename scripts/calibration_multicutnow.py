"""
calibration_multicutnow.py — Multi-cut calibration rho report.

Cuts computed for both comparators:
  (a) Full 67-session set
  (b) swegym empty-loop cluster EXCLUDED  ← honest headline
  (c) H2=0 subset
  (d) Per scaffold: openhands_nebius / openhands_swegym / swe_agent

Comparators:
  1. objective_efficiency_proxy  (deterministic formula)
  2. llm_provisional_rating      (1–5 LLM secondary signal)

No external dependencies — stdlib only.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent

JUDGE_PATH = ROOT / "data" / "judge_scores.jsonl"
PROXY_PATH = ROOT / "data" / "objective_proxy.jsonl"
LLM_PATH   = ROOT / "data" / "llm_provisional_ratings.jsonl"
CAL_PATH   = ROOT / "data" / "calibration_sample.json"
L1_PATH    = ROOT / "data" / "layer1_outputs.jsonl"

VERDICT_SCORE = {
    "MUCH_BETTER": 1.00, "BETTER": 0.75, "SIMILAR": 0.50,
    "WORSE": 0.25,       "MUCH_WORSE": 0.00,
}
SEED      = 42
N_BOOT    = 2000
KILL_RHO  = 0.55
TARGET_RHO = 0.75

# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

def load_records() -> list[dict]:
    judge = {r["session_id"]: r for r in _jsonl(JUDGE_PATH)}
    proxy = {r["session_id"]: r for r in _jsonl(PROXY_PATH)}
    llm   = {r["session_id"]: r for r in _jsonl(LLM_PATH)}
    cal   = {r["session_id"]: r for r in json.loads(CAL_PATH.read_text(encoding="utf-8"))}
    l1    = {r["session_id"]: r for r in _jsonl(L1_PATH)}

    rows = []
    for sid, jr in judge.items():
        verdict = jr.get("verdict", "")
        js = VERDICT_SCORE.get(verdict)
        if js is None:
            continue
        cr, l1r, pr, lr = cal.get(sid,{}), l1.get(sid,{}), proxy.get(sid,{}), llm.get(sid,{})
        rows.append({
            "session_id":  sid,
            "verdict":     verdict,
            "judge_score": js,
            "scaffold":    jr.get("scaffold") or cr.get("scaffold", "unknown"),
            "h2":          cr.get("h2_duplicate_count"),
            "turn_count":  cr.get("turn_count") or l1r.get("turn_count"),
            "resolved":    cr.get("resolved"),
            "p25_ratio":   l1r.get("p25_token_ratio"),
            "obj_proxy":   pr.get("objective_efficiency_proxy"),
            "llm_rating":  lr.get("llm_provisional_rating"),
        })
    return rows

# ---------------------------------------------------------------------------
# Spearman + bootstrap CI (stdlib only)
# ---------------------------------------------------------------------------

def _ranks(vals: list[float]) -> list[float]:
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    r = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and vals[order[j+1]] == vals[order[j]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j+1):
            r[order[k]] = avg
        i = j + 1
    return r

def spearman(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 4:
        return float("nan")
    rx, ry = _ranks(x), _ranks(y)
    d2 = sum((a - b)**2 for a, b in zip(rx, ry))
    return 1.0 - 6.0 * d2 / (n * (n*n - 1))

def bootstrap_ci(x: list[float], y: list[float], n_boot: int = N_BOOT, seed: int = SEED
                 ) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(x)
    if n < 4:
        return float("nan"), float("nan")
    pairs = list(zip(x, y))
    rhos = sorted(
        spearman(*zip(*[rng.choice(pairs) for _ in range(n)]))   # type: ignore[arg-type]
        for _ in range(n_boot)
    )
    rhos = [r for r in rhos if not math.isnan(r)]
    if len(rhos) < 20:
        return float("nan"), float("nan")
    return rhos[int(0.025 * len(rhos))], rhos[int(0.975 * len(rhos))]

# ---------------------------------------------------------------------------
# Cluster definition
# ---------------------------------------------------------------------------

def is_empty_loop(r: dict) -> bool:
    """swegym short empty-loop failure: ≤15 turns, openhands_swegym, MUCH_WORSE."""
    return (r["scaffold"] == "openhands_swegym"
            and r["turn_count"] is not None and r["turn_count"] <= 15
            and r["verdict"] == "MUCH_WORSE")

# ---------------------------------------------------------------------------
# Rho reporting helpers
# ---------------------------------------------------------------------------

def compute_rho_row(label: str, subset: list[dict], comp_key: str) -> dict:
    pairs = [(r["judge_score"], r[comp_key]) for r in subset
             if r[comp_key] is not None and not math.isnan(r["judge_score"])]
    n = len(pairs)
    if n < 4:
        return {"label": label, "n": n, "rho": float("nan"),
                "ci_lo": float("nan"), "ci_hi": float("nan")}
    xs, ys = zip(*pairs)
    rho = spearman(list(xs), list(ys))
    lo, hi = bootstrap_ci(list(xs), list(ys))
    return {"label": label, "n": n, "rho": rho, "ci_lo": lo, "ci_hi": hi}

def _bar(rho: float, width: int = 22) -> str:
    if math.isnan(rho):
        return " " * width
    filled = max(0, min(width, int((rho + 1) / 2 * width)))
    return "#" * filled + "." * (width - filled)

def print_rho_table(rows: list[dict]) -> None:
    print(f"\n  {'Cut':<42}  {'N':>3}  {'rho':>6}  {'95% CI':>18}  bar")
    print(f"  {'-'*42}  {'-'*3}  {'-'*6}  {'-'*18}  {'-'*22}")
    for r in rows:
        rho_s = f"{r['rho']:+.3f}" if not math.isnan(r["rho"]) else "  n/a"
        ci_s  = (f"[{r['ci_lo']:+.3f},{r['ci_hi']:+.3f}]"
                 if not math.isnan(r["ci_lo"]) else "         n/a      ")
        bar   = _bar(r["rho"])
        print(f"  {r['label']:<42}  {r['n']:>3}  {rho_s}  {ci_s}  |{bar}|")

def interpret(rho: float, excl_rho: float) -> str:
    if math.isnan(rho):
        return "n/a"
    drop = rho - excl_rho if not math.isnan(excl_rho) else float("nan")
    parts = []
    if rho >= TARGET_RHO:
        parts.append("TARGET MET (>=0.75)")
    elif rho >= KILL_RHO:
        parts.append("KILL CRITERION MET (>=0.55), below target")
    else:
        parts.append("BELOW KILL CRITERION (<0.55) — ESCALATE")
    if not math.isnan(drop):
        if drop > 0.15:
            parts.append(f"cluster inflates headline by {drop:+.3f} — headline=(b)")
        elif drop > 0.08:
            parts.append(f"cluster contributes {drop:+.3f} — judge still discriminates")
        else:
            parts.append(f"cluster effect minimal ({drop:+.3f}) — rho is real")
    return "; ".join(parts)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    records = load_records()
    n = len(records)

    cluster   = [r for r in records if is_empty_loop(r)]
    excl      = [r for r in records if not is_empty_loop(r)]
    h2_zero   = [r for r in records if r["h2"] == 0]
    nebius    = [r for r in records if r["scaffold"] == "openhands_nebius"]
    swegym    = [r for r in records if r["scaffold"] == "openhands_swegym"]
    swe_agent = [r for r in records if r["scaffold"] == "swe_agent"]
    neg_tail  = [r for r in records if r["verdict"] in ("WORSE", "MUCH_WORSE")]
    mw_all    = [r for r in records if r["verdict"] == "MUCH_WORSE"]
    mw_clust  = [r for r in cluster  if r["verdict"] == "MUCH_WORSE"]

    # ── Step 1: Quantify cluster ─────────────────────────────────────────────
    print("=" * 80)
    print("STEP 1 — SWEGYM EMPTY-LOOP CLUSTER QUANTIFICATION")
    print("=" * 80)
    print(f"\n  Criteria: scaffold=openhands_swegym, turns <= 15, verdict=MUCH_WORSE")
    print(f"  Cluster:        {len(cluster):>2} / {n} total        ({len(cluster)/n:.1%})")
    print(f"  Of neg tail:    {len(cluster):>2} / {len(neg_tail)} WORSE+MW  ({len(cluster)/len(neg_tail):.1%})")
    print(f"  Of MUCH_WORSE:  {len(mw_clust):>2} / {len(mw_all)} MUCH_WORSE ({len(mw_clust)/len(mw_all):.1%})")
    print()
    for r in cluster:
        p = f"{r['p25_ratio']:.3f}" if r['p25_ratio'] else "n/a"
        print(f"    {r['session_id']}  turns={r['turn_count']:>3}  H2={r['h2']}  "
              f"resolved={str(r['resolved']):<5}  p25={p}")
    print(f"\n  Excluded set: {len(excl)} sessions  |  remaining neg tail: "
          f"{len([r for r in excl if r['verdict'] in ('WORSE','MUCH_WORSE')])}")

    # ── Step 2 & 3: Rho tables ───────────────────────────────────────────────
    for comp_key, comp_name in [
        ("obj_proxy",  "Objective proxy (deterministic formula, 0–1)"),
        ("llm_rating", "LLM provisional rating (1–5 scale)"),
    ]:
        rows = [
            compute_rho_row("(a) Full 67",                    records,   comp_key),
            compute_rho_row("(b) Empty-loop EXCLUDED ← headline", excl,  comp_key),
            compute_rho_row("(c) H2=0 subset",                h2_zero,   comp_key),
            compute_rho_row("(d) openhands_nebius",           nebius,    comp_key),
            compute_rho_row("(d) openhands_swegym",           swegym,    comp_key),
            compute_rho_row("(d) swe_agent",                  swe_agent, comp_key),
        ]
        print(f"\n{'=' * 80}")
        print(f"CALIBRATION RHO — judge_score vs {comp_name}")
        print("=" * 80)
        print_rho_table(rows)

        rho_a = rows[0]["rho"]
        rho_b = rows[1]["rho"]
        print(f"\n  INTERPRETATION: {interpret(rho_b, rho_a)}")
        print(f"  Full (a): {rho_a:+.3f}  →  Excl (b): {rho_b:+.3f}  "
              f"(drop: {rho_a - rho_b:+.3f})")

    # ── Sanity: judge_score vs H2 ─────────────────────────────────────────────
    h2_pairs = [(r["judge_score"], float(r["h2"])) for r in records if r["h2"] is not None]
    rho_h2 = spearman([p[0] for p in h2_pairs], [p[1] for p in h2_pairs])
    print(f"\n{'=' * 80}")
    print("SANITY CHECK — judge_score vs H2 (expected negative)")
    print("=" * 80)
    print(f"\n  rho = {rho_h2:+.3f}  (n={len(h2_pairs)})")
    if rho_h2 < -0.30:
        print("  PASS: judge penalises high-H2 (duplicate-message) sessions as expected.")
    elif rho_h2 < -0.15:
        print("  WEAK: negative but modest — judge doesn't rely heavily on H2.")
    else:
        print("  ALERT: near-zero or positive — unexpected. Investigate.")

    # ── Coverage summary ──────────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("COVERAGE SUMMARY")
    print("=" * 80)
    print(f"\n  {'Cut':<28}  {'N total':>8}  {'w/obj_proxy':>12}  {'w/llm_rating':>13}")
    print(f"  {'-'*28}  {'-'*8}  {'-'*12}  {'-'*13}")
    for lbl, sub in [
        ("(a) full 67",          records),
        ("(b) cluster excl",     excl),
        ("(c) H2=0",             h2_zero),
        ("(d) nebius",           nebius),
        ("(d) swegym",           swegym),
        ("(d) swe_agent",        swe_agent),
    ]:
        n_o = sum(1 for r in sub if r["obj_proxy"]  is not None)
        n_l = sum(1 for r in sub if r["llm_rating"] is not None)
        print(f"  {lbl:<28}  {len(sub):>8}  {n_o:>12}  {n_l:>13}")


if __name__ == "__main__":
    main()
