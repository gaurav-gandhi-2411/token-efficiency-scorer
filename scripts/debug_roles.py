"""Check role distribution across all scaffolds."""
from __future__ import annotations
import json, pathlib, collections

files = list(pathlib.Path("data/validation-corpus/traces_normalized").glob("*.json"))
role_counts: dict[str, collections.Counter] = {}

for f in files:
    s = json.loads(f.read_text(encoding="utf-8"))
    sc = s["scaffold"]
    role_counts.setdefault(sc, collections.Counter())
    for t in s["turns"]:
        role_counts[sc][t["role"]] += 1

for sc, ctr in sorted(role_counts.items()):
    print(f"{sc}: {dict(ctr.most_common())}")

# Also check content length distribution for non-system roles
print("\nContent length check (first 5 sessions, non-system turns):")
import random; random.seed(42)
sample = random.sample(files, 5)
for f in sample:
    s = json.loads(f.read_text(encoding="utf-8"))
    turns_with_content = [(t["turn_index"], t["role"], len(t["content_text"]))
                          for t in s["turns"] if t["role"] != "system" and len(t["content_text"]) > 10]
    print(f"  {s['scaffold']} {s['session_id'][:8]}: {turns_with_content[:5]}")
