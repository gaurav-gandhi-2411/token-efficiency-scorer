"""
05_download_ood_traces.py

Downloads 10-20 out-of-distribution traces from three datasets that are
NOT SWE-bench-shaped, normalizes them to the same JSON schema used by
the main corpus, and writes them to data/ood-corpus/traces_normalized/.

OOD sources:
  S1  zai-org/CC-Bench-trajectories  — Claude Code on diverse tasks
      (data_analysis, frontend_development, machine_learning, build_deployment)
  S2  AlienKevin/SWE-ZERO-12M-trajectories  — multi-language SWE (non-Python)
  S3  Agent-Ark/Toucan-1.5M  — MCP tool usage (file mgmt, shell, API calls)

Output schema is identical to traces_normalized/ with one extra field:
  "ood_source": "<dataset_name>"
  "ood_task_category": "<category string from source>"

Usage:
    python scripts/05_download_ood_traces.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "ood-corpus" / "traces_normalized"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SEED = 42

# ── Shared normalizer ─────────────────────────────────────────────────────────

def _make_session_id(source: str, unique_key: str) -> str:
    return hashlib.sha256(f"{source}:{unique_key}".encode()).hexdigest()[:16]


def _count_tokens_approx(text: str) -> int:
    return len(text.split())


# ── S1: CC-Bench-trajectories ─────────────────────────────────────────────────

def _normalize_ccbench_turn(raw: dict, turn_index: int) -> dict:
    role_map = {"human": "user", "assistant": "assistant", "system": "system",
                "tool": "tool", "user": "user"}
    role = role_map.get(raw.get("role", ""), "user")
    content = raw.get("content", "") or ""
    if isinstance(content, list):
        text_parts = []
        tool_uses = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tool_uses.append({
                        "tool_name": block.get("name", ""),
                        "tool_input": block.get("input", {}),
                        "tool_result": None,
                    })
                elif block.get("type") == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        result_content = " ".join(
                            p.get("text", "") for p in result_content if isinstance(p, dict)
                        )
                    text_parts.append(str(result_content))
            else:
                text_parts.append(str(block))
        return {
            "turn_index": turn_index,
            "role": role,
            "content_text": " ".join(text_parts),
            "tool_uses": tool_uses,
        }
    return {
        "turn_index": turn_index,
        "role": role,
        "content_text": str(content),
        "tool_uses": [],
    }


def _normalize_ccbench_session(row: dict, idx: int) -> dict | None:
    """Normalize a CC-Bench row to standard schema."""
    traj = row.get("trajectory") or row.get("messages") or []
    if not traj:
        return None
    task_id = row.get("task_id") or row.get("task") or str(idx)
    category = row.get("task_category") or row.get("category") or "unknown"
    model = row.get("model_name") or row.get("model") or "unknown"
    result = str(row.get("success") or row.get("result") or "unknown").lower()
    if result in ("true", "1", "yes"):
        result = "pass"
    elif result in ("false", "0", "no"):
        result = "fail"

    turns = []
    for i, raw_turn in enumerate(traj):
        t = _normalize_ccbench_turn(raw_turn, i)
        turns.append(t)

    total_input = sum(_count_tokens_approx(t["content_text"]) for t in turns if t["role"] in ("user", "system"))
    total_output = sum(_count_tokens_approx(t["content_text"]) for t in turns if t["role"] == "assistant")

    return {
        "session_id": _make_session_id("ccbench", str(task_id)),
        "source_dataset": "zai-org/CC-Bench-trajectories",
        "scaffold": "claude_code",
        "model": model,
        "license": "CC-BY-4.0",
        "instance_id": f"ccbench__{task_id}",
        "ood_source": "zai-org/CC-Bench-trajectories",
        "ood_task_category": category,
        "outcome": {"result": result, "patch_diff": None},
        "turns": turns,
        "session_token_totals": {
            "input": total_input, "output": total_output,
            "cache_read": 0, "total": total_input + total_output,
        },
        "turn_count": len(turns),
    }


# ── S2: SWE-ZERO (non-Python filter) ─────────────────────────────────────────

def _normalize_swezero_session(row: dict, idx: int) -> dict | None:
    """Normalize a SWE-ZERO row to standard schema."""
    lang = (row.get("language") or "").lower()
    if lang == "python" or not lang:
        return None  # skip Python — use only OOD languages

    messages = row.get("messages") or row.get("trajectory") or []
    if not messages:
        return None

    task_id = row.get("instance_id") or row.get("id") or str(idx)
    result = row.get("resolved") or row.get("result") or "unknown"
    if isinstance(result, bool):
        result = "pass" if result else "fail"

    turns = []
    for i, raw in enumerate(messages):
        role_raw = raw.get("role", "user")
        role = {"human": "user", "gpt": "assistant", "assistant": "assistant",
                "user": "user", "system": "system"}.get(role_raw, role_raw)
        content = raw.get("content") or raw.get("value") or ""
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        # Parse tool uses from OpenAI-style function calls if present
        tool_calls = raw.get("tool_calls") or []
        tool_uses = []
        for tc in tool_calls:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            tool_uses.append({
                "tool_name": fn.get("name", ""),
                "tool_input": args,
                "tool_result": None,
            })
        turns.append({
            "turn_index": i,
            "role": role,
            "content_text": str(content),
            "tool_uses": tool_uses,
        })

    return {
        "session_id": _make_session_id("swezero", str(task_id)),
        "source_dataset": "AlienKevin/SWE-ZERO-12M-trajectories",
        "scaffold": "mini_swe_agent",
        "model": row.get("model") or "unknown",
        "license": "MIT",
        "instance_id": str(task_id),
        "ood_source": "AlienKevin/SWE-ZERO-12M-trajectories",
        "ood_task_category": f"swe_{lang}",
        "outcome": {"result": str(result), "patch_diff": row.get("patch") or None},
        "turns": turns,
        "session_token_totals": {"input": 0, "output": 0, "cache_read": 0, "total": 0},
        "turn_count": len(turns),
    }


# ── S3: Toucan-1.5M (MCP tool usage) ─────────────────────────────────────────

def _normalize_toucan_session(row: dict, idx: int) -> dict | None:
    """Normalize a Toucan (ShareGPT-style) row to standard schema."""
    convos = row.get("conversations") or row.get("messages") or []
    if not convos:
        return None
    # Filter for tool-bearing sessions (not pure Q&A)
    has_tool = any(
        m.get("role") in ("tool", "function") or m.get("from") in ("tool", "function")
        for m in convos
    )
    if not has_tool:
        return None

    task_id = row.get("id") or row.get("task_id") or str(idx)
    category = row.get("category") or row.get("task_type") or row.get("system", "")[:40]

    turns = []
    for i, raw in enumerate(convos):
        role_raw = raw.get("from") or raw.get("role") or "user"
        role = {"human": "user", "gpt": "assistant", "assistant": "assistant",
                "tool": "tool", "function": "tool", "user": "user",
                "system": "system"}.get(role_raw, role_raw)
        content = raw.get("value") or raw.get("content") or ""
        tool_uses: list[dict] = []
        if role == "tool":
            # Previous assistant turn should carry the tool call; note result here
            pass
        elif role == "assistant":
            # Parse function call if embedded as JSON
            if isinstance(content, str) and content.strip().startswith("{"):
                try:
                    maybe_call = json.loads(content)
                    if "name" in maybe_call or "function" in maybe_call:
                        fn_name = maybe_call.get("name") or maybe_call.get("function", "")
                        tool_uses.append({
                            "tool_name": fn_name,
                            "tool_input": maybe_call.get("arguments") or maybe_call.get("input") or {},
                            "tool_result": None,
                        })
                        content = f"[tool call: {fn_name}]"
                except json.JSONDecodeError:
                    pass
        turns.append({
            "turn_index": i,
            "role": role,
            "content_text": str(content),
            "tool_uses": tool_uses,
        })

    return {
        "session_id": _make_session_id("toucan", str(task_id)),
        "source_dataset": "Agent-Ark/Toucan-1.5M",
        "scaffold": "mcp_agent",
        "model": "unknown",
        "license": "Apache-2.0",
        "instance_id": f"toucan__{task_id}",
        "ood_source": "Agent-Ark/Toucan-1.5M",
        "ood_task_category": str(category),
        "outcome": {"result": "unknown", "patch_diff": None},
        "turns": turns,
        "session_token_totals": {"input": 0, "output": 0, "cache_read": 0, "total": 0},
        "turn_count": len(turns),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        from datasets import load_dataset  # type: ignore[import]
    except ImportError:
        print("ERROR: datasets library not installed. Run: pip install datasets", file=sys.stderr)
        sys.exit(1)

    records: list[dict] = []
    manifest: list[dict] = []

    # ── S1: CC-Bench (target: 8 sessions, diverse categories) ────────────────
    print("Downloading S1: CC-Bench-trajectories…")
    try:
        ds = load_dataset("zai-org/CC-Bench-trajectories", split="train", streaming=True)
        seen_categories: dict[str, int] = {}
        for i, row in enumerate(ds):
            if sum(seen_categories.values()) >= 8:
                break
            cat = row.get("task_category") or "unknown"
            if seen_categories.get(cat, 0) >= 2:
                continue
            norm = _normalize_ccbench_session(row, i)
            if norm and len(norm["turns"]) >= 4:
                records.append(norm)
                seen_categories[cat] = seen_categories.get(cat, 0) + 1
                print(f"  S1 [{len(records)}] {cat}: {norm['instance_id'][:40]}")
    except Exception as e:
        print(f"  S1 ERROR: {e}")

    # ── S2: SWE-ZERO non-Python (target: 5 sessions) ─────────────────────────
    print("Downloading S2: SWE-ZERO-12M non-Python…")
    try:
        ds2 = load_dataset(
            "AlienKevin/SWE-ZERO-12M-trajectories",
            split="train",
            streaming=True,
        )
        s2_count = 0
        for i, row in enumerate(ds2):
            if s2_count >= 5:
                break
            lang = (row.get("language") or "").lower()
            if lang in ("python", "", "unknown") or not lang:
                continue
            norm = _normalize_swezero_session(row, i)
            if norm and len(norm["turns"]) >= 4:
                records.append(norm)
                s2_count += 1
                print(f"  S2 [{s2_count}] lang={lang}: {norm['instance_id'][:40]}")
            if i > 2000:  # don't scan too deep in streaming
                print("  S2: reached scan limit without finding 5 non-Python sessions")
                break
    except Exception as e:
        print(f"  S2 ERROR: {e}")

    # ── S3: Toucan MCP (target: 5 sessions) ──────────────────────────────────
    print("Downloading S3: Toucan-1.5M MCP tool usage…")
    try:
        ds3 = load_dataset("Agent-Ark/Toucan-1.5M", split="train", streaming=True)
        s3_count = 0
        for i, row in enumerate(ds3):
            if s3_count >= 5:
                break
            norm = _normalize_toucan_session(row, i)
            if norm and len(norm["turns"]) >= 4:
                records.append(norm)
                s3_count += 1
                print(f"  S3 [{s3_count}] cat={norm['ood_task_category'][:40]}: {norm['session_id']}")
            if i > 500:
                print("  S3: reached scan limit")
                break
    except Exception as e:
        print(f"  S3 ERROR: {e}")

    # ── Write output ──────────────────────────────────────────────────────────
    print(f"\nWriting {len(records)} OOD sessions…")
    for r in records:
        out = OUT_DIR / f"{r['session_id']}.json"
        out.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest.append({
            "session_id": r["session_id"],
            "source_dataset": r["source_dataset"],
            "scaffold": r["scaffold"],
            "model": r["model"],
            "ood_task_category": r["ood_task_category"],
            "turn_count": r["turn_count"],
            "outcome": r["outcome"]["result"],
        })

    manifest_path = REPO_ROOT / "data" / "ood-corpus" / "ood_manifest.jsonl"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "\n".join(json.dumps(m) for m in manifest), encoding="utf-8"
    )

    print(f"\nOOD corpus: {len(records)} sessions")
    by_source = {}
    for m in manifest:
        s = m["source_dataset"]
        by_source[s] = by_source.get(s, 0) + 1
    for s, cnt in by_source.items():
        print(f"  {s}: {cnt}")
    print(f"\nOutput: {OUT_DIR}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
