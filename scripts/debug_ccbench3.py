"""Inspect CC-Bench trajectory format."""
from __future__ import annotations
import json
from datasets import load_dataset, VerificationMode

ds = load_dataset("zai-org/CC-Bench-trajectories", split="train",
                  verification_mode=VerificationMode.NO_CHECKS)
row = ds[0]
print("Keys:", list(row.keys()))
traj_raw = row.get("trajectory") or ""
print(f"trajectory type: {type(traj_raw).__name__}, len={len(traj_raw)}")
print("First 300 chars:", repr(traj_raw[:300]))

# Try parsing as JSON
try:
    parsed = json.loads(traj_raw)
    print(f"\nParsed as JSON: type={type(parsed).__name__}")
    if isinstance(parsed, list):
        print(f"  num turns: {len(parsed)}")
        print(f"  first turn keys: {list(parsed[0].keys()) if isinstance(parsed[0], dict) else type(parsed[0])}")
        print(f"  first turn: {str(parsed[0])[:200]}")
except Exception as e:
    print(f"Not JSON: {e}")

# Check other interesting fields
for k in row.keys():
    val = row[k]
    if isinstance(val, str) and len(val) > 0:
        print(f"\n{k} (len={len(val)}): {repr(val[:100])}")
    elif val is not None:
        print(f"\n{k}: {val}")
