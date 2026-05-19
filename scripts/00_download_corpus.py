"""
00_download_corpus.py

Downloads and samples traces from three public HuggingFace datasets:
  1. nebius/SWE-agent-trajectories         (CC-BY-4.0)
  2. nebius/SWE-rebench-openhands-trajectories (CC-BY-4.0)
  3. SWE-Gym/OpenHands-Sampled-Trajectories    (unspecified)

Sampling strategy: stratified by resolved/unresolved. Raw traces are NOT
committed (large JSON). The corpus manifest (corpus_manifest.jsonl) IS
committed — it contains provenance, sampling details, and field summaries.

Usage:
    python scripts/00_download_corpus.py

Output:
    data/validation-corpus/traces_normalized/  — one JSON per trace
    data/validation-corpus/corpus_manifest.jsonl — provenance record
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "validation-corpus" / "traces_normalized"
OUT_DIR.mkdir(parents=True, exist_ok=True)
MANIFEST_PATH = REPO_ROOT / "data" / "validation-corpus" / "corpus_manifest.jsonl"

SEED = 42
random.seed(SEED)

# Per-source sampling targets
SOURCES: list[dict] = [
    {
        "hf_dataset": "nebius/SWE-agent-trajectories",
        "split": "train",
        "scaffold": "swe_agent",
        "n_resolved": 40,
        "n_unresolved": 40,
        "license": "CC-BY-4.0",
        "resolved_field": "target",       # bool
        "trajectory_field": "trajectory",  # JSON list of steps
        "model_field": "model_name",
        "instance_field": "instance_id",
        "patch_field": "generated_patch",
    },
    {
        "hf_dataset": "nebius/SWE-rebench-openhands-trajectories",
        "split": "train",
        "scaffold": "openhands_nebius",
        "n_resolved": 35,
        "n_unresolved": 35,
        "license": "CC-BY-4.0",
        "resolved_field": "resolved",      # int 0/1
        "trajectory_field": "trajectory",
        "model_field": None,               # not in schema
        "instance_field": "instance_id",
        "patch_field": "model_patch",
    },
    {
        "hf_dataset": "SWE-Gym/OpenHands-Sampled-Trajectories",
        "split": "train.raw",
        "scaffold": "openhands_swegym",
        "n_resolved": 25,
        "n_unresolved": 25,
        "license": "unspecified_annotated_labels_only",
        "resolved_field": "resolved",
        "trajectory_field": "messages",
        "model_field": None,
        "instance_field": "instance_id",
        "patch_field": None,
    },
]


def _session_id(source: str, instance_id: str, idx: int) -> str:
    raw = f"{source}:{instance_id}:{idx}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _estimate_tokens(text: str) -> int:
    """Rough token estimate using whitespace split (≈ 0.75× word count)."""
    return max(1, int(len(text.split()) * 1.3))


def _normalize_turn(raw: dict | str, turn_idx: int) -> dict:
    """Convert a raw step/message dict to our standard turn schema."""
    if isinstance(raw, str):
        raw = {"role": "unknown", "content": raw}

    role = raw.get("role", raw.get("type", "unknown"))
    # SWE-agent uses "text"; OpenHands uses "content"; fallback chain
    content = raw.get("content") or raw.get("text") or raw.get("observation") or raw.get("action") or ""
    if isinstance(content, list):
        # Claude-style content blocks
        content = " ".join(
            b.get("text", str(b)) if isinstance(b, dict) else str(b)
            for b in content
        )
    content_str = str(content) if content else ""

    tool_uses = []
    raw_tcs = raw.get("tool_calls") or []
    if isinstance(raw_tcs, list):
        for tc in raw_tcs:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    import json as _json
                    args = _json.loads(args)
                except Exception:
                    args = {"raw": args}
            tool_uses.append({
                "tool_name": fn.get("name") or tc.get("name", "unknown"),
                "tool_input": args,
                "tool_result": None,
                "is_error": False,
            })
    # Also handle single-tool-call fields (SWE-agent style)
    if not tool_uses and (raw.get("tool_use_id") or raw.get("tool_name")):
        tool_uses.append({
            "tool_name": raw.get("tool_name", raw.get("action", "unknown")),
            "tool_input": raw.get("tool_input", raw.get("args", {})),
            "tool_result": raw.get("tool_result", raw.get("observation", None)),
            "is_error": raw.get("is_error", False),
        })

    approx_tokens = _estimate_tokens(content_str)

    return {
        "turn_index": turn_idx,
        "role": role,
        "content_text": content_str[:4000],  # cap for storage
        "tool_uses": tool_uses,
        "token_counts": {
            "input": approx_tokens if role in ("user", "tool") else 0,
            "output": approx_tokens if role == "assistant" else 0,
            "cache_read": 0,   # not available in these datasets
            "cache_creation": 0,
        },
    }


def process_source(cfg: dict) -> list[dict]:
    try:
        from datasets import load_dataset  # type: ignore[import]
    except ImportError:
        print("ERROR: datasets library not installed", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"Loading {cfg['hf_dataset']} (streaming)…")

    ds = load_dataset(cfg["hf_dataset"], split=cfg["split"], streaming=True)

    resolved_pool: list[dict] = []
    unresolved_pool: list[dict] = []
    needed_each = max(cfg["n_resolved"], cfg["n_unresolved"]) * 6  # buffer

    for row in ds:
        rv = row.get(cfg["resolved_field"], 0)
        is_resolved = bool(rv) if not isinstance(rv, bool) else rv
        if is_resolved and len(resolved_pool) < needed_each:
            resolved_pool.append(row)
        elif not is_resolved and len(unresolved_pool) < needed_each:
            unresolved_pool.append(row)
        if len(resolved_pool) >= needed_each and len(unresolved_pool) >= needed_each:
            break

    print(f"  Pooled {len(resolved_pool)} resolved, {len(unresolved_pool)} unresolved.")

    random.shuffle(resolved_pool)
    random.shuffle(unresolved_pool)
    sampled = (
        resolved_pool[: cfg["n_resolved"]]
        + unresolved_pool[: cfg["n_unresolved"]]
    )

    sessions = []
    for idx, row in enumerate(sampled):
        raw_traj = row.get(cfg["trajectory_field"], [])
        if isinstance(raw_traj, str):
            try:
                raw_traj = json.loads(raw_traj)
            except Exception:
                raw_traj = []

        turns = [_normalize_turn(t, i) for i, t in enumerate(raw_traj)]

        # Deduce outcome
        rv = row.get(cfg["resolved_field"], 0)
        resolved = bool(rv) if not isinstance(rv, bool) else rv

        instance_id = str(row.get(cfg["instance_field"], f"unknown_{idx}"))
        model = row.get(cfg["model_field"], "unknown") if cfg["model_field"] else "unknown"
        patch = row.get(cfg["patch_field"], None) if cfg["patch_field"] else None

        session = {
            "session_id": _session_id(cfg["hf_dataset"], instance_id, idx),
            "source_dataset": cfg["hf_dataset"],
            "scaffold": cfg["scaffold"],
            "model": model,
            "license": cfg["license"],
            "instance_id": instance_id,
            "outcome": {
                "result": "pass" if resolved else "fail",
                "patch_diff": str(patch)[:2000] if patch else None,
            },
            "turns": turns,
            "session_token_totals": {
                "input": sum(t["token_counts"]["input"] for t in turns),
                "output": sum(t["token_counts"]["output"] for t in turns),
                "cache_read": 0,
                "total": sum(
                    t["token_counts"]["input"] + t["token_counts"]["output"]
                    for t in turns
                ),
            },
            "turn_count": len(turns),
        }
        sessions.append(session)

    print(f"  Normalized {len(sessions)} sessions.")
    return sessions


def main() -> None:
    all_sessions: list[dict] = []
    for cfg in SOURCES:
        sessions = process_source(cfg)
        all_sessions.extend(sessions)

    # Write individual trace files
    for s in all_sessions:
        fname = OUT_DIR / f"{s['session_id']}.json"
        fname.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")

    # Write manifest
    manifest_lines = []
    for s in all_sessions:
        manifest_lines.append(json.dumps({
            "session_id": s["session_id"],
            "source_dataset": s["source_dataset"],
            "scaffold": s["scaffold"],
            "model": s["model"],
            "license": s["license"],
            "instance_id": s["instance_id"],
            "outcome": s["outcome"]["result"],
            "turn_count": s["turn_count"],
            "total_tokens": s["session_token_totals"]["total"],
        }))
    MANIFEST_PATH.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

    # Summary
    by_scaffold: dict[str, dict] = {}
    for s in all_sessions:
        sc = s["scaffold"]
        by_scaffold.setdefault(sc, {"total": 0, "resolved": 0, "unresolved": 0})
        by_scaffold[sc]["total"] += 1
        if s["outcome"]["result"] == "pass":
            by_scaffold[sc]["resolved"] += 1
        else:
            by_scaffold[sc]["unresolved"] += 1

    print("\n" + "="*60)
    print(f"CORPUS SUMMARY: {len(all_sessions)} total sessions")
    for sc, counts in by_scaffold.items():
        print(f"  {sc}: {counts['total']} ({counts['resolved']} resolved, {counts['unresolved']} unresolved)")
    print(f"\nWrote {len(all_sessions)} JSON files to {OUT_DIR}")
    print(f"Manifest: {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
