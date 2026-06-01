from __future__ import annotations
"""
diagnose_distribution.py - Phase 4 verdict distribution diagnosis.

Run LOCALLY after GPU scoring is retrieved. Answers:
  - Do H2=0 clean sessions score BETTER/MUCH_BETTER or WORSE?
  - Is negative skew from genuinely wasteful corpus or miscalibrated judge?

Usage: python scripts/diagnose_distribution.py
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
JUDGE_SCORES_PATH = REPO_ROOT / "data" / "judge_scores.jsonl"
CALIBRATION_SAMPLE_PATH = REPO_ROOT / "data" / "calibration_sample.json"
LAYER1_OUTPUTS_PATH = REPO_ROOT / "data" / "layer1_outputs.jsonl"

VERDICTS_ORDERED = ["MUCH_BETTER", "BETTER", "SIMILAR", "WORSE", "MUCH_WORSE"]

VERDICT_SCORE: dict[str, float] = {
    "MUCH_BETTER": 1.00,
    "BETTER": 0.75,
    "SIMILAR": 0.50,
    "WORSE": 0.25,
    "MUCH_WORSE": 0.00,
}


# ---------------------------------------------------------------------------
# Band helpers
# ---------------------------------------------------------------------------

def h2_band(h2: Optional[int]) -> str:
    """Assign h2_duplicate_count to a named band."""
    if h2 is None:
        return "unknown"
    if h2 == 0:
        return "H2=0"
    if h2 <= 5:
        return "H2=1-5"
    if h2 <= 20:
        return "H2=6-20"
    return "H2=21+"


def p25_band(p25: Optional[float]) -> str:
    """Assign p25_token_ratio to a named band."""
    if p25 is None:
        return "unknown"
    if p25 < 1.0:
        return "lean (<1.0)"
    if p25 <= 2.0:
        return "mid (1.0-2.0)"
    return "wasteful (>2.0)"


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------

def load_judge_scores(path: Path) -> dict[str, dict]:
    """Load judge_scores.jsonl; returns {session_id: row}."""
    scores: dict[str, dict] = {}
    bad = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    sid = row.get("session_id")
                    if sid:
                        scores[sid] = row
                except json.JSONDecodeError:
                    bad += 1
    except FileNotFoundError:
        print(f"WARNING: {path} not found - no scored sessions loaded.")
    if bad:
        print(f"WARNING: {bad} lines in judge_scores.jsonl failed JSON parse.")
    return scores


def load_calibration_sample(path: Path) -> dict[str, dict]:
    """Load calibration_sample.json; returns {session_id: row}."""
    rows: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        # calibration_sample.json is either a list or a dict keyed by session_id
        if isinstance(data, list):
            for item in data:
                sid = item.get("session_id")
                if sid:
                    rows[sid] = item
        elif isinstance(data, dict):
            # Could be {session_id: {...}} or {"sessions": [...]}
            for key, val in data.items():
                if isinstance(val, dict) and "session_id" in val:
                    rows[val["session_id"]] = val
                elif isinstance(val, dict):
                    rows[key] = val
    except FileNotFoundError:
        print(f"WARNING: {path} not found - calibration metadata unavailable.")
    except json.JSONDecodeError as exc:
        print(f"WARNING: {path} parse error: {exc}")
    return rows


def load_layer1_outputs(path: Path) -> dict[str, dict]:
    """Load layer1_outputs.jsonl; returns {session_id: row}."""
    rows: dict[str, dict] = {}
    bad = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    sid = row.get("session_id")
                    if sid:
                        rows[sid] = row
                except json.JSONDecodeError:
                    bad += 1
    except FileNotFoundError:
        print(f"WARNING: {path} not found - layer1 metadata unavailable.")
    if bad:
        print(f"WARNING: {bad} lines in layer1_outputs.jsonl failed JSON parse.")
    return rows


# ---------------------------------------------------------------------------
# Merge into a flat session record
# ---------------------------------------------------------------------------

def build_session_records(
    scores: dict[str, dict],
    cal: dict[str, dict],
    l1: dict[str, dict],
) -> list[dict]:
    """
    Merge the three sources into one record per scored session.

    Priority for h2_duplicate_count: calibration_sample > layer1_outputs.
    """
    records: list[dict] = []
    for sid, score_row in scores.items():
        verdict = score_row.get("verdict")
        if verdict not in VERDICT_SCORE:
            verdict = None  # treat parse failures as None

        cal_row = cal.get(sid, {})
        l1_row = l1.get(sid, {})

        h2 = cal_row.get("h2_duplicate_count") or l1_row.get("h2_duplicate_count")
        p25 = l1_row.get("p25_token_ratio") or cal_row.get("p25_token_ratio")
        resolved = cal_row.get("resolved")
        scaffold = cal_row.get("scaffold")

        # Normalise types
        if h2 is not None:
            try:
                h2 = int(h2)
            except (ValueError, TypeError):
                h2 = None
        if p25 is not None:
            try:
                p25 = float(p25)
            except (ValueError, TypeError):
                p25 = None
        if resolved is not None:
            resolved = bool(resolved)

        records.append(
            {
                "session_id": sid,
                "verdict": verdict,
                "h2": h2,
                "p25": p25,
                "resolved": resolved,
                "scaffold": scaffold,
            }
        )
    return records


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

def _mean_score(verdicts: list[Optional[str]]) -> Optional[float]:
    """Mean verdict score, ignoring None entries."""
    scored = [VERDICT_SCORE[v] for v in verdicts if v in VERDICT_SCORE]
    return sum(scored) / len(scored) if scored else None


def _col_count(verdicts: list[Optional[str]], label: str) -> int:
    return sum(1 for v in verdicts if v == label)


def print_breakdown_table(title: str, groups: dict[str, list[Optional[str]]]) -> None:
    """
    Print a breakdown table.

    groups: { band_label: [verdict_or_None, ...] }
    """
    col_w = 14  # width for verdict columns
    label_w = 18
    sep = "-" * 95
    print(f"\n{sep}")
    print(f"  {title}")
    print(f"{sep}")
    header = (
        f"  {'Band':<{label_w}} {'N':>5}  "
        + "  ".join(f"{v:>{col_w}}" for v in VERDICTS_ORDERED)
        + f"  {'mean_score':>{col_w}}"
    )
    print(header)
    print(f"  {'-' * label_w}  {'-----'}  " + "  ".join([f"{'------':>{col_w}}"] * 5) + f"  {'----------':>{col_w}}")

    for band, verdicts in sorted(groups.items()):
        n = len(verdicts)
        counts = [_col_count(verdicts, v) for v in VERDICTS_ORDERED]
        mean = _mean_score(verdicts)
        mean_str = f"{mean:.3f}" if mean is not None else "  n/a"
        row = (
            f"  {band:<{label_w}} {n:>5}  "
            + "  ".join(f"{c:>{col_w}}" for c in counts)
            + f"  {mean_str:>{col_w}}"
        )
        print(row)
    print(f"{sep}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Run all breakdown tables and print diagnostics."""
    print("=" * 95)
    print("  diagnose_distribution.py - Phase 4 verdict distribution diagnosis")
    print("=" * 95)

    # Load
    scores = load_judge_scores(JUDGE_SCORES_PATH)
    cal = load_calibration_sample(CALIBRATION_SAMPLE_PATH)
    l1 = load_layer1_outputs(LAYER1_OUTPUTS_PATH)

    records = build_session_records(scores, cal, l1)
    total = len(records)

    print(f"\n  Total scored sessions: {total}")
    if total == 0:
        print("  No scored sessions found. Run GPU scoring first.")
        return

    # ── Overall distribution ─────────────────────────────────────────────────
    all_verdicts: list[Optional[str]] = [r["verdict"] for r in records]
    valid_verdicts = [v for v in all_verdicts if v in VERDICT_SCORE]
    parse_fail = sum(1 for v in all_verdicts if v not in VERDICT_SCORE)

    print(f"\n  OVERALL DISTRIBUTION  (parse_fail={parse_fail}/{total})")
    print(f"  {'Verdict':<16} {'Count':>6}  {'%':>6}")
    print(f"  {'-' * 16}  {'------'}  {'------'}")
    for v in VERDICTS_ORDERED:
        c = _col_count(valid_verdicts, v)
        pct = c / len(valid_verdicts) * 100 if valid_verdicts else 0.0
        print(f"  {v:<16} {c:>6}  {pct:>5.1f}%")
    overall_mean = _mean_score(valid_verdicts)
    print(f"\n  Overall mean_score: {overall_mean:.3f}" if overall_mean is not None else "\n  Overall mean_score: n/a")

    # ── H2 band breakdown ────────────────────────────────────────────────────
    h2_groups: dict[str, list[Optional[str]]] = defaultdict(list)
    for r in records:
        h2_groups[h2_band(r["h2"])].append(r["verdict"])
    print_breakdown_table("Breakdown by H2 duplicate count band", h2_groups)

    # ── p25 band breakdown ───────────────────────────────────────────────────
    p25_groups: dict[str, list[Optional[str]]] = defaultdict(list)
    for r in records:
        p25_groups[p25_band(r["p25"])].append(r["verdict"])
    print_breakdown_table("Breakdown by p25_token_ratio band", p25_groups)

    # ── Resolved breakdown ───────────────────────────────────────────────────
    res_groups: dict[str, list[Optional[str]]] = defaultdict(list)
    for r in records:
        label = {True: "resolved=True", False: "resolved=False"}.get(r["resolved"], "resolved=unknown")
        res_groups[label].append(r["verdict"])
    print_breakdown_table("Breakdown by resolved status", res_groups)

    # ── Scaffold breakdown ───────────────────────────────────────────────────
    scf_groups: dict[str, list[Optional[str]]] = defaultdict(list)
    for r in records:
        label = r["scaffold"] if r["scaffold"] else "unknown"
        scf_groups[label].append(r["verdict"])
    print_breakdown_table("Breakdown by scaffold type", scf_groups)

    # ── Key diagnostics ──────────────────────────────────────────────────────
    print(f"\n{'=' * 95}")
    print("  KEY DIAGNOSTICS")
    print(f"{'=' * 95}")

    # H2=0 vs H2=21+ diagnostic
    h2_0_verdicts = h2_groups.get("H2=0", [])
    h2_21_verdicts = h2_groups.get("H2=21+", [])
    h2_0_mean = _mean_score(h2_0_verdicts)
    h2_21_mean = _mean_score(h2_21_verdicts)

    h2_0_n = len(h2_0_verdicts)
    h2_0_mean_str = f"{h2_0_mean:.3f}" if h2_0_mean is not None else "n/a"
    h2_21_mean_str = f"{h2_21_mean:.3f}" if h2_21_mean is not None else "n/a"

    if h2_0_mean is not None and h2_21_mean is not None:
        verdict_label = (
            "PASS (clean sessions score well)"
            if h2_0_mean > h2_21_mean
            else "ALERT (clean sessions scoring WORSE - possible prompt miscalibration)"
        )
    elif h2_0_mean is not None:
        verdict_label = "INCONCLUSIVE (no H2=21+ sessions to compare)"
    else:
        verdict_label = "INCONCLUSIVE (no H2=0 sessions in scored set)"

    print(
        f"\n  DIAGNOSTIC: H2=0 sessions (N={h2_0_n}): mean_score={h2_0_mean_str}"
        f"  H2=21+ mean_score={h2_21_mean_str}"
    )
    print(f"  -> {verdict_label}")

    # p25 lean vs wasteful diagnostic
    lean_verdicts = p25_groups.get("lean (<1.0)", [])
    wasteful_verdicts = p25_groups.get("wasteful (>2.0)", [])
    lean_mean = _mean_score(lean_verdicts)
    wasteful_mean = _mean_score(wasteful_verdicts)
    lean_str = f"{lean_mean:.3f}" if lean_mean is not None else "n/a"
    wasteful_str = f"{wasteful_mean:.3f}" if wasteful_mean is not None else "n/a"
    print(
        f"\n  DIAGNOSTIC: lean (<1.0) sessions mean_score={lean_str}"
        f" vs wasteful (>2.0) mean_score={wasteful_str}"
    )

    if lean_mean is not None and wasteful_mean is not None:
        if lean_mean > wasteful_mean:
            print("  -> PASS (lean sessions score better than wasteful - judge direction correct)")
        else:
            print("  -> ALERT (wasteful sessions not penalised - judge may not distinguish token efficiency)")
    else:
        print("  -> INCONCLUSIVE (insufficient data in one or both bands)")

    print(f"\n{'=' * 95}\n")


if __name__ == "__main__":
    main()
