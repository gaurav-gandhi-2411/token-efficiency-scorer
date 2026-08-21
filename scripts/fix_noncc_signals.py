from __future__ import annotations

"""fix_noncc_signals.py — One-shot fixer for missing non-CC detector results.

Reads swechat_noncc_adapted.jsonl (already adapted in previous run),
runs frozen detectors, appends to public_waste_signals.jsonl, and
rewrites generalization_compare.json with correct non-CC stats.

Does NOT touch waste_detectors.py or claudecode_adapter.py.
"""

import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from waste_detectors import detect_redundant_read, detect_repeated_failed_retry

# Paths
NONCC_ADAPTED = ROOT / "data" / "swechat_noncc_adapted.jsonl"
SIGNALS_PATH = ROOT / "data" / "public_waste_signals.jsonl"
COMPARE_PATH = ROOT / "data" / "generalization_compare.json"

# B4 pool baseline
_POOL_SESSIONS = 181
_POOL_RFR_SESSIONS = 12
_POOL_RR_ANY_SESSIONS = 20
_POOL_RR_PATH_A_SESSIONS = 4
_POOL_RR_PATH_B_SESSIONS = 18


def main() -> None:
    if not NONCC_ADAPTED.exists():
        print(f"ERROR: {NONCC_ADAPTED} not found", file=sys.stderr)
        sys.exit(1)

    lines = NONCC_ADAPTED.read_text(encoding="utf-8").splitlines()
    noncc_records = [json.loads(l) for l in lines if l.strip()]
    print(f"Non-CC sessions loaded: {len(noncc_records)}")

    noncc_signals = []
    agent_counts: dict[str, int] = {}

    try:
        from tqdm import tqdm

        iterator = tqdm(noncc_records, desc="Detecting non-CC", unit="session")
    except ImportError:
        iterator = noncc_records  # type: ignore[assignment]

    for row in iterator:
        session_id: str = row["session_id"]
        turns = row["digest"]["turns"]
        agent_type: str = row.get("agent_type", "unknown")

        rfr_events = detect_repeated_failed_retry(session_id, turns)
        rr_events = detect_redundant_read(session_id, turns)
        all_events = rfr_events + rr_events

        path_a_events = [e for e in rr_events if e.evidence.get("path") == "A"]
        path_b_events = [e for e in rr_events if e.evidence.get("path") == "B"]

        noncc_signals.append(
            {
                "session_id": session_id,
                "source": "swechat_noncc",
                "agent_type": agent_type,
                "turn_count": row.get("turn_count", len(turns)),
                "waste_events": [dataclasses.asdict(e) for e in all_events],
                "waste_event_count": len(all_events),
                "rfr_fired": bool(rfr_events),
                "rr_fired": bool(rr_events),
                "path_a_fired": bool(path_a_events),
                "path_b_fired": bool(path_b_events),
            }
        )

        agent_counts[agent_type] = agent_counts.get(agent_type, 0) + 1

    noncc_rfr = sum(1 for r in noncc_signals if r["rfr_fired"])
    noncc_rr = sum(1 for r in noncc_signals if r["rr_fired"])
    noncc_pa = sum(1 for r in noncc_signals if r["path_a_fired"])
    noncc_pb = sum(1 for r in noncc_signals if r["path_b_fired"])

    print(
        f"Non-CC results: {len(noncc_signals)} sessions | "
        f"RFR fired: {noncc_rfr} | RR fired: {noncc_rr} "
        f"(PATH A: {noncc_pa}, PATH B: {noncc_pb})"
    )
    print(f"Agent breakdown: {agent_counts}")

    # --- Read existing CC signals ---
    if not SIGNALS_PATH.exists():
        print(f"ERROR: {SIGNALS_PATH} not found - run CC detector phase first", file=sys.stderr)
        sys.exit(1)

    cc_lines = SIGNALS_PATH.read_text(encoding="utf-8").splitlines()
    cc_signals = [json.loads(l) for l in cc_lines if l.strip()]

    # Validate: all existing signals should be CC source
    noncc_in_file = [r for r in cc_signals if r.get("source") != "swechat_cc"]
    if noncc_in_file:
        print(
            f"WARNING: {len(noncc_in_file)} non-CC records already in signals file — removing duplicates",
            file=sys.stderr,
        )
        cc_signals = [r for r in cc_signals if r.get("source") == "swechat_cc"]

    print(f"CC signals loaded: {len(cc_signals)}")

    # --- Rebuild combined signals file ---
    all_signals = cc_signals + noncc_signals
    SIGNALS_PATH.write_text(
        "\n".join(json.dumps(r) for r in all_signals) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(all_signals)} total signal records to {SIGNALS_PATH}")

    # --- Rebuild compare JSON ---
    existing_compare = json.loads(COMPARE_PATH.read_text(encoding="utf-8"))
    swechat_cc = existing_compare["swechat_cc"]
    cc_n = swechat_cc["sessions"]
    distinct_users = swechat_cc["distinct_users"]

    cc_rfr = swechat_cc["rfr_sessions_fired"]
    cc_rr = swechat_cc["rr_sessions_fired"]
    cc_pa = swechat_cc["path_a_sessions"]
    cc_pb = swechat_cc["path_b_sessions"]

    noncc_n = len(noncc_signals)
    noncc_rfr_rate = noncc_rfr / noncc_n if noncc_n else 0.0

    compare = {
        "cc_pool": {
            "sessions": _POOL_SESSIONS,
            "rfr_fire_rate": round(_POOL_RFR_SESSIONS / _POOL_SESSIONS, 4),
            "rr_fire_rate": round(_POOL_RR_ANY_SESSIONS / _POOL_SESSIONS, 4),
            "path_a_sessions": _POOL_RR_PATH_A_SESSIONS,
            "path_b_sessions": _POOL_RR_PATH_B_SESSIONS,
        },
        "swechat_cc": {
            "sessions": cc_n,
            "distinct_users": distinct_users,
            "rfr_sessions_fired": cc_rfr,
            "rfr_fire_rate": round(cc_rfr / cc_n if cc_n else 0.0, 4),
            "rr_sessions_fired": cc_rr,
            "rr_fire_rate": round(cc_rr / cc_n if cc_n else 0.0, 4),
            "path_a_sessions": cc_pa,
            "path_a_note": (
                "UNAVAILABLE - CC v2.1.38 may have dropped hint"
                if cc_pa == 0
                else f"{cc_pa} sessions fired"
            ),
            "path_b_sessions": cc_pb,
            "path_b_note": (
                "UNAVAILABLE - CC v2.1.38 uses arrow format not \\d+\\t"
                if cc_pb == 0
                else f"{cc_pb} sessions fired"
            ),
        },
        "swechat_noncc": {
            "sessions": noncc_n,
            "agent_breakdown": agent_counts,
            "rfr_sessions_fired": noncc_rfr,
            "rfr_fire_rate": round(noncc_rfr_rate, 4),
            "rr_sessions_fired": noncc_rr,
            "path_a_sessions": noncc_pa,
            "path_a_note": "UNAVAILABLE - 'File unchanged since last read' is CC-proprietary",
            "path_b_sessions": noncc_pb,
            "path_b_note": "UNAVAILABLE - non-CC Read output lacks \\d+\\t format",
            "mapping_note": "tool-name mapping approximate; spurious fires are expected findings",
        },
    }

    COMPARE_PATH.write_text(json.dumps(compare, indent=2), encoding="utf-8")
    print(f"Wrote updated compare JSON to {COMPARE_PATH}")

    # --- Print summary ---
    pool_rfr_pct = _POOL_RFR_SESSIONS / _POOL_SESSIONS * 100
    cc_rfr_pct = cc_rfr / cc_n * 100 if cc_n else 0.0
    noncc_rfr_pct = noncc_rfr_rate * 100

    print("\n=== GENERALIZATION SUMMARY ===")
    print(
        f"CC pool (B4):          {_POOL_RFR_SESSIONS}/{_POOL_SESSIONS} = {pool_rfr_pct:.1f}% RFR | 1 developer"
    )
    print(
        f"SWE-chat CC:           {cc_rfr}/{cc_n} = {cc_rfr_pct:.1f}% RFR | {distinct_users} users"
    )
    print(
        f"SWE-chat non-CC:       {noncc_rfr}/{noncc_n} = {noncc_rfr_pct:.1f}% RFR | {agent_counts}"
    )
    print(f"PATH A (SWE-chat CC):  {cc_pa} sessions (pool: {_POOL_RR_PATH_A_SESSIONS})")
    print("PATH B (SWE-chat CC):  UNAVAILABLE (CC v2.1.38 format change)")
    print("PATH B (non-CC):       UNAVAILABLE (no \\d+\\t format in non-CC agents)")


if __name__ == "__main__":
    main()
