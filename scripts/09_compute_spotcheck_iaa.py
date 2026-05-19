"""
Compute Cohen's kappa between spot-check (Sonnet) labels and:
  1. Structural GT (H1: is_retry, H2: redundant_read)
  2. Haiku annotations (all 4 fields) — if haiku/ dir is populated

Also produces the pre-annotation kappa to document baseline agreement
between deterministic GT and Sonnet judgment.
"""
from __future__ import annotations
import json, math, pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ANNOT_DIR = REPO_ROOT / "data" / "validation-corpus" / "annotations"
SPOTCHECK = ANNOT_DIR / "spotcheck_sample.json"
STRUCT_GT_DIR = ANNOT_DIR / "structural_gt"
HAIKU_DIR = ANNOT_DIR / "haiku"
OUT_PATH = ANNOT_DIR / "spotcheck_iaa.json"


def cohen_kappa(a: list[int], b: list[int]) -> dict:
    n = len(a)
    if n == 0:
        return {"kappa": None, "n": 0}
    po = sum(x == y for x, y in zip(a, b)) / n
    pa = sum(a) / n
    pb = sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    kappa = (po - pe) / (1 - pe) if pe < 1.0 else 1.0
    return {
        "kappa": round(kappa, 3),
        "po": round(po, 3),
        "pe": round(pe, 3),
        "n": n,
        "labeler_a_pos_rate": round(pa, 3),
        "labeler_b_pos_rate": round(pb, 3),
    }


def main() -> None:
    samples = json.loads(SPOTCHECK.read_text(encoding="utf-8"))

    # Build lookup for spotcheck labels
    spotcheck: dict[tuple[str, int], dict] = {
        (s["session_id"], s["turn_index"]): s["spotcheck_labels"]
        for s in samples
        if s["spotcheck_labels"]["is_retry"] is not None
    }

    # ── 1. Structural GT vs Spot-check ────────────────────────────────────────
    struct_retry_sc, struct_retry_gt = [], []
    struct_rr_sc, struct_rr_gt = [], []

    for s in samples:
        key = (s["session_id"], s["turn_index"])
        sc_labels = s["spotcheck_labels"]
        if sc_labels["is_retry"] is None:
            continue

        gt_file = STRUCT_GT_DIR / f"{s['session_id']}.json"
        if not gt_file.exists():
            continue
        gt_ann = json.loads(gt_file.read_text(encoding="utf-8"))
        gt_by_turn = {lbl["turn_index"]: lbl for lbl in gt_ann.get("per_turn_labels", [])}
        gt_turn = gt_by_turn.get(s["turn_index"])
        if not gt_turn:
            continue

        struct_retry_sc.append(int(sc_labels["is_retry"]))
        struct_retry_gt.append(int(gt_turn.get("h1_is_retry_gt", False)))

        struct_rr_sc.append(int(sc_labels["redundant_read"]))
        struct_rr_gt.append(int(gt_turn.get("h2_redundant_read_gt", False)))

    kappa_struct_retry = cohen_kappa(struct_retry_sc, struct_retry_gt)
    kappa_struct_rr = cohen_kappa(struct_rr_sc, struct_rr_gt)

    print("=== Structural GT vs Sonnet spot-check ===")
    print(f"  is_retry (H1):      kappa={kappa_struct_retry['kappa']}  n={kappa_struct_retry['n']}")
    print(f"    SC positive rate: {kappa_struct_retry['labeler_a_pos_rate']}  GT positive rate: {kappa_struct_retry['labeler_b_pos_rate']}")
    print(f"  redundant_read (H2): kappa={kappa_struct_rr['kappa']}  n={kappa_struct_rr['n']}")
    print(f"    SC positive rate: {kappa_struct_rr['labeler_a_pos_rate']}  GT positive rate: {kappa_struct_rr['labeler_b_pos_rate']}")

    # ── 2. Haiku vs Spot-check (if annotations exist) ────────────────────────
    haiku_files = {f.stem: f for f in HAIKU_DIR.glob("*.json")} if HAIKU_DIR.exists() else {}
    haiku_kappas: dict[str, dict] = {}

    if haiku_files:
        fields = ["is_retry", "is_backtrack", "tool_result_used", "redundant_read"]
        for field in fields:
            sc_vals, hk_vals = [], []
            for s in samples:
                sc_labels = s["spotcheck_labels"]
                if sc_labels[field] is None:
                    continue
                hk_file = haiku_files.get(s["session_id"])
                if not hk_file:
                    continue
                hk_ann = json.loads(hk_file.read_text(encoding="utf-8"))
                hk_by_turn = {lbl["turn_index"]: lbl for lbl in hk_ann.get("per_turn_labels", [])}
                hk_turn = hk_by_turn.get(s["turn_index"])
                if not hk_turn:
                    continue
                sc_vals.append(int(bool(sc_labels[field])))
                hk_vals.append(int(bool(hk_turn.get(field, False))))
            haiku_kappas[field] = cohen_kappa(sc_vals, hk_vals)

        print("\n=== Haiku annotations vs Sonnet spot-check ===")
        for field, k in haiku_kappas.items():
            print(f"  {field:<22} kappa={k['kappa']}  n={k['n']}  "
                  f"SC_pos={k['labeler_a_pos_rate']}  HK_pos={k['labeler_b_pos_rate']}")
    else:
        print("\n(Haiku annotations not available — run 01_annotate_corpus.py first)")
        haiku_kappas = {}

    result = {
        "n_spotcheck": len(samples),
        "structural_gt_vs_sonnet": {
            "is_retry_kappa": kappa_struct_retry,
            "redundant_read_kappa": kappa_struct_rr,
        },
        "haiku_vs_sonnet": haiku_kappas,
        "spotcheck_label_distribution": {
            "is_retry": sum(s["spotcheck_labels"]["is_retry"] for s in samples if s["spotcheck_labels"]["is_retry"] is not None),
            "is_backtrack": sum(s["spotcheck_labels"]["is_backtrack"] for s in samples if s["spotcheck_labels"]["is_backtrack"] is not None),
            "tool_result_used": sum(s["spotcheck_labels"]["tool_result_used"] for s in samples if s["spotcheck_labels"]["tool_result_used"] is not None),
            "redundant_read": sum(s["spotcheck_labels"]["redundant_read"] for s in samples if s["spotcheck_labels"]["redundant_read"] is not None),
            "n_total": len([s for s in samples if s["spotcheck_labels"]["is_retry"] is not None]),
        },
    }
    OUT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nIAA report written to {OUT_PATH}")


if __name__ == "__main__":
    main()
