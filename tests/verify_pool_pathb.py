from __future__ import annotations

"""tests/verify_pool_pathb.py — GUARDRAIL 3: verify PATH-B pool re-run is byte-identical to B4.

Loads B4 reference PATH-B events from data/pool_waste_signals.jsonl, re-runs
detect_redundant_read on the full pool (181 sessions), and confirms:
  - Same set of session IDs with PATH-B events
  - For each session, same events (turns list, evidence dict values)
"""

import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from waste_detectors import detect_redundant_read

POOL_PATH = ROOT / "data" / "corpus_pool" / "pool_adapted.jsonl"
B4_SIGNALS_PATH = ROOT / "data" / "pool_waste_signals.jsonl"


def load_b4_pathb() -> dict[str, list[dict]]:
    """Load B4 reference: {session_id: [PATH-B WasteEvent dicts]}."""
    b4: dict[str, list[dict]] = {}
    with B4_SIGNALS_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            pathb = [e for e in r.get("waste_events", []) if e.get("evidence", {}).get("path") == "B"]
            if pathb:
                b4[r["session_id"]] = pathb
    return b4


def load_pool() -> list[dict]:
    return [json.loads(line) for line in POOL_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_pathb_on_pool(pool: list[dict]) -> dict[str, list[dict]]:
    """Re-run PATH-B on all pool sessions; return {session_id: [PATH-B event dicts]}."""
    result: dict[str, list[dict]] = {}
    for row in pool:
        sid: str = row["session_id"]
        turns: list[dict] = row["digest"]["turns"]
        events = detect_redundant_read(sid, turns)
        pathb = [dataclasses.asdict(e) for e in events if e.evidence.get("path") == "B"]
        if pathb:
            result[sid] = pathb
    return result


def compare_events(b4_events: list[dict], new_events: list[dict]) -> list[str]:
    """Return list of discrepancy descriptions, empty if identical."""
    issues: list[str] = []
    if len(b4_events) != len(new_events):
        issues.append(f"event count: B4={len(b4_events)}, new={len(new_events)}")
        return issues
    for i, (b4e, newe) in enumerate(zip(b4_events, new_events)):
        if b4e["turns"] != newe["turns"]:
            issues.append(f"event[{i}] turns: B4={b4e['turns']}, new={newe['turns']}")
        for k in ("path", "gap", "call_1_turn", "result_1_turn", "call_2_turn", "result_2_turn",
                  "content_snippet"):
            b4v = b4e.get("evidence", {}).get(k)
            newv = newe.get("evidence", {}).get(k)
            if b4v != newv:
                issues.append(f"event[{i}] evidence[{k!r}]: B4={b4v!r}, new={newv!r}")
    return issues


def main() -> None:
    print("Loading B4 reference PATH-B events …")
    b4 = load_b4_pathb()
    print(f"  B4 sessions with PATH-B events: {len(b4)}")

    print("Loading pool and re-running detect_redundant_read …")
    pool = load_pool()
    new = run_pathb_on_pool(pool)
    print(f"  New run sessions with PATH-B events: {len(new)}")

    b4_sids = set(b4.keys())
    new_sids = set(new.keys())

    all_ok = True

    # --- Session ID set comparison ---
    if b4_sids != new_sids:
        all_ok = False
        only_b4 = b4_sids - new_sids
        only_new = new_sids - b4_sids
        if only_b4:
            print(f"MISMATCH: sessions in B4 but not new run: {only_b4}")
        if only_new:
            print(f"MISMATCH: sessions in new run but not B4: {only_new}")
    else:
        print(f"  Session ID set: MATCH ({len(b4_sids)} sessions)")

    # --- Per-session event comparison ---
    mismatched_sessions = 0
    for sid in sorted(b4_sids & new_sids):
        issues = compare_events(b4[sid], new[sid])
        if issues:
            all_ok = False
            mismatched_sessions += 1
            print(f"  MISMATCH session {sid}:")
            for issue in issues:
                print(f"    {issue}")

    if all_ok:
        print(f"\nPool PATH-B: {len(b4_sids)}/{len(b4_sids)} sessions match B4 reference exactly")
    else:
        print(f"\nPool PATH-B: FAILED — {mismatched_sessions} session(s) have discrepancies")
        sys.exit(1)


if __name__ == "__main__":
    main()
