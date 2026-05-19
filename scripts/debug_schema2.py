"""Inspect trace schema for taxonomy/difficulty fields."""
from __future__ import annotations
import json, pathlib

traces_dir = pathlib.Path("data/validation-corpus/traces_normalized")
files = sorted(traces_dir.glob("*.json"))

s = json.loads(files[0].read_text())
print("Top-level fields:", list(s.keys()))
print()
print("Sample session metadata:")
for k in ["session_id", "scaffold", "source_task_id", "outcome", "turn_count", "total_tokens"]:
    print(f"  {k}: {s.get(k)}")
print()
print("Outcome fields:", list(s.get("outcome", {}).keys()))
print("Sample outcome:", s.get("outcome"))
print()
if "metadata" in s:
    print("Metadata fields:", list(s["metadata"].keys()))
    print("Sample metadata:", str(s["metadata"])[:300])
print()

# Check all sessions for problem_statement / task description fields
print("--- Checking task description fields across 10 sessions ---")
for f in files[:10]:
    s2 = json.loads(f.read_text())
    meta = s2.get("metadata", {})
    outcome = s2.get("outcome", {})
    print(f"  [{s2['scaffold']}] meta keys: {list(meta.keys())[:8]}, outcome keys: {list(outcome.keys())}")
