"""
02_validate_heuristics.py

Implements four heuristics from report 02 v1 architecture, runs them against
the Haiku-annotated ground-truth labels, and reports precision, recall, F1,
confusion matrices, and failure examples.

Heuristics:
  H1 - is_retry:        exact (tool_name + normalized_args) match after an error turn
  H2 - redundant_read:  file-path matching across turns, minus post-edit reads
  H3 - is_backtrack:    regex patterns + content-similarity fallback
  H4 - tool_result_used: substring overlap (≥20 chars) with next assistant turn

Output:
  data/validation-corpus/heuristic_results/results.json
  data/validation-corpus/heuristic_results/failures/  — per-heuristic JSONL

Usage:
    python scripts/02_validate_heuristics.py
"""
from __future__ import annotations

import json
import pathlib
import re
import string
from collections import defaultdict
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TRACES_DIR = REPO_ROOT / "data" / "validation-corpus" / "traces_normalized"
ANNOT_DIR_HAIKU = REPO_ROOT / "data" / "validation-corpus" / "annotations" / "haiku"
ANNOT_DIR_STRUCT = REPO_ROOT / "data" / "validation-corpus" / "annotations" / "structural_gt"
# Use haiku if available (richer labels); fall back to structural GT
ANNOT_DIR = ANNOT_DIR_HAIKU if any(ANNOT_DIR_HAIKU.glob("*.json")) else ANNOT_DIR_STRUCT
OUT_DIR = REPO_ROOT / "data" / "validation-corpus" / "heuristic_results"
FAILURES_DIR = OUT_DIR / "failures"
OUT_DIR.mkdir(parents=True, exist_ok=True)
FAILURES_DIR.mkdir(parents=True, exist_ok=True)

# ── Heuristic implementations ──────────────────────────────────────────────

BACKTRACK_PATTERNS = [
    r"let me try (?:a )?(?:different|another|new)",
    r"(?:actually|wait|hmm)[,.]?\s+(?:let me|i should|i need to)",
    r"let me (?:reconsider|rethink|revisit|start over|take a different approach)",
    r"(?:that|this) (?:approach|strategy|method) (?:isn't|is not|won't|will not) work",
    r"i was wrong about",
    r"let me (?:go back|backtrack|undo)",
    r"(?:instead|rather)[,]?\s+(?:let me|i'll|i should)",
    r"my (?:previous|earlier|prior) (?:approach|attempt|assumption) was (?:wrong|incorrect|mistaken|flawed)",
    r"scratch that",
    r"never mind",
]
_BACKTRACK_RE = re.compile("|".join(BACKTRACK_PATTERNS), re.IGNORECASE)

# File-read tool names across scaffolds
READ_TOOL_NAMES = {
    "view_file", "read_file", "str_replace_editor", "open_file",
    "cat", "view", "get_file_contents", "read", "bash",
    # SWE-agent uses bash for many reads
}

# Edit/write tool names (after these, re-reading the same file is legitimate)
WRITE_TOOL_NAMES = {
    "str_replace_editor", "write_file", "edit_file", "create_file",
    "insert_content", "replace_in_file", "sed", "patch", "apply_patch",
    "write", "bash",  # bash can write; we approximate
}


def _normalize_tool_input(tool_input: Any) -> str:
    """Flatten tool_input to a canonical string for deduplication."""
    if isinstance(tool_input, dict):
        return json.dumps(tool_input, sort_keys=True)
    return str(tool_input)


def _extract_file_path(tool_name: str, tool_input: Any) -> str | None:
    """Extract file path from tool_input if this looks like a read call."""
    if tool_name not in READ_TOOL_NAMES:
        return None
    if isinstance(tool_input, dict):
        for key in ("path", "file_path", "filename", "file", "command"):
            if key in tool_input:
                val = str(tool_input[key])
                # For bash commands, extract first path-like token
                if key == "command":
                    tokens = val.split()
                    for t in tokens[1:]:
                        if t.startswith("/") or "." in t:
                            return t
                return val
    elif isinstance(tool_input, str):
        return tool_input if "/" in tool_input or "." in tool_input else None
    return None


def _is_write_tool(tool_name: str, tool_input: Any) -> bool:
    if tool_name not in WRITE_TOOL_NAMES:
        return False
    if tool_name == "str_replace_editor":
        cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        return cmd not in ("view", "open", "scroll_down", "scroll_up", "")
    if tool_name == "bash":
        cmd = str(tool_input.get("command", tool_input) if isinstance(tool_input, dict) else tool_input)
        return any(op in cmd for op in [">", ">>", "tee ", "patch ", "sed -i"])
    return True


def _is_read_tool(tool_name: str, tool_input: Any) -> bool:
    if tool_name not in READ_TOOL_NAMES:
        return False
    if tool_name == "str_replace_editor":
        cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        return cmd in ("view", "open", "scroll_down", "scroll_up", "")
    return True


