from __future__ import annotations

import json
import collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "data" / "swechat_noncc_adapted.jsonl"
lines = path.read_text(encoding="utf-8").splitlines()

tool_name_counter: collections.Counter[str] = collections.Counter()
bash_in_tool_names = 0
total_ai_turns = 0
sessions_with_bash = 0

for line in lines:
    if not line.strip():
        continue
    row = json.loads(line)
    turns = row["digest"]["turns"]
    session_has_bash = False
    for t in turns:
        if t["role"] == "ai":
            total_ai_turns += 1
            for tn in t.get("tool_names", []):
                tool_name_counter[tn] += 1
                if tn in ("Bash", "PowerShell"):
                    bash_in_tool_names += 1
                    session_has_bash = True
    if session_has_bash:
        sessions_with_bash += 1

print("Top 20 tool names in non-CC adapted turns:")
for name, cnt in tool_name_counter.most_common(20):
    print(f"  {name!r}: {cnt}")
print(f"Total AI turns: {total_ai_turns}")
print(f"Bash/PowerShell in tool_names: {bash_in_tool_names}")
print(f"Sessions with Bash/PowerShell: {sessions_with_bash}")
