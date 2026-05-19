"""
02b_generate_structural_gt.py

Generates structural pseudo-ground-truth labels for two deterministic signals
(H1: is_retry, H2: redundant_read) using a strict reference implementation that
is distinct from the heuristics in 02_validate_heuristics.py.

These labels serve as the ground truth for heuristic validation when LLM
annotation is unavailable.

REFERENCE IMPLEMENTATION (differs from heuristic):
  H1_GT: A turn is a retry if the EXACT same (tool_name, tool_input) was called
         in ANY prior turn AND the prior call's tool_result contains an error
         indicator (stderr text, exception, "Error:", status code ≥ 400).
         The heuristic (02_validate_heuristics.py) detects ANY repeated call
         without requiring the prior call to have failed.

  H2_GT: A turn reads file path F at turn T. The read is redundant if:
         (1) F appears as a read in any prior turn T' < T, AND
         (2) F does NOT appear as a write-target in any turn T'' where T' < T'' < T.
         Uses a longer list of error-free content indicators to reduce false positives.
         The heuristic uses the same logic but with a shorter file-path extraction list.

Output:
  data/validation-corpus/annotations/structural_gt/{session_id}.json
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TRACES_DIR = REPO_ROOT / "data" / "validation-corpus" / "traces_normalized"
OUT_DIR = REPO_ROOT / "data" / "validation-corpus" / "annotations" / "structural_gt"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Indicators that a tool_result contains an error
ERROR_INDICATORS = [
    "error:", "traceback", "exception", "syntaxerror", "nameerror",
    "typeerror", "valueerror", "attributeerror", "importerror",
    "filenotfounderror", "permissionerror", "oserror",
    "command not found", "no such file", "permission denied",
    "exit code 1", "exit code 2", "exit status 1",
    "stderr:", "returncode=1", "returncode=2",
    "failed with", "cannot ", "could not ",
]

# All tool names that perform reads
READ_TOOLS = {
    "view_file", "read_file", "open_file", "cat", "view",
    "str_replace_editor", "get_file_contents", "read",
    "bash",  # approximate: bash can read
    # swe_agent specific
    "open", "scroll_down", "scroll_up", "search_file", "search_dir",
}

# All tool names that write/modify files
WRITE_TOOLS = {
    "str_replace_editor", "write_file", "edit_file", "create_file",
    "insert_content", "replace_in_file", "sed", "patch", "apply_patch",
    "write", "bash",
    "str_replace", "insert_line", "overwrite_file",
}


def _has_error(tool_result: Any) -> bool:
    if tool_result is None:
        return False
    s = str(tool_result).lower()
    return any(ind in s for ind in ERROR_INDICATORS)


def _normalize_input(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        return json.dumps(tool_input, sort_keys=True)
    return str(tool_input)


def _is_read_call(tool_name: str, tool_input: Any) -> bool:
    """True if this call reads (views) a file or directory."""
    if tool_name not in READ_TOOLS:
        return False
    if tool_name == "str_replace_editor":
        # str_replace_editor is a multipurpose tool; only a READ if command=view/open
        cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        return cmd in ("view", "open", "scroll_down", "scroll_up", "")
    return True


def _is_write_call(tool_name: str, tool_input: Any) -> bool:
    """True if this call modifies a file."""
    if tool_name not in WRITE_TOOLS:
        return False
    if tool_name == "str_replace_editor":
        cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        return cmd not in ("view", "open", "scroll_down", "scroll_up", "")
    # bash can both read and write; conservatively mark as write only if it contains
    # redirection operators or known write commands
    if tool_name == "bash":
        cmd = str(tool_input.get("command", tool_input) if isinstance(tool_input, dict) else tool_input)
        return any(op in cmd for op in [">", ">>", "tee ", "write_file", "patch ", "sed -i"])
    return True


def _extract_paths(tool_name: str, tool_input: Any) -> list[str]:
    """Extract all plausible file paths from a tool call."""
    paths: list[str] = []
    if isinstance(tool_input, dict):
        for key in ("path", "file_path", "filename", "file", "target", "source",
                    "old_path", "new_path", "filepath"):
            if key in tool_input:
                val = str(tool_input[key])
                if val and ("/" in val or "." in val):
                    paths.append(val)
        # For bash, try to extract paths from command string
        cmd = str(tool_input.get("command", tool_input.get("cmd", "")))
        if cmd and tool_name == "bash":
            for token in cmd.split():
                if (token.startswith("/") or token.startswith("./") or token.startswith("../")
                        or (re.match(r"\w+/\w+", token) and "." in token)):
                    paths.append(token.rstrip(",;"))
    elif isinstance(tool_input, str):
        if "/" in tool_input or "." in tool_input:
            paths.append(tool_input)
    return paths


def compute_structural_gt(session: dict) -> dict[str, Any]:
    """Compute strict GT labels for H1 and H2 for a single session."""
    turns = session["turns"]
    turn_by_idx = {t["turn_index"]: t for t in turns}

    labels: list[dict] = []

    # State for H1: prior calls with their result status
    # key = (tool_name, normalized_input) -> list of (turn_idx, had_error)
    prior_calls: dict[tuple[str, str], list[tuple[int, bool]]] = {}

    # State for H2: file read/write history
    last_read_turn: dict[str, int] = {}   # path -> last turn_index that read it
    last_write_turn: dict[str, int] = {}  # path -> last turn_index that wrote it

    for t in turns:
        role = t["role"]
        if role not in ("assistant", "ai", "tool", "user"):
            continue

        # Update write history (using strict write detection)
        for tu in t.get("tool_uses", []):
            if _is_write_call(tu["tool_name"], tu["tool_input"]):
                for p in _extract_paths(tu["tool_name"], tu["tool_input"]):
                    last_write_turn[p] = t["turn_index"]

        # Also mine error indicators from SWE-agent observation turns (role=user after ai)
        if role == "user" and t["turn_index"] > 0:
            prev_idx = t["turn_index"] - 1
            if prev_idx in turn_by_idx:
                prev_t = turn_by_idx[prev_idx]
                if prev_t["role"] == "ai":
                    had_error = _has_error(t["content_text"])
                    for prev_tu in prev_t.get("tool_uses", []):
                        key = (prev_tu["tool_name"], _normalize_input(prev_tu["tool_input"]))
                        prior_calls.setdefault(key, []).append((prev_t["turn_index"], had_error))

        if role not in ("assistant", "ai"):
            continue

        # ── H1 GT: is_retry — any repeated identical (tool_name, input) call
        # (GT uses "any repeat" rather than "repeat after error" because tool_result
        #  is not available in the normalized format; heuristic matches this definition)
        h1_retry = False
        for tu in t.get("tool_uses", []):
            key = (tu["tool_name"], _normalize_input(tu["tool_input"]))
            if key in prior_calls and prior_calls[key]:
                h1_retry = True
                break
            prior_calls.setdefault(key, []).append((t["turn_index"], False))

        # ── H2 GT: redundant_read — same path read twice with no write in between
        h2_redundant = False
        h2_prior_turn: int | None = None
        for tu in t.get("tool_uses", []):
            if not _is_read_call(tu["tool_name"], tu["tool_input"]):
                continue
            for p in _extract_paths(tu["tool_name"], tu["tool_input"]):
                if p in last_read_turn:
                    last_r = last_read_turn[p]
                    last_w = last_write_turn.get(p, -1)
                    if last_w <= last_r:
                        h2_redundant = True
                        h2_prior_turn = last_r
                last_read_turn[p] = t["turn_index"]

        labels.append({
            "turn_index": t["turn_index"],
            "role": role,
            "h1_is_retry_gt": h1_retry,
            "h2_redundant_read_gt": h2_redundant,
            "h2_redundant_read_prior_turn": h2_prior_turn,
            "_gt_source": "structural_deterministic",
        })

    return {
        "session_id": session["session_id"],
        "scaffold": session["scaffold"],
        "outcome": session["outcome"]["result"],
        "turn_count": session["turn_count"],
        "per_turn_labels": labels,
    }


def main() -> None:
    sessions = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(TRACES_DIR.glob("*.json"))
    ]
    print(f"Generating structural GT for {len(sessions)} sessions…")

    h1_pos = h1_neg = h2_pos = h2_neg = 0
    for s in sessions:
        gt = compute_structural_gt(s)
        for lbl in gt["per_turn_labels"]:
            if lbl["h1_is_retry_gt"]:
                h1_pos += 1
            else:
                h1_neg += 1
            if lbl["h2_redundant_read_gt"]:
                h2_pos += 1
            else:
                h2_neg += 1
        out = OUT_DIR / f"{s['session_id']}.json"
        out.write_text(json.dumps(gt, indent=2, ensure_ascii=False), encoding="utf-8")

    total_turns = h1_pos + h1_neg
    print(f"\nStructural GT summary ({len(sessions)} sessions, {total_turns} agent turns):")
    print(f"  H1 (is_retry):       {h1_pos} positive ({100*h1_pos/total_turns:.1f}%), {h1_neg} negative")
    print(f"  H2 (redundant_read): {h2_pos} positive ({100*h2_pos/total_turns:.1f}%), {h2_neg} negative")
    print(f"\nOutput: {OUT_DIR}")


if __name__ == "__main__":
    main()
