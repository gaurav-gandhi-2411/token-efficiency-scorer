"""Inspect CC-Bench Claude Code session turn structure."""
from __future__ import annotations
import json
from datasets import load_dataset, VerificationMode

ds = load_dataset("zai-org/CC-Bench-trajectories", split="train",
                  verification_mode=VerificationMode.NO_CHECKS)

# Find a row with tool calls
for row in ds:
    tcs = row.get("tool_calls", 0)
    if tcs and tcs > 2:
        traj = json.loads(row["trajectory"])
        print(f"task_id={row['task_id']} cat={row['task_category']} model={row['model_name']}")
        print(f"  total_tokens={row['total_tokens']} tool_calls={tcs}")
        print(f"  n_turns={len(traj)}")
        for i, t in enumerate(traj[:6]):
            role = t.get("type", "?")
            msg = t.get("message", {})
            msg_role = msg.get("role", "?") if isinstance(msg, dict) else "?"
            content = msg.get("content", []) if isinstance(msg, dict) else []
            if isinstance(content, str):
                print(f"    t{i} [{role}/{msg_role}] text: {content[:100]}")
            elif isinstance(content, list):
                for blk in content[:3]:
                    if isinstance(blk, dict):
                        btype = blk.get("type", "?")
                        if btype == "text":
                            print(f"    t{i} [{role}/{msg_role}] text: {blk.get('text','')[:100]}")
                        elif btype == "tool_use":
                            print(f"    t{i} [{role}/{msg_role}] tool_use: {blk.get('name')} input_keys={list(blk.get('input',{}).keys())}")
                        elif btype == "tool_result":
                            rc = blk.get("content", "")
                            if isinstance(rc, list):
                                rc = " ".join(p.get("text","") for p in rc if isinstance(p,dict))
                            print(f"    t{i} [{role}/{msg_role}] tool_result: {str(rc)[:100]}")
        print()
        break
