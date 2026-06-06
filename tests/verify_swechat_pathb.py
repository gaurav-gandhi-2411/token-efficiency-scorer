from __future__ import annotations

"""tests/verify_swechat_pathb.py — GUARDRAIL 4: confirm PATH-B now fires on SWE-chat CC sessions.

After the dual-format regex fix (tab OR arrow format), PATH-B should detect redundant reads
in CC v2.1.38+ sessions from the SWE-chat corpus.  This script counts how many sessions fire
and prints the rate -- if still 0, the fix did not reach the actual content format.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from waste_detectors import detect_redundant_read

SWECHAT_PATH = ROOT / "data" / "swechat_cc_adapted.jsonl"


def main() -> None:
    print(f"Loading SWE-chat CC sessions from {SWECHAT_PATH} …")
    sessions = [
        json.loads(line)
        for line in SWECHAT_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    total = len(sessions)
    print(f"  Total sessions: {total}")

    pathb_fire_count = 0
    pathb_event_total = 0
    sample_snippets: list[str] = []

    for row in sessions:
        sid: str = row["session_id"]
        turns: list[dict] = row["digest"]["turns"]
        events = detect_redundant_read(sid, turns)
        pathb = [e for e in events if e.evidence.get("path") == "B"]
        if pathb:
            pathb_fire_count += 1
            pathb_event_total += len(pathb)
            if len(sample_snippets) < 3:
                snippet = pathb[0].evidence.get("content_snippet", "")[:80]
                sample_snippets.append(f"  session={sid[:8]} gap={pathb[0].evidence.get('gap')} "
                                       f"snippet={snippet!r}")

    fire_rate = pathb_fire_count / total * 100 if total else 0.0
    print(f"\nResults:")
    print(f"  Total sessions:          {total}")
    print(f"  PATH-B fire count:       {pathb_fire_count}")
    print(f"  PATH-B total events:     {pathb_event_total}")
    print(f"  PATH-B fire rate:        {fire_rate:.2f}%")

    if sample_snippets:
        print(f"\nSample PATH-B events (up to 3):")
        for s in sample_snippets:
            print(s.encode("ascii", errors="replace").decode("ascii"))

    if pathb_fire_count == 0:
        print("\nWARNING: PATH-B still fires 0 on SWE-chat CC — regex may not match actual format.")
        print("Inspect a sample content_snippet from a tool turn in swechat_cc_adapted.jsonl.")
        sys.exit(1)
    else:
        print(f"\nPATH-B fix confirmed: {pathb_fire_count}/{total} SWE-chat CC sessions fire ({fire_rate:.2f}%)")


if __name__ == "__main__":
    main()