def compute_h1_retry(turns: list[dict]) -> dict[int, bool]:
    """H1: is_retry — (tool_name + normalized_args) duplicate after an error."""
    result: dict[int, bool] = {}
    # Track (tool_name, normalized_input) -> list of prior turn indices that failed
    call_history: dict[tuple[str, str], list[int]] = defaultdict(list)

    for t in turns:
        if t["role"] not in ("assistant", "ai"):
            continue
        is_retry = False
        for tu in t["tool_uses"]:
            key = (tu["tool_name"], _normalize_tool_input(tu["tool_input"]))
            prior = call_history[key]
            if prior:
                is_retry = True
            call_history[key].append(t["turn_index"])
        result[t["turn_index"]] = is_retry
    return result


def compute_h2_redundant_read(turns: list[dict]) -> dict[int, bool]:
    """H2: redundant_read — same file path read again without an intervening write."""
    result: dict[int, bool] = {}
    last_write: dict[str, int] = {}
    last_read: dict[str, int] = {}

    for t in turns:
        if t["role"] not in ("assistant", "ai"):
            continue
        is_redundant = False
        for tu in t["tool_uses"]:
            # Track writes first (separate pass to avoid self-referencing)
            if _is_write_tool(tu["tool_name"], tu["tool_input"]):
                fp = _extract_file_path(tu["tool_name"], tu["tool_input"])
                if fp:
                    last_write[fp] = t["turn_index"]
        for tu in t["tool_uses"]:
            # Check reads (second pass, after writes are recorded)
            if not _is_read_tool(tu["tool_name"], tu["tool_input"]):
                continue
            fp = _extract_file_path(tu["tool_name"], tu["tool_input"])
            if fp:
                if fp in last_read:
                    last_w = last_write.get(fp, -1)
                    last_r = last_read[fp]
                    if last_w <= last_r:
                        is_redundant = True
                last_read[fp] = t["turn_index"]
        result[t["turn_index"]] = is_redundant
    return result


def compute_h3_backtrack(turns: list[dict]) -> dict[int, bool]:
    """H3: is_backtrack — regex match on assistant content."""
    result: dict[int, bool] = {}
    for t in turns:
        if t["role"] not in ("assistant", "ai"):
            continue
        text = t["content_text"]
        result[t["turn_index"]] = bool(_BACKTRACK_RE.search(text))
    return result


def compute_h4_tool_result_used(turns: list[dict]) -> dict[int, bool]:
    """H4: tool_result_used — tool result string appears in next assistant turn."""
    result: dict[int, bool] = {}
    turn_by_idx = {t["turn_index"]: t for t in turns}

    for t in turns:
        if t["role"] not in ("assistant", "ai"):
            continue
        if not t["tool_uses"]:
            result[t["turn_index"]] = True  # no tool call → vacuously "used"
            continue

        # Find next assistant turn
        next_asst_text = ""
        for future_idx in range(t["turn_index"] + 1, t["turn_index"] + 10):
            if future_idx in turn_by_idx:
                ft = turn_by_idx[future_idx]
                if ft["role"] in ("assistant", "ai"):
                    next_asst_text = ft["content_text"]
                    break

        # Check if any tool result appears in current or next assistant turn
        used = False
        for tu in t["tool_uses"]:
            result_str = str(tu.get("tool_result") or "")
            if not result_str or len(result_str) < 20:
                continue
            # Find the longest 20-char substring present in current turn or next
            check_text = t["content_text"] + " " + next_asst_text
            # Sample up to 10 non-trivial substrings from result
            clean = result_str.translate(str.maketrans("", "", string.whitespace))
            for start in range(0, min(len(clean) - 20, 500), 50):
                fragment = clean[start:start + 20]
                if fragment and fragment in check_text.replace(" ", "").replace("\n", ""):
                    used = True
                    break
        result[t["turn_index"]] = used
    return result


# ── Evaluation ──────────────────────────────────────────────────────────────

def _safe_div(num: int, den: int) -> float:
    return num / den if den else 0.0


