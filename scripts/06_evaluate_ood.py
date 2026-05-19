"""
06_evaluate_ood.py

Runs domain classifier, task taxonomy, and H1-H4 heuristics against the
OOD corpus and reports degradation relative to the in-distribution corpus.

Metrics reported:
  - Domain classification: what fraction get assigned a known domain vs "unknown"
  - Heuristic firing rates: does each heuristic still fire at plausible rates?
  - Qualitative failure examples: cases where the heuristic result seems wrong

Usage:
    python scripts/06_evaluate_ood.py
"""
from __future__ import annotations

import json
import pathlib
import re
import string
from collections import defaultdict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OOD_DIR = REPO_ROOT / "data" / "ood-corpus" / "traces_normalized"
IN_DIST_DIR = REPO_ROOT / "data" / "validation-corpus" / "traces_normalized"
IN_DIST_RESULTS = REPO_ROOT / "data" / "validation-corpus" / "heuristic_results" / "results.json"
OUT_DIR = REPO_ROOT / "data" / "ood-corpus"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Re-import heuristics from 02_validate_heuristics ─────────────────────────
# (duplicated here to keep this script self-contained)

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

READ_TOOL_NAMES = {
    "view_file", "read_file", "str_replace_editor", "open_file",
    "cat", "view", "get_file_contents", "read", "bash",
    "Read",  # Claude Code tool
}
WRITE_TOOL_NAMES = {
    "str_replace_editor", "write_file", "edit_file", "create_file",
    "insert_content", "replace_in_file", "sed", "patch", "apply_patch",
    "write", "bash", "Write", "Edit",  # Claude Code tools
}


def _normalize_tool_input(ti: object) -> str:
    if isinstance(ti, dict):
        return json.dumps(ti, sort_keys=True)
    return str(ti)


def _extract_file_path(tool_name: str, tool_input: object) -> str | None:
    if tool_name not in READ_TOOL_NAMES:
        return None
    if isinstance(tool_input, dict):
        for key in ("path", "file_path", "filename", "file", "command"):
            if key in tool_input:
                val = str(tool_input[key])
                if key == "command":
                    for t in val.split()[1:]:
                        if t.startswith("/") or "." in t:
                            return t
                return val
    elif isinstance(tool_input, str):
        return tool_input if "/" in tool_input or "." in tool_input else None
    return None


def _is_write_tool(tool_name: str, tool_input: object) -> bool:
    if tool_name not in WRITE_TOOL_NAMES:
        return False
    if tool_name in ("str_replace_editor", "Edit"):
        cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        return cmd not in ("view", "open", "scroll_down", "scroll_up", "")
    if tool_name == "bash":
        cmd = str(tool_input.get("command", tool_input) if isinstance(tool_input, dict) else tool_input)
        return any(op in cmd for op in [">", ">>", "tee ", "patch ", "sed -i"])
    return True


def _is_read_tool(tool_name: str, tool_input: object) -> bool:
    if tool_name not in READ_TOOL_NAMES:
        return False
    if tool_name in ("str_replace_editor", "Read"):
        cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        return cmd in ("view", "open", "scroll_down", "scroll_up", "")
    return True


def compute_heuristics(turns: list[dict]) -> dict[str, list[dict]]:
    """Run all four heuristics. Returns per-turn results."""
    turn_results: dict[str, list[dict]] = {
        "h1_retry": [], "h2_redundant_read": [], "h3_backtrack": [], "h4_tool_result_used": []
    }
    call_history: dict[tuple, list[int]] = defaultdict(list)
    last_write: dict[str, int] = {}
    last_read: dict[str, int] = {}
    turn_by_idx = {t["turn_index"]: t for t in turns}

    for t in turns:
        if t["role"] not in ("assistant", "ai"):
            continue
        idx = t["turn_index"]

        # H1
        h1 = False
        for tu in t["tool_uses"]:
            key = (tu["tool_name"], _normalize_tool_input(tu["tool_input"]))
            if call_history[key]:
                h1 = True
            call_history[key].append(idx)
        turn_results["h1_retry"].append({"turn_index": idx, "value": h1})

        # H2
        h2 = False
        for tu in t["tool_uses"]:
            if _is_write_tool(tu["tool_name"], tu["tool_input"]):
                fp = _extract_file_path(tu["tool_name"], tu["tool_input"])
                if fp:
                    last_write[fp] = idx
        for tu in t["tool_uses"]:
            if not _is_read_tool(tu["tool_name"], tu["tool_input"]):
                continue
            fp = _extract_file_path(tu["tool_name"], tu["tool_input"])
            if fp:
                if fp in last_read and last_write.get(fp, -1) <= last_read[fp]:
                    h2 = True
                last_read[fp] = idx
        turn_results["h2_redundant_read"].append({"turn_index": idx, "value": h2})

        # H3
        h3 = bool(_BACKTRACK_RE.search(t["content_text"]))
        turn_results["h3_backtrack"].append({"turn_index": idx, "value": h3})

        # H4
        if not t["tool_uses"]:
            h4 = True
        else:
            next_asst_text = ""
            for fi in range(idx + 1, idx + 10):
                if fi in turn_by_idx and turn_by_idx[fi]["role"] in ("assistant", "ai"):
                    next_asst_text = turn_by_idx[fi]["content_text"]
                    break
            h4 = False
            for tu in t["tool_uses"]:
                rs = str(tu.get("tool_result") or "")
                if len(rs) < 20:
                    continue
                clean = rs.translate(str.maketrans("", "", string.whitespace))
                check = (t["content_text"] + next_asst_text).replace(" ", "").replace("\n", "")
                for start in range(0, min(len(clean) - 20, 500), 50):
                    if clean[start:start + 20] in check:
                        h4 = True
                        break
        turn_results["h4_tool_result_used"].append({"turn_index": idx, "value": h4})

    return turn_results


