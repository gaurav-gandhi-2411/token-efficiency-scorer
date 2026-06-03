from __future__ import annotations

"""run_waste_analysis.py — Run approved detectors over the 143-session pool.

Outputs data/pool_waste_signals.jsonl: one record per session, with all
fired waste events (detector, turns, evidence).  Currently runs only
REPEATED-FAILED-RETRY (the first approved detector in the B4 build order).
"""

import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from waste_detectors import WasteEvent, detect_repeated_failed_retry

POOL_PATH = ROOT / "data" / "corpus_pool" / "pool_adapted.jsonl"
JUDGE_PATH = ROOT / "data" / "pool_judge_scores.jsonl"
OUTPUT_PATH = ROOT / "data" / "pool_waste_signals.jsonl"


def load_pool() -> list[dict]:
    return [json.loads(l) for l in POOL_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_judge_index() -> dict[str, dict]:
    """Return {session_id: score_record} for quick lookup."""
    index: dict[str, dict] = {}
    for line in JUDGE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        index[r["session_id"]] = r
    return index


def run_detectors(session_id: str, turns: list[dict]) -> list[WasteEvent]:
    """Run all approved detectors; extend here as detectors are approved."""
    events: list[WasteEvent] = []
    events.extend(detect_repeated_failed_retry(session_id, turns))
    return events


def main() -> None:
    pool = load_pool()
    judge_idx = load_judge_index()

    total_sessions = len(pool)
    sessions_with_events = 0
    total_events = 0
    fire_rate_by_detector: dict[str, int] = {}

    records: list[dict] = []

    for row in pool:
        session_id: str = row["session_id"]
        turns: list[dict] = row["digest"]["turns"]

        events = run_detectors(session_id, turns)

        judge_rec = judge_idx.get(session_id, {})
        record = {
            "session_id": session_id,
            "turn_count": row["turn_count"],
            "qwen_verdict": judge_rec.get("verdict"),
            "qwen_waste_categories": judge_rec.get("waste_categories", []),
            "waste_events": [dataclasses.asdict(e) for e in events],
            "waste_event_count": len(events),
        }
        records.append(record)

        if events:
            sessions_with_events += 1
            total_events += len(events)
            for e in events:
                fire_rate_by_detector[e.detector] = fire_rate_by_detector.get(e.detector, 0) + 1

    OUTPUT_PATH.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )

    print(f"\n=== REPEATED-FAILED-RETRY pool-wide results ===")
    print(f"Pool size:             {total_sessions} sessions")
    print(f"Sessions with events:  {sessions_with_events}  ({sessions_with_events/total_sessions*100:.1f}%)")
    print(f"Total events fired:    {total_events}")
    for det, count in fire_rate_by_detector.items():
        print(f"  {det}: {count} sessions")

    # Cross-check: among sessions that fired, what were Qwen's verdicts?
    fired_ids = {r["session_id"] for r in records if r["waste_events"]}
    verdict_counts: dict[str, int] = {}
    for r in records:
        if r["session_id"] in fired_ids:
            v = r["qwen_verdict"] or "unscored"
            verdict_counts[v] = verdict_counts.get(v, 0) + 1

    print(f"\nQwen verdict breakdown among {sessions_with_events} sessions that fired:")
    for v, c in sorted(verdict_counts.items(), key=lambda x: -x[1]):
        print(f"  {v}: {c}")

    # Sample evidence from first 3 fired sessions
    print("\n--- Sample events (first 3 sessions with fires) ---")
    shown = 0
    for r in records:
        if r["waste_events"] and shown < 3:
            sid_short = r["session_id"][:8]
            print(f"\n  Session {sid_short} (turns={r['turn_count']}, qwen={r['qwen_verdict']}):")
            for ev in r["waste_events"][:2]:
                print(f"    detector={ev['detector']}, repeat_count={ev['repeat_count']}")
                print(f"    proof turns: {ev['turns']}")
                snip = ev["evidence"].get("error_snippet", "")[:100]
                print(f"    error: {repr(snip)}")
            shown += 1

    print(f"\nOutput written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
