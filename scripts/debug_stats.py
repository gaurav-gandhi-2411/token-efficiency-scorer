"""Quick corpus statistics."""
from __future__ import annotations
import json
import pathlib
import statistics

files = list(pathlib.Path("data/validation-corpus/traces_normalized").glob("*.json"))
turn_counts: list[int] = []
by_scaffold: dict[str, list[int]] = {}

for f in files:
    s = json.loads(f.read_text(encoding="utf-8"))
    tc = s["turn_count"]
    turn_counts.append(tc)
    by_scaffold.setdefault(s["scaffold"], []).append(tc)

turn_counts.sort()
n = len(turn_counts)
print(f"Total sessions: {n}")
print(f"Turn counts: min={min(turn_counts)} median={statistics.median(turn_counts):.0f} "
      f"p75={turn_counts[int(n*0.75)]} max={max(turn_counts)}")
for sc, tcs in sorted(by_scaffold.items()):
    tcs.sort()
    print(f"  {sc}: n={len(tcs)} median={statistics.median(tcs):.0f} max={max(tcs)}")
