"""Debug tool_use content in normalized traces."""
from __future__ import annotations
import json, pathlib, random

random.seed(42)
files = list(pathlib.Path("data/validation-corpus/traces_normalized").glob("*.json"))
random.shuffle(files)

for f in files[:10]:
    s = json.loads(f.read_text(encoding="utf-8"))
    for t in s["turns"]:
        if t["role"] in ("assistant", "ai") and t["tool_uses"]:
            print(f"\n[{s['scaffold']}] session={s['session_id'][:8]} turn={t['turn_index']}")
            for tu in t["tool_uses"][:2]:
                print(f"  tool_name: {tu['tool_name']}")
                print(f"  tool_input: {str(tu['tool_input'])[:100]}")
                print(f"  tool_result: {str(tu['tool_result'])[:80] if tu['tool_result'] else 'None'}")
            # Also show surrounding turns
            break

# Check which turns HAVE content_text
print("\n--- Tool-bearing turns by scaffold ---")
counts = {}
for f in files:
    s = json.loads(f.read_text(encoding="utf-8"))
    sc = s["scaffold"]
    counts.setdefault(sc, {"tool_turns": 0, "with_result": 0, "with_path": 0})
    for t in s["turns"]:
        if t["role"] in ("assistant", "ai") and t["tool_uses"]:
            counts[sc]["tool_turns"] += 1
            for tu in t["tool_uses"]:
                if tu["tool_result"]:
                    counts[sc]["with_result"] += 1
                inp = tu["tool_input"]
                if isinstance(inp, dict) and any(k in inp for k in ["path", "file_path", "command", "filename"]):
                    counts[sc]["with_path"] += 1
                elif isinstance(inp, str) and ("/" in inp or "." in inp):
                    counts[sc]["with_path"] += 1

for sc, c in counts.items():
    print(f"  {sc}: {c['tool_turns']} tool-bearing turns, {c['with_result']} have result, {c['with_path']} have path")