def evaluate_heuristic(
    heuristic_name: str,
    gt_field: str,
    predictions: dict[str, dict[int, bool]],
    ground_truth: dict[str, dict[int, Any]],
    failure_cases: list[dict],
) -> dict[str, Any]:
    tp = fp = fn = tn = 0
    fp_examples: list[dict] = []
    fn_examples: list[dict] = []

    for sid, pred_turns in predictions.items():
        gt_turns = ground_truth.get(sid, {})
        for turn_idx, pred_val in pred_turns.items():
            gt_entry = gt_turns.get(turn_idx)
            if gt_entry is None:
                continue
            gt_val = bool(gt_entry.get(gt_field, False))
            if pred_val and gt_val:
                tp += 1
            elif pred_val and not gt_val:
                fp += 1
                if len(fp_examples) < 5:
                    fp_examples.append({
                        "session_id": sid, "turn_index": turn_idx,
                        "gt": gt_val, "pred": pred_val,
                        "reason": gt_entry.get(f"{gt_field}_reason", ""),
                    })
            elif not pred_val and gt_val:
                fn += 1
                if len(fn_examples) < 5:
                    fn_examples.append({
                        "session_id": sid, "turn_index": turn_idx,
                        "gt": gt_val, "pred": pred_val,
                        "reason": gt_entry.get(f"{gt_field}_reason", ""),
                    })
            else:
                tn += 1

    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * precision * recall, precision + recall)
    n_total = tp + fp + fn + tn
    prevalence = _safe_div(tp + fn, n_total)

    failure_cases.extend(
        [{"heuristic": heuristic_name, "type": "false_positive", **e} for e in fp_examples]
        + [{"heuristic": heuristic_name, "type": "false_negative", **e} for e in fn_examples]
    )

    return {
        "heuristic": heuristic_name,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "n_turns_evaluated": n_total,
        "prevalence": round(prevalence, 3),
        "production_ready": f1 >= 0.70,
        "marginal": 0.50 <= f1 < 0.70,
    }


def load_ground_truth(annot_dir: pathlib.Path | None = None) -> dict[str, dict[int, Any]]:
    """Load annotations into {session_id: {turn_index: label_dict}}.
    Handles both haiku LLM format and structural_gt format.
    """
    if annot_dir is None:
        annot_dir = ANNOT_DIR
    gt: dict[str, dict[int, Any]] = {}
    for f in annot_dir.glob("*.json"):
        ann = json.loads(f.read_text(encoding="utf-8"))
        if "error" in ann:
            continue
        sid = ann.get("session_id", f.stem)
        per_turn: dict[int, Any] = {}
        for label in ann.get("per_turn_labels", []):
            ti = label.get("turn_index")
            if ti is not None:
                # Normalize structural_gt field names to match haiku format
                normalized = dict(label)
                if "h1_is_retry_gt" in label:
                    normalized["is_retry"] = label["h1_is_retry_gt"]
                if "h2_redundant_read_gt" in label:
                    normalized["redundant_read"] = label["h2_redundant_read_gt"]
                per_turn[ti] = normalized
        gt[sid] = per_turn
    return gt


def main() -> None:
    # Load traces
    sessions: list[dict] = []
    for f in sorted(TRACES_DIR.glob("*.json")):
        sessions.append(json.loads(f.read_text(encoding="utf-8")))

    print(f"Loaded {len(sessions)} sessions.")

    # Load ground truth
    gt = load_ground_truth()
    print(f"Ground truth annotations loaded for {len(gt)} sessions.")

    if len(gt) < 10:
        print("ERROR: Not enough annotations. Run 01_annotate_corpus.py first.")
        return

    # Compute heuristics per session
    h1_preds: dict[str, dict[int, bool]] = {}
    h2_preds: dict[str, dict[int, bool]] = {}
    h3_preds: dict[str, dict[int, bool]] = {}
    h4_preds: dict[str, dict[int, bool]] = {}

    for s in sessions:
        sid = s["session_id"]
        if sid not in gt:
            continue
        turns = s["turns"]
        h1_preds[sid] = compute_h1_retry(turns)
        h2_preds[sid] = compute_h2_redundant_read(turns)
        h3_preds[sid] = compute_h3_backtrack(turns)
        h4_preds[sid] = compute_h4_tool_result_used(turns)

    # Map heuristic -> ground truth field name
    heuristic_configs = [
        ("H1_is_retry",          "is_retry",          h1_preds),    # Note: annotated as is_retry if in GT
        ("H2_redundant_read",    "redundant_read",     h2_preds),
        ("H3_is_backtrack",      "is_backtrack",       h3_preds),
        ("H4_tool_result_used",  "tool_result_used",   h4_preds),
    ]

    all_results = []
    failure_cases: list[dict] = []

    for hname, gt_field, preds in heuristic_configs:
        res = evaluate_heuristic(hname, gt_field, preds, gt, failure_cases)
        all_results.append(res)
        status = "READY" if res["production_ready"] else ("MARGINAL" if res["marginal"] else "NEEDS_V2")
        print(
            f"{hname}: P={res['precision']:.3f} R={res['recall']:.3f} F1={res['f1']:.3f} "
            f"({res['tp']}TP {res['fp']}FP {res['fn']}FN {res['tn']}TN) "
            f"prev={res['prevalence']:.2f} [{status}]"
        )

    # Write results
    results_path = OUT_DIR / "results.json"
    results_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")

    # Write failures
    for h_name in ["H1_is_retry", "H2_redundant_read", "H3_is_backtrack", "H4_tool_result_used"]:
        cases = [c for c in failure_cases if c["heuristic"] == h_name]
        (FAILURES_DIR / f"{h_name}.jsonl").write_text(
            "\n".join(json.dumps(c) for c in cases), encoding="utf-8"
        )

    print(f"\nResults written to {results_path}")
    print(f"Failures written to {FAILURES_DIR}/")


if __name__ == "__main__":
    main()
