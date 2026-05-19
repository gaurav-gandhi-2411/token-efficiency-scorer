"""Check instance_id, first-turn content, and patch_diff stats."""
from __future__ import annotations
import json, pathlib, re, collections

traces_dir = pathlib.Path("data/validation-corpus/traces_normalized")
files = sorted(traces_dir.glob("*.json"))

print("=== Instance IDs (first 20) ===")
for f in files[:20]:
    s = json.loads(f.read_text())
    print(f"  scaffold={s['scaffold']:<22} instance_id={s['instance_id']}")

print("\n=== First-turn content sample ===")
for f in files[:5]:
    s = json.loads(f.read_text())
    first_user = next((t for t in s["turns"] if t["role"] in ("user", "system")), None)
    if first_user:
        print(f"\n  [{s['scaffold']}] role={first_user['role']} content[:200]:")
        print("   ", first_user["content_text"][:200].replace("\n", " "))

print("\n=== Patch diff stats (first 10 resolved) ===")
count = 0
for f in files:
    s = json.loads(f.read_text())
    diff = s["outcome"].get("patch_diff", "") or ""
    if s["outcome"]["result"] in ("pass", "resolved", True, "true") and diff:
        lines_added = diff.count("\n+") - diff.count("\n+++")
        lines_removed = diff.count("\n-") - diff.count("\n---")
        files_changed = len(re.findall(r"^diff --git", diff, re.MULTILINE))
        print(f"  [{s['scaffold']}] files={files_changed} +{lines_added} -{lines_removed} instance={s['instance_id'][:30]}")
        count += 1
        if count >= 10:
            break

print("\n=== session_token_totals sample ===")
for f in files[:5]:
    s = json.loads(f.read_text())
    print(f"  {s['scaffold']}: {s.get('session_token_totals')}")
