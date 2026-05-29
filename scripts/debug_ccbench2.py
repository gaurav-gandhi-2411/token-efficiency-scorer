"""Load CC-Bench-trajectories with verification_mode bypass."""
from __future__ import annotations
from datasets import load_dataset, VerificationMode
ds = load_dataset(
    "zai-org/CC-Bench-trajectories",
    split="train",
    verification_mode=VerificationMode.NO_CHECKS,
)
print("Rows:", len(ds))
print("Keys:", list(ds[0].keys()))
print("Categories:", list(set(r.get("task_category","?") for r in ds)))
row = ds[0]
for k in ["task_id", "task_category", "model_name", "success"]:
    print(f"  {k}: {str(row.get(k,''))[:80]}")
traj = row.get("trajectory") or []
print(f"  trajectory length: {len(traj)}")
if traj:
    print(f"  first turn keys: {list(traj[0].keys())}")
    content = traj[0].get("content", "")
    print(f"  content type: {type(content).__name__}")
    if isinstance(content, list) and content:
        print(f"  content[0]: {content[0]}")
