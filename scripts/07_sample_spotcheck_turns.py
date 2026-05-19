"""Sample 20 diverse assistant turns for manual spot-check labeling."""
from __future__ import annotations
import json, pathlib, random, sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TRACES_DIR = REPO_ROOT / "data" / "validation-corpus" / "traces_normalized"
OUT_PATH = REPO_ROOT / "data" / "validation-corpus" / "annotations" / "spotcheck_sample.json"

random.seed(99)  # different seed from annotation to avoid bias

sessions = [json.loads(f.read_text()) for f in sorted(TRACES_DIR.glob("*.json"))]
random.shuffle(sessions)

# Target: 20 turns, at least 5 from each scaffold, mix of tool-bearing and text-only
samples: list[dict] = []
seen_sessions: set[str] = set()

# Collect candidates
candidates = []
for s in sessions:
    for t in s["turns"]:
        if t["role"] not in ("assistant", "ai"):
            continue
        if len(t["content_text"]) < 30:
            continue
        has_tools = len(t["tool_uses"]) > 0
        # Find surrounding context
        turns_by_idx = {tt["turn_index"]: tt for tt in s["turns"]}
        prev_turn = turns_by_idx.get(t["turn_index"] - 1)
        next_turn = turns_by_idx.get(t["turn_index"] + 1)
        candidates.append({
            "session_id": s["session_id"],
            "scaffold": s["scaffold"],
            "instance_id": s["instance_id"],
            "outcome": s["outcome"]["result"],
            "turn_index": t["turn_index"],
            "content_text": t["content_text"],
            "tool_uses": t["tool_uses"],
            "prev_content": prev_turn["content_text"][:300] if prev_turn else "",
            "next_content": (turns_by_idx.get(t["turn_index"] + 2, {}) or {}).get("content_text", "")[:200],
            "has_tools": has_tools,
        })

# Sample across scaffolds and tool/no-tool
random.shuffle(candidates)
scaffold_counts: dict[str, int] = {}
tool_count = 0
for c in candidates:
    if len(samples) >= 20:
        break
    sc = c["scaffold"]
    if scaffold_counts.get(sc, 0) >= 8:
        continue
    if not c["has_tools"] and tool_count >= 10:
        continue
    if c["has_tools"]:
        tool_count += 1
    scaffold_counts[sc] = scaffold_counts.get(sc, 0) + 1
    samples.append(c)

# Add groundtruth placeholder
for sample in samples:
    sample["spotcheck_labels"] = {
        "is_retry": None,
        "is_backtrack": None,
        "tool_result_used": None,
        "redundant_read": None,
        "labeler": "claude-sonnet-4-6",
        "notes": "",
    }

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
OUT_PATH.write_text(json.dumps(samples, indent=2), encoding="utf-8")
print(f"Sampled {len(samples)} turns for spot-check:")
for sc, cnt in scaffold_counts.items():
    print(f"  {sc}: {cnt} turns")
print(f"Tool-bearing: {tool_count}, text-only: {len(samples) - tool_count}")
print(f"Output: {OUT_PATH}")
