"""Inspect CC-Bench and write OOD traces to disk."""
from __future__ import annotations
import json, pathlib, hashlib, sys
sys.stdout.reconfigure(encoding="utf-8")

from datasets import load_dataset, VerificationMode

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "ood-corpus" / "traces_normalized"
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG = REPO_ROOT / "data" / "ood-corpus" / "ccbench_download.log"

def _sid(key: str) -> str:
    return hashlib.sha256(f"ccbench:{key}".encode()).hexdigest()[:16]

def parse_content(content) -> tuple[str, list]:
    """Parse Claude Code content block into (text, tool_uses)."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content), []
    texts, tools = [], []
    for blk in content:
        if not isinstance(blk, dict):
            continue
        btype = blk.get("type", "")
        if btype == "text":
            texts.append(blk.get("text", ""))
        elif btype == "tool_use":
            tools.append({
                "tool_name": blk.get("name", ""),
                "tool_input": blk.get("input", {}),
                "tool_result": None,
            })
        elif btype == "tool_result":
            rc = blk.get("content", "")
            if isinstance(rc, list):
                rc = " ".join(p.get("text", "") for p in rc if isinstance(p, dict))
            texts.append(f"[RESULT: {str(rc)[:300]}]")
    return " ".join(texts), tools

ds = load_dataset("zai-org/CC-Bench-trajectories", split="train",
                  verification_mode=VerificationMode.NO_CHECKS)

log_lines = [f"CC-Bench: {len(ds)} rows"]
categories = list(set(r.get("task_category","?") for r in ds))
log_lines.append(f"Categories: {categories}")

records = []
seen_cats: dict[str, int] = {}

for row in ds:
    cat = row.get("task_category") or "unknown"
    if seen_cats.get(cat, 0) >= 2:
        continue
    traj_raw = row.get("trajectory") or "[]"
    try:
        traj = json.loads(traj_raw)
    except Exception:
        continue
    if not isinstance(traj, list) or len(traj) < 4:
        continue

    # Only process rows with actual tool calls
    tcs = row.get("tool_calls", 0) or 0
    if tcs == 0:
        continue

    # Parse turns
    turns = []
    for i, raw in enumerate(traj):
        raw_role = raw.get("type", "user")
        role = {"user": "user", "assistant": "assistant", "system": "system"}.get(raw_role, raw_role)
        msg = raw.get("message", {})
        if not isinstance(msg, dict):
            content_raw = str(msg)
            text, tool_uses = content_raw, []
        else:
            content_raw = msg.get("content", "")
            text, tool_uses = parse_content(content_raw)
        turns.append({
            "turn_index": i,
            "role": role,
            "content_text": text[:2000],  # cap per turn
            "tool_uses": tool_uses,
        })

    task_id = str(row.get("task_id") or len(records))
    result = "pass" if row.get("tool_failures", 0) == 0 and tcs > 0 else "unknown"

    rec = {
        "session_id": _sid(task_id),
        "source_dataset": "zai-org/CC-Bench-trajectories",
        "scaffold": "claude_code",
        "model": row.get("model_name") or "unknown",
        "license": "CC-BY-4.0",
        "instance_id": f"ccbench__{task_id}",
        "ood_source": "zai-org/CC-Bench-trajectories",
        "ood_task_category": cat,
        "outcome": {"result": result, "patch_diff": None},
        "turns": turns,
        "session_token_totals": {
            "input": row.get("total_input_tokens", 0) or 0,
            "output": row.get("total_output_tokens", 0) or 0,
            "cache_read": 0,
            "total": row.get("total_tokens", 0) or 0,
        },
        "turn_count": len(turns),
    }
    records.append(rec)
    seen_cats[cat] = seen_cats.get(cat, 0) + 1
    log_lines.append(f"  cat={cat} task_id={task_id} turns={len(turns)} tools={tcs}")
    if sum(seen_cats.values()) >= 10:
        break

for rec in records:
    out = OUT_DIR / f"{rec['session_id']}.json"
    out.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")

log_lines.append(f"\nTotal CC-Bench records written: {len(records)}")
LOG.write_text("\n".join(log_lines), encoding="utf-8")
print(f"Written {len(records)} CC-Bench OOD records. See {LOG}")