# ── Domain classifier (from 03_task_taxonomy) ────────────────────────────────

DOMAIN_MAP: list[tuple[str, str]] = [
    ("mypy", "type_checker"), ("pyflakes", "type_checker"), ("autopep8", "type_checker"),
    ("black", "type_checker"), ("pylint", "type_checker"), ("ruff", "type_checker"),
    ("cognitive_complexity", "type_checker"), ("wemake", "type_checker"),
    ("numpy", "data_ml"), ("pandas", "data_ml"), ("scipy", "data_ml"),
    ("sklearn", "data_ml"), ("monai", "data_ml"), ("pytorch", "data_ml"),
    ("dask", "data_ml"), ("modin", "data_ml"), ("pennylane", "data_ml"),
    ("django", "web_api"), ("flask", "web_api"), ("fastapi", "web_api"),
    ("aiohttp", "web_api"), ("requests", "web_api"), ("httpx", "web_api"),
    ("moto", "cloud_devops"), ("boto", "cloud_devops"), ("docker", "cloud_devops"),
    ("hydra", "cloud_devops"), ("dvc", "cloud_devops"), ("xonsh", "cloud_devops"),
    ("sqlalchemy", "db_orm"), ("sqlglot", "db_orm"), ("pymongo", "db_orm"),
    ("networkx", "graph_geo"), ("geopandas", "graph_geo"), ("folium", "graph_geo"),
    ("pytest", "testing_ci"), ("faker", "testing_ci"),
    ("pydantic", "lib_general"), ("click", "lib_general"), ("rich", "lib_general"),
    ("marshmallow", "lib_general"), ("pint", "lib_general"), ("pygame", "lib_general"),
]


