"""Temporary schema inspection script — not part of final pipeline."""
from __future__ import annotations
import json
import sys
from datasets import load_dataset  # type: ignore[import]

for dataset_name in [
    "nebius/SWE-agent-trajectories",
    "nebius/SWE-rebench-openhands-trajectories",
    "SWE-Gym/OpenHands-Sampled-Trajectories",
]:
    split = "train.raw" if "SWE-Gym" in dataset_name else "train"
    print(f"\n{'='*60}\nDataset: {dataset_name}")
    ds = load_dataset(dataset_name, split=split, streaming=True)
    for row in ds:
        print("Top-level keys:", list(row.keys()))
        # Find the trajectory field
        traj_field = next((k for k in ["trajectory", "messages"] if k in row), None)
        if traj_field:
            traj = row[traj_field]
            print(f"  {traj_field} type:", type(traj).__name__, "len:", len(traj) if traj else 0)
            if traj:
                t = traj[0] if isinstance(traj, list) else traj
                if isinstance(t, dict):
                    print("  First item keys:", list(t.keys()))
                    print("  First item sample:", json.dumps(t, default=str)[:500])
                elif isinstance(t, str):
                    print("  First item (str):", t[:300])
                else:
                    print("  First item type:", type(t).__name__)
        break
