"""Load CC-Bench-trajectories with verification bypass and inspect schema."""
from __future__ import annotations
from datasets import load_dataset
ds = load_dataset("zai-org/CC-Bench-trajectories", split="train", ignore_verifications=True)
print("Rows:", len(ds))
print("Keys:", list(ds[0].keys()))
print("Categories:", list(set(r.get("task_category","?") for r in ds)))
row = ds[0]
print("\nFirst row sample:")
for k in ["task_id", "task_category", "model_name", "success"]:
    print(f"  {k}: {str(row.get(k,''))[:80]}")
traj = row.get("trajectory") or row.get("messages") or []
print(f"  trajectory length: {len(traj)}")
if traj:
    print(f"  first turn keys: {list(traj[0].keys())}")
    print(f"  first turn role: {traj[0].get('role', '?')}")
    content = traj[0].get("content", "")
    print(f"  content type: {type(content).__name__}")
    if isinstance(content, list):
        print(f"  content items: {[x.get('type','?') if isinstance(x,dict) else type(x).__name__ for x in content[:4]]}")
