from __future__ import annotations

"""Check role distribution and turn completeness in non-CC adapted sessions."""

import json
import collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "data" / "swechat_noncc_adapted.jsonl"
lines = path.read_text(encoding="utf-8").splitlines()

role_counter: collections.Counter[str] = collections.Counter()
turn_counts: list[int] = []
agent_turn_counts: dict[str, list[int]] = collections.defaultdict(list)

for line in lines:
    if not line.strip():
        continue
    row = json.loads(line)
    turns = row["digest"]["turns"]
    agent = row.get("agent_type", "unknown")
    for t in turns:
        role_counter[t["role"]] += 1
    tc = len(turns)
    turn_counts.append(tc)
    agent_turn_counts[agent].append(tc)

print("Role distribution across all non-CC adapted turns:")
for role, cnt in sorted(role_counter.items(), key=lambda x: -x[1]):
    print(f"  {role!r}: {cnt}")

print(f"\nTurn count stats across {len(turn_counts)} sessions:")
print(f"  Total: {sum(turn_counts)}")
print(f"  Min: {min(turn_counts)}, Max: {max(turn_counts)}")
print(f"  Mean: {sum(turn_counts)/len(turn_counts):.1f}")

print("\nPer-agent mean turn count:")
for agent, counts in sorted(agent_turn_counts.items()):
    print(f"  {agent}: {len(counts)} sessions, mean {sum(counts)/len(counts):.1f} turns")

# Check sessions with very few tool turns
few_tool_sessions = 0
for line in lines:
    if not line.strip():
        continue
    row = json.loads(line)
    turns = row["digest"]["turns"]
    tool_turns = sum(1 for t in turns if t["role"] == "tool")
    if tool_turns == 0:
        few_tool_sessions += 1

print(f"\nSessions with 0 tool turns: {few_tool_sessions}/{len(turn_counts)}")
