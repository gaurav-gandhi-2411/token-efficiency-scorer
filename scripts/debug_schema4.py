"""Find task description turn and check token totals coverage."""
from __future__ import annotations
import json, pathlib, collections

traces_dir = pathlib.Path("data/validation-corpus/traces_normalized")
files = sorted(traces_dir.glob("*.json"))

print("=== Task description turn (first non-empty user turn) ===")
for f in files[:6]:
    s = json.loads(f.read_text())
    task_text = ""
    for t in s["turns"]:
        if t["role"] in ("user",) and len(t["content_text"]) > 50:
            task_text = t["content_text"][:400]
            break
    print(f"\n[{s['scaffold']}] {s['instance_id'][:40]}")
    print("  ", task_text[:300].replace("\n", " "))

print("\n=== Token totals coverage ===")
has_output = sum(1 for f in files
                 if json.loads(f.read_text()).get("session_token_totals", {}).get("output", 0) > 0)
has_input = sum(1 for f in files
                if json.loads(f.read_text()).get("session_token_totals", {}).get("input", 0) > 0)
print(f"  sessions with input tokens: {has_input}/200")
print(f"  sessions with output tokens: {has_output}/200")

# Check output tokens by scaffold
by_scaffold: dict[str, list[int]] = collections.defaultdict(list)
for f in files:
    s = json.loads(f.read_text())
    tot = s.get("session_token_totals") or {}
    by_scaffold[s["scaffold"]].append(tot.get("output", 0))

print()
for sc, vals in by_scaffold.items():
    nonzero = sum(1 for v in vals if v > 0)
    print(f"  {sc}: {nonzero}/{len(vals)} have output tokens, "
          f"mean_total={sum(vals)/len(vals):.0f}")