def classify_domain(instance_id: str) -> str:
    repo_part = instance_id.split("__")[-1] if "__" in instance_id else instance_id
    rl = repo_part.lower()
    for frag, cat in DOMAIN_MAP:
        if frag in rl:
            return cat
    return "unknown"


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ood_files = sorted(OOD_DIR.glob("*.json"))
    if not ood_files:
        print(f"No OOD files found in {OOD_DIR}. Run 05_download_ood_traces.py first.")
        return

    ood_sessions = [json.loads(f.read_text(encoding="utf-8")) for f in ood_files]
    print(f"OOD sessions: {len(ood_sessions)}")

    # Load in-dist baseline firing rates
    in_dist_results: dict[str, float] = {}
    if IN_DIST_RESULTS.exists():
        for r in json.loads(IN_DIST_RESULTS.read_text(encoding="utf-8")):
            in_dist_results[r["heuristic"]] = r["prevalence"]

    # Per-session evaluation
    session_reports: list[dict] = []
    h_counts: dict[str, dict[str, int]] = {
        "h1_retry": {"positive": 0, "total": 0},
        "h2_redundant_read": {"positive": 0, "total": 0},
        "h3_backtrack": {"positive": 0, "total": 0},
        "h4_tool_result_used": {"positive": 0, "total": 0},
    }
    domain_counts: dict[str, int] = defaultdict(int)
    h3_examples: list[dict] = []
    h4_low_rate_examples: list[dict] = []

    for s in ood_sessions:
        domain = classify_domain(s["instance_id"])
        domain_counts[domain] += 1

        h_results = compute_heuristics(s["turns"])
        session_row: dict = {
            "session_id": s["session_id"],
            "scaffold": s["scaffold"],
            "ood_source": s.get("ood_source", ""),
            "ood_task_category": s.get("ood_task_category", ""),
            "domain": domain,
            "turn_count": s["turn_count"],
        }

        for key, turns_list in h_results.items():
            pos = sum(1 for t in turns_list if t["value"])
            total = len(turns_list)
            h_counts[key]["positive"] += pos
            h_counts[key]["total"] += total
            session_row[f"{key}_rate"] = round(pos / total, 3) if total else 0.0

        # Collect H3 examples for inspection
        for tr in h_results["h3_backtrack"]:
            if tr["value"]:
                orig_turn = next(
                    (t for t in s["turns"] if t["turn_index"] == tr["turn_index"]), None
                )
                if orig_turn and len(h3_examples) < 5:
                    h3_examples.append({
                        "session_id": s["session_id"][:8],
                        "scaffold": s["scaffold"],
                        "ood_category": s.get("ood_task_category", ""),
                        "turn_index": tr["turn_index"],
                        "content_snippet": orig_turn["content_text"][:200],
                    })

        session_reports.append(session_row)

    # Summary
    print("\n=== DOMAIN CLASSIFICATION ===")
    total_ood = len(ood_sessions)
    unknown_n = domain_counts.get("unknown", 0)
    print(f"  Known domain:   {total_ood - unknown_n}/{total_ood} ({100*(total_ood-unknown_n)//max(total_ood,1)}%)")
    print(f"  Unknown domain: {unknown_n}/{total_ood} ({100*unknown_n//max(total_ood,1)}%)")
    for d, cnt in sorted(domain_counts.items(), key=lambda x: -x[1]):
        print(f"    {d:<30} {cnt}")

    print("\n=== HEURISTIC FIRING RATES (OOD vs in-dist) ===")
    heuristic_map = {
        "h1_retry": "H1_is_retry",
        "h2_redundant_read": "H2_redundant_read",
        "h3_backtrack": "H3_is_backtrack",
        "h4_tool_result_used": "H4_tool_result_used",
    }
    ood_firing: dict[str, float] = {}
    for key, counts in h_counts.items():
        rate = counts["positive"] / counts["total"] if counts["total"] else 0.0
        ood_firing[key] = rate
        in_dist_prev = in_dist_results.get(heuristic_map.get(key, ""), None)
        in_str = f"{in_dist_prev:.3f}" if in_dist_prev is not None else "N/A"
        delta = f"{rate - in_dist_prev:+.3f}" if in_dist_prev is not None else "N/A"
        print(f"  {key:<25}  OOD={rate:.3f}  in-dist={in_str}  delta={delta}  "
              f"({counts['positive']}/{counts['total']} turns)")

    print("\n=== H3 BACKTRACK EXAMPLES (OOD corpus) ===")
    if h3_examples:
        for ex in h3_examples:
            print(f"  [{ex['scaffold']}] cat={ex['ood_category'][:30]} t{ex['turn_index']}:")
            print(f"    {ex['content_snippet'][:150]}")
    else:
        print("  No H3 positives in OOD corpus.")

    print("\n=== PER-SESSION SUMMARY ===")
    for r in session_reports:
        print(f"  [{r['scaffold']:<15}] cat={r['ood_task_category'][:25]:<25} "
              f"domain={r['domain']:<15} "
              f"H1={r['h1_retry_rate']:.2f} H2={r['h2_redundant_read_rate']:.2f} "
              f"H3={r['h3_backtrack_rate']:.2f} H4={r['h4_tool_result_used_rate']:.2f}")

    # Write output
    out = {
        "n_ood_sessions": total_ood,
        "domain_classification": dict(domain_counts),
        "domain_known_rate": round((total_ood - unknown_n) / max(total_ood, 1), 3),
        "heuristic_firing_rates": {
            k: {
                "ood_rate": round(h_counts[k]["positive"] / max(h_counts[k]["total"], 1), 3),
                "in_dist_prevalence": in_dist_results.get(heuristic_map.get(k, ""), None),
                "n_positive": h_counts[k]["positive"],
                "n_total": h_counts[k]["total"],
            }
            for k in h_counts
        },
        "h3_examples": h3_examples,
        "per_session": session_reports,
    }
    out_path = OUT_DIR / "ood_evaluation.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nOOD evaluation written to {out_path}")


if __name__ == "__main__":
    main()
