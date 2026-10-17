# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""run_waste_analysis.py — Run approved detectors over an adapted-session pool.

Usage (every path is explicit; the maintainer's pool is not in the repository):
    python scripts/run_waste_analysis.py --pool POOL.jsonl --judge-scores SCORES.jsonl \
        --output SIGNALS.jsonl

Writes one record per session to --output, with all
fired waste events (detector, turns, evidence).  Detectors approved so far:
  - REPEATED-FAILED-RETRY (B4 step 2-3)
  - REDUNDANT-READ        (B4 step 3)
"""

import argparse
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from waste_detectors import WasteEvent, detect_redundant_read, detect_repeated_failed_retry


def load_pool(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_judge_index(path: Path) -> dict[str, dict]:
    """Return {session_id: score_record} for quick lookup."""
    index: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
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
    events.extend(detect_redundant_read(session_id, turns))
    return events


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the waste detectors over a pool.")
    parser.add_argument("--pool", type=Path, required=True, help="adapted-session pool (JSONL)")
    parser.add_argument("--judge-scores", type=Path, required=True, help="judge scores (JSONL)")
    parser.add_argument("--output", type=Path, required=True, help="waste signals to write (JSONL)")
    args = parser.parse_args(argv)
    pool = load_pool(args.pool)
    judge_idx = load_judge_index(args.judge_scores)

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

    args.output.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )

    _report(records, total_sessions, args.output)


def _report(records: list[dict], total_sessions: int, output_path: Path) -> None:
    """Print pool-wide summary with per-detector and per-path breakdowns."""
    from collections import Counter

    worse_verdicts = {"WORSE", "MUCH_WORSE"}
    good_verdicts = {"MUCH_BETTER", "BETTER", "SIMILAR"}

    # ------------------------------------------------------------------ #
    # REPEATED-FAILED-RETRY summary
    # ------------------------------------------------------------------ #
    rfr_fired = [
        r
        for r in records
        if any(e["detector"] == "REPEATED-FAILED-RETRY" for e in r["waste_events"])
    ]
    print("\n=== REPEATED-FAILED-RETRY ===")
    print(
        f"Sessions fired: {len(rfr_fired)}/{total_sessions} ({len(rfr_fired) / total_sessions * 100:.1f}%)"
    )
    rfr_verdicts = Counter((r["qwen_verdict"] or "unscored") for r in rfr_fired)
    for v, c in rfr_verdicts.most_common():
        print(f"  qwen={v}: {c}")
    worse_fired_rfr = sum(1 for r in rfr_fired if r["qwen_verdict"] in worse_verdicts)
    worse_total = sum(1 for r in records if r["qwen_verdict"] in worse_verdicts)
    good_fired_rfr = sum(1 for r in rfr_fired if r["qwen_verdict"] in good_verdicts)
    good_total = sum(1 for r in records if r["qwen_verdict"] in good_verdicts)
    print(
        f"  4-way: fired+WORSE={worse_fired_rfr}/{worse_total}, fired+good={good_fired_rfr}/{good_total}, "
        f"no-fire+WORSE={worse_total - worse_fired_rfr}/{worse_total}, no-fire+good={good_total - good_fired_rfr}/{good_total}"
    )

    # ------------------------------------------------------------------ #
    # REDUNDANT-READ summary — per-path breakdown + gap distribution
    # ------------------------------------------------------------------ #
    rr_fired = [
        r for r in records if any(e["detector"] == "REDUNDANT-READ" for e in r["waste_events"])
    ]
    rr_events_a = [
        e
        for r in records
        for e in r["waste_events"]
        if e["detector"] == "REDUNDANT-READ" and e["evidence"].get("path") == "A"
    ]
    rr_events_b = [
        e
        for r in records
        for e in r["waste_events"]
        if e["detector"] == "REDUNDANT-READ" and e["evidence"].get("path") == "B"
    ]

    # Sessions with at least one PATH A event
    rr_a_sids = {
        r["session_id"]
        for r in records
        if any(
            e["detector"] == "REDUNDANT-READ" and e["evidence"].get("path") == "A"
            for e in r["waste_events"]
        )
    }
    rr_b_sids = {
        r["session_id"]
        for r in records
        if any(
            e["detector"] == "REDUNDANT-READ" and e["evidence"].get("path") == "B"
            for e in r["waste_events"]
        )
    }

    print("\n=== REDUNDANT-READ ===")
    print(
        f"Sessions fired (any path): {len(rr_fired)}/{total_sessions} ({len(rr_fired) / total_sessions * 100:.1f}%)"
    )
    print(f"  PATH A (CC hint): {len(rr_a_sids)} sessions, {len(rr_events_a)} events")
    print(f"  PATH B (content): {len(rr_b_sids)} sessions, {len(rr_events_b)} events")
    print(f"  PATH A+B overlap: {len(rr_a_sids & rr_b_sids)} sessions")

    # PATH B gap distribution
    if rr_events_b:
        gap_counts = Counter(e["evidence"]["gap"] for e in rr_events_b)
        print("  PATH B gap distribution (call_2.turn_idx - result_1.turn_idx):")
        for g in sorted(gap_counts):
            print(f"    gap={g}: {gap_counts[g]} events")

    # Qwen verdict breakdown
    rr_verdicts = Counter((r["qwen_verdict"] or "unscored") for r in rr_fired)
    print("  Qwen verdict breakdown:")
    for v, c in rr_verdicts.most_common():
        print(f"    qwen={v}: {c}")

    # 4-way Qwen cross-check (session-level, any REDUNDANT-READ event)
    rr_fired_sids = {r["session_id"] for r in rr_fired}
    worse_fired_rr = sum(
        1
        for r in records
        if r["session_id"] in rr_fired_sids and r["qwen_verdict"] in worse_verdicts
    )
    good_fired_rr = sum(
        1
        for r in records
        if r["session_id"] in rr_fired_sids and r["qwen_verdict"] in good_verdicts
    )
    print(
        f"  4-way: fired+WORSE={worse_fired_rr}/{worse_total}, fired+good={good_fired_rr}/{good_total}, "
        f"no-fire+WORSE={worse_total - worse_fired_rr}/{worse_total}, no-fire+good={good_total - good_fired_rr}/{good_total}"
    )

    # Sample PATH A and PATH B events
    print("\n--- REDUNDANT-READ sample events ---")
    shown_a = shown_b = 0
    for r in records:
        for ev in r["waste_events"]:
            if ev["detector"] != "REDUNDANT-READ":
                continue
            path = ev["evidence"].get("path")
            snip = ev["evidence"].get("content_snippet", "")[:80]
            sid = r["session_id"][:8]
            if path == "A" and shown_a < 2:
                print(f"  [A] {sid} (qwen={r['qwen_verdict']}) turns={ev['turns']}: {repr(snip)}")
                shown_a += 1
            elif path == "B" and shown_b < 2:
                gap = ev["evidence"].get("gap")
                print(
                    f"  [B] {sid} (qwen={r['qwen_verdict']}) gap={gap} turns={ev['turns']}: {repr(snip)}"
                )
                shown_b += 1
        if shown_a >= 2 and shown_b >= 2:
            break

    print(f"\nOutput written to {output_path}")


if __name__ == "__main__":
    main()
