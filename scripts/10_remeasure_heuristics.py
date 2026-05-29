"""
10_remeasure_heuristics.py  — Phase A.1 heuristic F1 remeasurement

Loads GPT-OSS LLM labels (from scripts/01_annotate_corpus.py output) and
evaluates four heuristic implementations against them. Also implements and
tests a revised H1 definition.

Phase A.1 label → heuristic mapping:
  H1 (redundant_read)      — old H2 from 02_validate_heuristics.py
  H2 (duplicate_message)   — old H1 (is_retry) from 02_validate_heuristics.py
  H3 (backtrack)           — old H3 (is_backtrack)
  H4 (tool_result_used)    — old H4 (tool_result_used)
  H1-revised               — new implementation (content hash + failure gate)

Outputs:
  data/validation-corpus/heuristic_results/phaseA1_results.json
  data/validation-corpus/heuristic_results/phaseA1_failures/H*.jsonl

Usage:
    python scripts/10_remeasure_heuristics.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import string
from collections import defaultdict
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TRACES_DIR = REPO_ROOT / "data" / "validation-corpus" / "traces_normalized"
GPT_OSS_DIR = REPO_ROOT / "data" / "validation-corpus" / "annotations" / "gpt_oss"
OUT_DIR = REPO_ROOT / "data" / "validation-corpus" / "heuristic_results"
FAILURES_DIR = OUT_DIR / "phaseA1_failures"
OUT_DIR.mkdir(parents=True, exist_ok=True)
FAILURES_DIR.mkdir(parents=True, exist_ok=True)


# ── Failure / error detection (used by revised H1) ────────────────────────────

# Signals that a tool result or turn content indicates a failure
_FAILURE_PATTERNS = re.compile(
    r"traceback|assertionerror|error:|errors:|failed|fail:|"
    r"exception:|exit code [1-9]|command not found|no such file|"
    r"syntaxerror|typeerror|nameerror|valueerror|indexerror|keyerror|"
    r"attributeerror|importerror|runtimeerror|tests? failed|"
    r"compilation failed|build failed|linting failed",
    re.IGNORECASE,
)


def _turn_has_failure(turn: dict[str, Any]) -> bool:
    """Return True if the turn contains evidence of a failure/error."""
    text = (turn.get("content_text") or "").lower()
    if _FAILURE_PATTERNS.search(text):
        return True
    for tu in turn.get("tool_uses", []):
        res = str(tu.get("tool_result") or "").lower()
        if _FAILURE_PATTERNS.search(res):
            return True
    return False


# ── Tool name sets ─────────────────────────────────────────────────────────────

READ_TOOLS = {
    "view_file", "read_file", "str_replace_editor", "open_file",
    "cat", "view", "get_file_contents", "read", "bash",
}
WRITE_TOOLS = {
    "str_replace_editor", "write_file", "edit_file", "create_file",
    "insert_content", "replace_in_file", "sed", "patch", "apply_patch",
    "write", "bash",
}


def _is_read(name: str, inp: Any) -> bool:
    if name not in READ_TOOLS:
        return False
    if name == "str_replace_editor":
        cmd = inp.get("command", "") if isinstance(inp, dict) else ""
        return cmd in ("view", "open", "scroll_down", "scroll_up", "")
    return True


def _is_write(name: str, inp: Any) -> bool:
    if name not in WRITE_TOOLS:
        return False
    if name == "str_replace_editor":
        cmd = inp.get("command", "") if isinstance(inp, dict) else ""
        return cmd not in ("view", "open", "scroll_down", "scroll_up", "")
    if name == "bash":
        cmd = str(inp.get("command", inp) if isinstance(inp, dict) else inp)
        return any(op in cmd for op in [">", ">>", "tee ", "patch ", "sed -i"])
    return True


def _extract_path(name: str, inp: Any) -> str | None:
    if not _is_read(name, inp):
        return None
    if isinstance(inp, dict):
        for key in ("path", "file_path", "filename", "file", "command"):
            if key in inp:
                val = str(inp[key])
                if key == "command":
                    for tok in val.split()[1:]:
                        if tok.startswith("/") or ("." in tok and "/" in tok):
                            return tok
                else:
                    return val
    elif isinstance(inp, str):
        return inp if ("/" in inp or "." in inp) else None
    return None


# ── Heuristic implementations ─────────────────────────────────────────────────

def compute_h1_orig_redundant_read(turns: list[dict[str, Any]]) -> dict[int, bool]:
    """Original H2 (now called H1): file-path repeat without intervening write."""
    result: dict[int, bool] = {}
    last_write: dict[str, int] = {}
    last_read: dict[str, int] = {}

    for t in turns:
        if t["role"] not in ("assistant", "ai"):
            continue
        for tu in t.get("tool_uses", []):
            if _is_write(tu["tool_name"], tu.get("tool_input")):
                fp = _extract_path(tu["tool_name"], tu.get("tool_input"))
                if fp:
                    last_write[fp] = t["turn_index"]
        is_redundant = False
        for tu in t.get("tool_uses", []):
            if not _is_read(tu["tool_name"], tu.get("tool_input")):
                continue
            fp = _extract_path(tu["tool_name"], tu.get("tool_input"))
            if fp and fp in last_read:
                if last_write.get(fp, -1) <= last_read[fp]:
                    is_redundant = True
            if fp:
                last_read[fp] = t["turn_index"]
        result[t["turn_index"]] = is_redundant
    return result


def compute_h1_revised_redundant_read(turns: list[dict[str, Any]]) -> dict[int, bool]:
    """Revised H1: same path + same content hash + no intervening failure."""
    result: dict[int, bool] = {}
    by_idx = {t["turn_index"]: t for t in turns}

    # file_path -> (last_read_turn_idx, content_hash)
    last_read: dict[str, tuple[int, str]] = {}
    last_write: dict[str, int] = {}

    for t in sorted(turns, key=lambda x: x["turn_index"]):
        if t["role"] not in ("assistant", "ai"):
            continue
        # Record writes first
        for tu in t.get("tool_uses", []):
            if _is_write(tu["tool_name"], tu.get("tool_input")):
                fp = _extract_path(tu["tool_name"], tu.get("tool_input"))
                if fp:
                    last_write[fp] = t["turn_index"]

        is_redundant = False
        for tu in t.get("tool_uses", []):
            if not _is_read(tu["tool_name"], tu.get("tool_input")):
                continue
            fp = _extract_path(tu["tool_name"], tu.get("tool_input"))
            if not fp:
                continue

            result_str = str(tu.get("tool_result") or "")
            content_hash = hashlib.md5(result_str.encode(), usedforsecurity=False).hexdigest()

            if fp in last_read:
                prior_idx, prior_hash = last_read[fp]
                last_w = last_write.get(fp, -1)

                # Condition (a): same path and same content hash
                same_content = (prior_hash == content_hash)
                # Condition (b): no write since last read
                no_write = last_w <= prior_idx
                # Condition (c): no failure between prior read and this read
                no_failure = not any(
                    _turn_has_failure(by_idx[idx])
                    for idx in range(prior_idx + 1, t["turn_index"])
                    if idx in by_idx
                )

                if same_content and no_write and no_failure:
                    is_redundant = True

            last_read[fp] = (t["turn_index"], content_hash)

        result[t["turn_index"]] = is_redundant
    return result


def compute_h2_duplicate_message(turns: list[dict[str, Any]]) -> dict[int, bool]:
    """H2: assistant turn nearly identical to a prior assistant turn (>90% char overlap)."""
    result: dict[int, bool] = {}
    MIN_LEN = 50  # ignore very short turns
    prior_texts: list[str] = []

    def _norm(text: str) -> str:
        return "".join(text.split())

    for t in turns:
        if t["role"] not in ("assistant", "ai"):
            continue
        raw = (t.get("content_text") or "").strip()
        norm = _norm(raw)

        is_dup = False
        if len(norm) >= MIN_LEN:
            for prev in prior_texts:
                if len(prev) < MIN_LEN:
                    continue
                # Longest common substring ratio approximation
                shorter, longer = (norm, prev) if len(norm) <= len(prev) else (prev, norm)
                # Check overlap: fraction of shorter that appears in longer
                overlap = sum(1 for c in shorter if c in longer) / max(1, len(shorter))
                # More precise: sliding window check
                win = 30
                if len(shorter) >= win:
                    matches = sum(
                        1 for i in range(0, len(shorter) - win, win)
                        if shorter[i:i + win] in longer
                    )
                    windows = max(1, (len(shorter) - win) // win)
                    overlap = matches / windows
                if overlap > 0.90:
                    is_dup = True
                    break

        result[t["turn_index"]] = is_dup
        prior_texts.append(norm)
    return result


def compute_h3_backtrack(turns: list[dict[str, Any]]) -> dict[int, bool]:
    """H3: regex backtrack patterns in assistant content."""
    _RE = re.compile(
        r"let me try (?:a )?(?:different|another|new)|"
        r"(?:actually|wait|hmm)[,.]?\s+(?:let me|i should|i need to)|"
        r"let me (?:reconsider|rethink|revisit|start over|take a different approach)|"
        r"(?:that|this) (?:approach|strategy|method) (?:isn't|is not|won't|will not) work|"
        r"i was wrong about|"
        r"let me (?:go back|backtrack|undo)|"
        r"(?:instead|rather)[,]?\s+(?:let me|i'll|i should)|"
        r"my (?:previous|earlier|prior) (?:approach|attempt|assumption) was (?:wrong|incorrect)|"
        r"scratch that|never mind",
        re.IGNORECASE,
    )
    result: dict[int, bool] = {}
    for t in turns:
        if t["role"] not in ("assistant", "ai"):
            continue
        result[t["turn_index"]] = bool(_RE.search(t.get("content_text") or ""))
    return result


def compute_h4_tool_result_used(turns: list[dict[str, Any]]) -> dict[int, bool]:
    """H4: tool result substring appears in current or next assistant turn."""
    result: dict[int, bool] = {}
    by_idx = {t["turn_index"]: t for t in turns}

    for t in turns:
        if t["role"] not in ("assistant", "ai"):
            continue
        tool_uses = t.get("tool_uses", [])
        if not tool_uses:
            result[t["turn_index"]] = True
            continue

        # Find next assistant turn (within 10 turns)
        next_text = ""
        for fi in range(t["turn_index"] + 1, t["turn_index"] + 10):
            if fi in by_idx and by_idx[fi]["role"] in ("assistant", "ai"):
                next_text = by_idx[fi].get("content_text") or ""
                break

        check = (t.get("content_text") or "") + " " + next_text
        check_compact = check.translate(str.maketrans("", "", string.whitespace))

        used = False
        for tu in tool_uses:
            res_str = str(tu.get("tool_result") or "")
            if len(res_str) < 20:
                continue
            clean_res = res_str.translate(str.maketrans("", "", string.whitespace))
            for start in range(0, min(len(clean_res) - 20, 500), 50):
                frag = clean_res[start:start + 20]
                if frag and frag in check_compact:
                    used = True
                    break
            if used:
                break

        result[t["turn_index"]] = used
    return result


# ── Evaluation ────────────────────────────────────────────────────────────────

def _load_llm_labels(gpt_dir: pathlib.Path) -> dict[str, dict[int, dict[str, Any]]]:
    """Load GPT-OSS annotations: {session_id: {turn_idx: label_dict}}."""
    gt: dict[str, dict[int, dict[str, Any]]] = {}
    for f in gpt_dir.glob("*.json"):
        ann = json.loads(f.read_text(encoding="utf-8"))
        if "error" in ann:
            continue
        sid = ann.get("session_id", f.stem)
        by_turn: dict[int, dict[str, Any]] = {
            lbl["turn_index"]: lbl
            for lbl in ann.get("per_turn_labels", [])
            if "turn_index" in lbl
        }
        gt[sid] = by_turn
    return gt


def _div(num: int, den: int) -> float:
    return num / den if den else 0.0


def evaluate(
    name: str,
    llm_field: str,
    predictions: dict[str, dict[int, bool]],
    llm_labels: dict[str, dict[int, dict[str, Any]]],
    iaa_kappa: float | None = None,
    fail_list: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    fp_examples: list[dict[str, Any]] = []
    fn_examples: list[dict[str, Any]] = []

    for sid, preds in predictions.items():
        gt_turns = llm_labels.get(sid, {})
        for turn_idx, pred in preds.items():
            gt_entry = gt_turns.get(turn_idx)
            if gt_entry is None:
                continue
            gt_val = bool(gt_entry.get(llm_field, False))
            if pred and gt_val:
                tp += 1
            elif pred and not gt_val:
                fp += 1
                if len(fp_examples) < 5:
                    fp_examples.append({
                        "session_id": sid, "turn_index": turn_idx,
                        "pred": True, "gt": False,
                        "llm_reason": gt_entry.get(llm_field.replace("llm_", "llm_") + "_reason",
                                                    gt_entry.get("llm_h1_reason", "")),
                    })
            elif not pred and gt_val:
                fn += 1
                if len(fn_examples) < 5:
                    fn_examples.append({
                        "session_id": sid, "turn_index": turn_idx,
                        "pred": False, "gt": True,
                        "llm_reason": gt_entry.get(llm_field + "_reason", ""),
                    })
            else:
                tn += 1

    prec = _div(tp, tp + fp)
    rec = _div(tp, tp + fn)
    f1 = _div(2 * prec * rec, prec + rec)
    n = tp + fp + fn + tn
    prev = _div(tp + fn, n)

    if fail_list is not None:
        for e in fp_examples:
            fail_list.append({"heuristic": name, "type": "false_positive", **e})
        for e in fn_examples:
            fail_list.append({"heuristic": name, "type": "false_negative", **e})

    verdict = (
        "PRODUCTION_READY" if f1 >= 0.70
        else "SALVAGEABLE" if f1 >= 0.50
        else "DEAD"
    )

    result: dict[str, Any] = {
        "heuristic": name,
        "llm_label_field": llm_field,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(prec, 3),
        "recall": round(rec, 3),
        "f1": round(f1, 3),
        "n_turns_evaluated": n,
        "prevalence": round(prev, 3),
        "verdict": verdict,
    }
    if iaa_kappa is not None:
        result["iaa_kappa"] = iaa_kappa
        result["f1_caveat"] = (
            f"F1={f1:.3f} is upper-bounded by IAA kappa={iaa_kappa:.3f}; "
            "ground truth is LLM-derived."
        )
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # Load corpus
    sessions: list[dict[str, Any]] = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(TRACES_DIR.glob("*.json"))
    ]
    print(f"Loaded {len(sessions)} sessions.")

    llm_labels = _load_llm_labels(GPT_OSS_DIR)
    print(f"GPT-OSS labels loaded for {len(llm_labels)} sessions.")

    if len(llm_labels) < 10:
        print(
            "ERROR: Not enough annotations. "
            "Run: python scripts/01_annotate_corpus.py"
        )
        return

    # Load IAA kappas if available
    iaa_path = REPO_ROOT / "data" / "validation-corpus" / "annotations" / "iaa_phaseA1.json"
    iaa: dict[str, Any] = {}
    if iaa_path.exists():
        iaa = json.loads(iaa_path.read_text(encoding="utf-8"))

    def _kappa(label: str) -> float | None:
        per = iaa.get("per_label", {})
        return per.get(label, {}).get("kappa")

    # Compute all heuristics
    h1_orig: dict[str, dict[int, bool]] = {}
    h1_rev: dict[str, dict[int, bool]] = {}
    h2_dup: dict[str, dict[int, bool]] = {}
    h3_back: dict[str, dict[int, bool]] = {}
    h4_used: dict[str, dict[int, bool]] = {}

    for s in sessions:
        sid = s["session_id"]
        if sid not in llm_labels:
            continue
        turns = s["turns"]
        h1_orig[sid] = compute_h1_orig_redundant_read(turns)
        h1_rev[sid] = compute_h1_revised_redundant_read(turns)
        h2_dup[sid] = compute_h2_duplicate_message(turns)
        h3_back[sid] = compute_h3_backtrack(turns)
        h4_used[sid] = compute_h4_tool_result_used(turns)

    print(f"Heuristics computed for {len(h1_orig)} sessions.")

    all_failures: list[dict[str, Any]] = []

    configs = [
        ("H1_orig_redundant_read", "llm_h1_redundant_read",
         h1_orig, _kappa("H1_redundant_read")),
        ("H1_revised_redundant_read", "llm_h1_redundant_read",
         h1_rev, _kappa("H1_redundant_read")),
        ("H2_duplicate_message", "llm_h2_duplicate_message",
         h2_dup, _kappa("H2_duplicate_message")),
        ("H3_backtrack", "llm_h3_backtrack",
         h3_back, _kappa("H3_backtrack")),
        ("H4_tool_result_used", "llm_h4_tool_result_used",
         h4_used, _kappa("H4_tool_result_used")),
    ]

    results: list[dict[str, Any]] = []
    for name, field, preds, kappa in configs:
        res = evaluate(name, field, preds, llm_labels, kappa, all_failures)
        results.append(res)
        print(
            f"{name:<32} P={res['precision']:.3f} R={res['recall']:.3f} "
            f"F1={res['f1']:.3f}  "
            f"({res['tp']}TP {res['fp']}FP {res['fn']}FN {res['tn']}TN) "
            f"prev={res['prevalence']:.2f}  [{res['verdict']}]"
            + (f"  kappa={kappa:.3f}" if kappa is not None else "")
        )

    # Write results
    out_path = OUT_DIR / "phaseA1_results.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nResults → {out_path}")

    # Write per-heuristic failure JSONL
    for cfg in configs:
        hname = cfg[0]
        cases = [c for c in all_failures if c["heuristic"] == hname]
        fp_path = FAILURES_DIR / f"{hname}.jsonl"
        fp_path.write_text("\n".join(json.dumps(c) for c in cases), encoding="utf-8")
    print(f"Failures → {FAILURES_DIR}/")

    # H1 delta summary
    h1_orig_res = next((r for r in results if r["heuristic"] == "H1_orig_redundant_read"), None)
    h1_rev_res = next((r for r in results if r["heuristic"] == "H1_revised_redundant_read"), None)
    if h1_orig_res and h1_rev_res:
        delta = h1_rev_res["f1"] - h1_orig_res["f1"]
        print(
            f"\nH1 revision delta: {delta:+.3f} "
            f"(orig F1={h1_orig_res['f1']:.3f} → "
            f"revised F1={h1_rev_res['f1']:.3f})"
        )
        if delta > 0.02:
            print("  → Improvement is real. Lock new H1 definition.")
        else:
            print("  → No meaningful improvement from revision.")


if __name__ == "__main__":
    main()
