from __future__ import annotations

"""generalization_run.py — B5 generalization run: frozen waste detectors on SWE-chat public data.

Phases:
  0 — Metadata: count CC sessions, distinct users, extract transcript UUIDs
  1 — Download CC transcripts from HuggingFace SALT-NLP/SWE-chat
  2 — Adapt CC transcripts → swechat_cc_adapted.jsonl via claudecode_adapter
  3 — Download conversations.parquet + adapt non-CC → swechat_noncc_adapted.jsonl
  4 — Run detectors on CC adapted sessions
  5 — Run detectors on non-CC adapted sessions
  6 — Write output files (public_waste_signals.jsonl, generalization_compare.json)
  7 — Print comparison report

CLI flags:
  --skip-download   assume transcripts already in data/swechat_raw/transcripts/
  --skip-cc-adapt   assume swechat_cc_adapted.jsonl already exists
  --skip-noncc      skip the non-CC phase entirely
  --max-cc N        limit to first N CC sessions (for testing)
"""

import argparse
import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# ---------------------------------------------------------------------------
# Constants — baseline numbers from 181-session CC pool (B4 run)
# ---------------------------------------------------------------------------

_POOL_SESSIONS: int = 181
_POOL_RFR_SESSIONS: int = 12
_POOL_RR_ANY_SESSIONS: int = 20
_POOL_RR_PATH_A_SESSIONS: int = 4
_POOL_RR_PATH_B_SESSIONS: int = 18

_HF_REPO_ID: str = "SALT-NLP/SWE-chat"
_UUID_RE: re.Pattern[str] = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl"
)

DATA_DIR: Path = ROOT / "data" / "swechat_raw"
TRANSCRIPTS_DIR: Path = DATA_DIR / "transcripts"
CC_ADAPTED_PATH: Path = ROOT / "data" / "swechat_cc_adapted.jsonl"
NONCC_ADAPTED_PATH: Path = ROOT / "data" / "swechat_noncc_adapted.jsonl"
SIGNALS_PATH: Path = ROOT / "data" / "public_waste_signals.jsonl"
COMPARE_PATH: Path = ROOT / "data" / "generalization_compare.json"

# Non-CC agents included in the generalization run (exclude "unknown" — no agent identity)
_INCLUDE_AGENTS: frozenset[str] = frozenset(
    {"opencode", "codex", "gemini cli", "cursor", "copilot cli", "agent", "roger roger agent",
     "vogon agent"}
)


# ---------------------------------------------------------------------------
# Phase 0: Metadata
# ---------------------------------------------------------------------------


def phase_0_metadata() -> tuple[list[str], int, int]:
    """Load sessions.parquet and extract CC transcript UUIDs.

    Returns:
        (uuid_list, cc_total, distinct_user_count)
    """
    import pandas as pd

    sessions_path = DATA_DIR / "sessions.parquet"
    if not sessions_path.exists():
        print(f"ERROR: {sessions_path} not found. Download sessions.parquet first.", file=sys.stderr)
        sys.exit(1)

    sessions_df = pd.read_parquet(sessions_path)
    cc_mask = sessions_df["agent"].str.lower().str.contains("claude", na=False)
    cc_df = sessions_df[cc_mask]

    cc_total: int = len(cc_df)
    distinct_users: int = int(cc_df["user_id"].nunique())

    # Extract UUIDs from transcript_path column
    uuids: list[str] = []
    for tp in cc_df["transcript_path"].dropna():
        m = _UUID_RE.search(str(tp))
        if m:
            uuids.append(m.group(1))

    print(
        f"CC sessions: {cc_total} total, {len(uuids)} with transcript UUIDs, "
        f"{distinct_users} distinct users"
    )
    return uuids, cc_total, distinct_users


# ---------------------------------------------------------------------------
# Phase 1: Download CC transcripts
# ---------------------------------------------------------------------------


def phase_1_download(uuids: list[str], max_cc: int | None) -> list[str]:
    """Download CC transcript JSONLs from HuggingFace.

    Uses hf_hub_download in a loop with tqdm progress bar.
    Already-cached files are skipped automatically (force_download=False).

    Returns:
        List of UUID strings whose files are now present locally.
    """
    from huggingface_hub import hf_hub_download

    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    target_uuids = uuids if max_cc is None else uuids[:max_cc]
    print(f"Downloading {len(target_uuids)} CC transcripts...")

    try:
        from tqdm import tqdm
        iterator = tqdm(target_uuids, desc="Transcripts", unit="file")
    except ImportError:
        iterator = target_uuids  # type: ignore[assignment]

    present: list[str] = []
    for uuid in iterator:
        local_path = TRANSCRIPTS_DIR / f"{uuid}.jsonl"
        if local_path.exists():
            present.append(uuid)
            continue
        try:
            hf_hub_download(
                repo_id=_HF_REPO_ID,
                filename=f"transcripts/{uuid}.jsonl",
                repo_type="dataset",
                local_dir=str(DATA_DIR),
                force_download=False,
            )
            present.append(uuid)
        except Exception as exc:
            print(f"[download] WARN: could not download {uuid}.jsonl: {exc}", file=sys.stderr)

    print(f"Transcripts present: {len(present)} / {len(target_uuids)}")
    return present


# ---------------------------------------------------------------------------
# Phase 2: Adapt CC transcripts → swechat_cc_adapted.jsonl
# ---------------------------------------------------------------------------


def phase_2_adapt_cc(present_uuids: list[str]) -> int:
    """Adapt each CC transcript using claudecode_adapter.adapt_session().

    Adds "source": "swechat_cc" and "agent_type": "claude_code" fields.
    Writes one JSON record per line to CC_ADAPTED_PATH.

    Returns:
        Total turn count across all adapted sessions.
    """
    from adapters.claudecode_adapter import adapt_session

    attempted: int = 0
    adapted: int = 0
    errors: int = 0
    total_turns: int = 0

    CC_ADAPTED_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        from tqdm import tqdm
        iterator = tqdm(present_uuids, desc="Adapting CC", unit="session")
    except ImportError:
        iterator = present_uuids  # type: ignore[assignment]

    with CC_ADAPTED_PATH.open("w", encoding="utf-8") as fh:
        for uuid in iterator:
            transcript_path = TRANSCRIPTS_DIR / f"{uuid}.jsonl"
            if not transcript_path.exists():
                continue
            attempted += 1
            try:
                record = adapt_session(transcript_path)
                record["source"] = "swechat_cc"
                record["agent_type"] = "claude_code"
                fh.write(json.dumps(record) + "\n")
                adapted += 1
                total_turns += record.get("turn_count", 0)
            except Exception as exc:
                errors += 1
                print(
                    f"[adapt_cc] ERROR: {uuid}: {exc}",
                    file=sys.stderr,
                )

    print(
        f"\nCC adaptation complete:\n"
        f"  Sessions adapted: {adapted} / {attempted} attempted\n"
        f"  Total turns: {total_turns}\n"
        f"  Errors: {errors} (see stderr)"
    )
    return total_turns


# ---------------------------------------------------------------------------
# Phase 3: Download conversations.parquet + adapt non-CC
# ---------------------------------------------------------------------------


def phase_3_noncc(sessions_parquet_path: Path) -> dict[str, int]:
    """Download conversations.parquet and adapt non-CC sessions.

    Returns:
        Agent-type breakdown dict {agent_name: session_count}.
    """
    import pandas as pd
    import pyarrow.parquet as pq

    from public_trace_adapter import _NON_CC_TOOL_MAP, adapt_swechat_session

    # Download conversations.parquet if not present
    convs_path = DATA_DIR / "conversations.parquet"
    if not convs_path.exists():
        from huggingface_hub import hf_hub_download

        print("Downloading conversations.parquet from HuggingFace...")
        hf_hub_download(
            repo_id=_HF_REPO_ID,
            filename="conversations.parquet",
            repo_type="dataset",
            local_dir=str(DATA_DIR),
            force_download=False,
        )
        print(f"conversations.parquet downloaded to {convs_path}")
    else:
        print(f"conversations.parquet already present ({convs_path.stat().st_size / 1e6:.1f} MB)")

    sessions_df = pd.read_parquet(sessions_parquet_path)

    # Non-CC sessions: agent NOT containing "claude" and NOT "unknown"
    cc_mask = sessions_df["agent"].str.lower().str.contains("claude", na=False)
    unknown_mask = sessions_df["agent"].str.lower() == "unknown"
    noncc_df = sessions_df[~cc_mask & ~unknown_mask].copy()

    # Agent breakdown
    agent_breakdown: dict[str, int] = noncc_df["agent"].value_counts().to_dict()
    print(f"Non-CC agents: {agent_breakdown}")

    print(f"Loading conversations.parquet ({convs_path.stat().st_size / 1e6:.1f} MB)...")
    table = pq.read_table(str(convs_path))
    conv_df = table.to_pandas()

    # Discover the session link column and ordering column
    session_col: str | None = next(
        (c for c in ["session_id", "session", "conversation_id", "conv_id"] if c in conv_df.columns),
        None,
    )
    order_col: str | None = next(
        (c for c in ["turn_id", "turn_index", "index", "seq", "sequence", "order", "id"]
         if c in conv_df.columns),
        None,
    )

    if session_col is None:
        print("[phase_3] WARNING: cannot identify session column. Skipping non-CC adapt.",
              file=sys.stderr)
        return agent_breakdown

    noncc_session_ids: set[str] = set(noncc_df["session_id"].astype(str).tolist())

    NONCC_ADAPTED_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        from tqdm import tqdm
        session_iter = tqdm(sorted(noncc_session_ids), desc="Adapting non-CC", unit="session")
    except ImportError:
        session_iter = sorted(noncc_session_ids)  # type: ignore[assignment]

    written: int = 0
    per_agent_counts: dict[str, int] = {}

    with NONCC_ADAPTED_PATH.open("w", encoding="utf-8") as fh:
        for sid in session_iter:
            session_rows = conv_df[conv_df[session_col].astype(str) == sid].copy()
            if session_rows.empty:
                continue
            if order_col:
                session_rows = session_rows.sort_values(order_col)

            agent_row = noncc_df[noncc_df["session_id"].astype(str) == sid]
            agent_type = str(agent_row.iloc[0]["agent"]) if not agent_row.empty else "unknown"

            try:
                record = adapt_swechat_session(sid, session_rows, agent_type, _NON_CC_TOOL_MAP)
                record["source"] = "swechat_noncc"
                fh.write(json.dumps(record) + "\n")
                written += 1
                per_agent_counts[agent_type] = per_agent_counts.get(agent_type, 0) + 1
            except Exception as exc:
                print(f"[adapt_noncc] ERROR: {sid}: {exc}", file=sys.stderr)

    print(f"Non-CC: {per_agent_counts}")
    return agent_breakdown


# ---------------------------------------------------------------------------
# Detector runner (shared by phases 4 and 5)
# ---------------------------------------------------------------------------


def _run_detectors_on_adapted(
    adapted_path: Path,
    source_label: str,
) -> tuple[list[dict[str, Any]], int]:
    """Run both detectors on every session in an adapted JSONL file.

    Returns:
        (session_signal_records, total_sessions_processed)
    """
    from waste_detectors import detect_redundant_read, detect_repeated_failed_retry

    if not adapted_path.exists():
        print(f"[detectors] WARNING: {adapted_path} not found - skipping.", file=sys.stderr)
        return [], 0

    lines = adapted_path.read_text(encoding="utf-8").splitlines()
    records_in = [json.loads(l) for l in lines if l.strip()]

    signal_records: list[dict[str, Any]] = []

    try:
        from tqdm import tqdm
        iterator = tqdm(records_in, desc=f"Detecting [{source_label}]", unit="session")
    except ImportError:
        iterator = records_in  # type: ignore[assignment]

    for row in iterator:
        session_id: str = row["session_id"]
        turns: list[dict[str, Any]] = row["digest"]["turns"]
        agent_type: str = row.get("agent_type", "unknown")

        rfr_events = detect_repeated_failed_retry(session_id, turns)
        rr_events = detect_redundant_read(session_id, turns)
        all_events = rfr_events + rr_events

        path_a_events = [e for e in rr_events if e.evidence.get("path") == "A"]
        path_b_events = [e for e in rr_events if e.evidence.get("path") == "B"]

        signal_records.append(
            {
                "session_id": session_id,
                "source": source_label,
                "agent_type": agent_type,
                "turn_count": row.get("turn_count", len(turns)),
                "waste_events": [dataclasses.asdict(e) for e in all_events],
                "waste_event_count": len(all_events),
                "rfr_fired": bool(rfr_events),
                "rr_fired": bool(rr_events),
                "path_a_fired": bool(path_a_events),
                "path_b_fired": bool(path_b_events),
            }
        )

    return signal_records, len(records_in)


# ---------------------------------------------------------------------------
# Phase 6: Write output files
# ---------------------------------------------------------------------------


def phase_6_write_outputs(
    cc_signals: list[dict[str, Any]],
    noncc_signals: list[dict[str, Any]],
    distinct_users: int,
) -> dict[str, Any]:
    """Write public_waste_signals.jsonl and generalization_compare.json.

    Returns the compare dict for use in phase 7.
    """
    # Write combined signals
    all_signals = cc_signals + noncc_signals
    SIGNALS_PATH.write_text(
        "\n".join(json.dumps(r) for r in all_signals) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(all_signals)} signal records to {SIGNALS_PATH}")

    # CC stats
    cc_n = len(cc_signals)
    cc_rfr = sum(1 for r in cc_signals if r["rfr_fired"])
    cc_rr = sum(1 for r in cc_signals if r["rr_fired"])
    cc_pa = sum(1 for r in cc_signals if r["path_a_fired"])
    cc_pb = sum(1 for r in cc_signals if r["path_b_fired"])

    # Non-CC stats
    noncc_n = len(noncc_signals)
    noncc_rfr = sum(1 for r in noncc_signals if r["rfr_fired"])

    # Agent breakdown
    agent_counts: dict[str, int] = {}
    for r in noncc_signals:
        at = r.get("agent_type", "unknown")
        agent_counts[at] = agent_counts.get(at, 0) + 1

    pool_rfr_rate = _POOL_RFR_SESSIONS / _POOL_SESSIONS
    pool_rr_rate = _POOL_RR_ANY_SESSIONS / _POOL_SESSIONS
    cc_rfr_rate = cc_rfr / cc_n if cc_n else 0.0
    cc_rr_rate = cc_rr / cc_n if cc_n else 0.0
    noncc_rfr_rate = noncc_rfr / noncc_n if noncc_n else 0.0

    compare: dict[str, Any] = {
        "cc_pool": {
            "sessions": _POOL_SESSIONS,
            "rfr_fire_rate": round(pool_rfr_rate, 4),
            "rr_fire_rate": round(pool_rr_rate, 4),
            "path_a_sessions": _POOL_RR_PATH_A_SESSIONS,
            "path_b_sessions": _POOL_RR_PATH_B_SESSIONS,
        },
        "swechat_cc": {
            "sessions": cc_n,
            "distinct_users": distinct_users,
            "rfr_sessions_fired": cc_rfr,
            "rfr_fire_rate": round(cc_rfr_rate, 4),
            "rr_sessions_fired": cc_rr,
            "rr_fire_rate": round(cc_rr_rate, 4),
            "path_a_sessions": cc_pa,
            "path_a_note": (
                "UNAVAILABLE - CC v2.1.38 may have dropped hint"
                if cc_pa == 0
                else f"{cc_pa} sessions fired"
            ),
            "path_b_sessions": cc_pb,
            "path_b_note": (
                "UNAVAILABLE - CC v2.1.38 uses arrow format not \\d+\\t"
                if cc_pb == 0
                else f"{cc_pb} sessions fired"
            ),
        },
        "swechat_noncc": {
            "sessions": noncc_n,
            "agent_breakdown": agent_counts,
            "rfr_sessions_fired": noncc_rfr,
            "rfr_fire_rate": round(noncc_rfr_rate, 4),
            "path_b_note": "UNAVAILABLE - non-CC Read output lacks \\d+\\t format",
            "mapping_note": "tool-name mapping approximate; spurious fires are expected findings",
        },
    }

    COMPARE_PATH.write_text(json.dumps(compare, indent=2), encoding="utf-8")
    print(f"Wrote comparison JSON to {COMPARE_PATH}")

    return compare


# ---------------------------------------------------------------------------
# Phase 7: Comparison report
# ---------------------------------------------------------------------------


def phase_7_report(
    compare: dict[str, Any],
    cc_signals: list[dict[str, Any]],
) -> None:
    """Print structured comparison report to stdout."""
    pool = compare["cc_pool"]
    swcc = compare["swechat_cc"]
    noncc = compare["swechat_noncc"]

    pool_rfr_pct = pool["rfr_fire_rate"] * 100
    pool_rr_pct = pool["rr_fire_rate"] * 100
    cc_rfr_pct = swcc["rfr_fire_rate"] * 100
    cc_rr_pct = swcc["rr_fire_rate"] * 100
    noncc_rfr_pct = noncc["rfr_fire_rate"] * 100

    delta_rfr = cc_rfr_pct - pool_rfr_pct

    def _delta_label(delta: float) -> str:
        abs_d = abs(delta)
        direction = "HIGHER" if delta > 0 else "LOWER"
        if abs_d < 1.0:
            return f"SIMILAR ({delta:+.1f} pp delta)"
        return f"{direction} ({delta:+.1f} pp delta)"

    # Frozen file check via git diff
    def _frozen_check(rel_path: str) -> str:
        result = subprocess.run(
            ["git", "diff", "--exit-code", rel_path],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        return "FROZEN OK (no changes)" if result.returncode == 0 else f"MODIFIED - {result.stdout[:200]}"

    wd_check = _frozen_check("scripts/waste_detectors.py")
    adapter_check = _frozen_check("scripts/adapters/claudecode_adapter.py")

    print("\n" + "=" * 60)
    print("=== B5 GENERALIZATION RUN - COMPARISON REPORT ===")
    print("=" * 60)

    print(f"\n--- CC POOL (baseline) ---")
    print(f"Sessions: {pool['sessions']} | Developers: 1 (single developer)")
    print(f"REPEATED-FAILED-RETRY: {_POOL_RFR_SESSIONS}/{pool['sessions']} = {pool_rfr_pct:.1f}%")
    print(f"REDUNDANT-READ (any path): {_POOL_RR_ANY_SESSIONS}/{pool['sessions']} = {pool_rr_pct:.1f}%")
    print(f"  PATH A: {pool['path_a_sessions']} sessions | PATH B: {pool['path_b_sessions']} sessions")

    print(f"\n--- SWE-CHAT CC SUBSET (apples-to-apples: same agent, different developers) ---")
    print(f"Sessions: {swcc['sessions']} | Distinct users: {swcc['distinct_users']}")
    print(
        f"REPEATED-FAILED-RETRY: {swcc['rfr_sessions_fired']}/{swcc['sessions']} = {cc_rfr_pct:.1f}%"
    )
    print(f"  vs CC pool: [{_delta_label(delta_rfr)}]")
    print(
        f"REDUNDANT-READ PATH A: {swcc['path_a_note']}"
    )
    print(
        f"REDUNDANT-READ PATH B: UNAVAILABLE - CC v2.1.38 format change (Finding 2)"
    )
    print(
        "  CC pool used tab separator (^\\d+\\t); SWE-chat CC uses arrow format (v2.1.38+)"
    )
    print(
        "  The frozen detector silently fires zero times on current Claude Code."
    )
    print(
        "  This is a version-specific assumption in the detector - documented as maintenance issue."
    )

    if noncc["sessions"] > 0:
        print(f"\n--- SWE-CHAT NON-CC SUBSET (cross-agent: different agent + different developers) ---")
        print(f"Sessions: {noncc['sessions']} | Agents: {noncc['agent_breakdown']}")
        print(
            f"REPEATED-FAILED-RETRY: {noncc['rfr_sessions_fired']}/{noncc['sessions']} = "
            f"{noncc_rfr_pct:.1f}% [WEAKER CLAIM - tool-name-mapped, artifacts possible]"
        )
        print(f"REDUNDANT-READ PATH B: {noncc['path_b_note']}")
        print("REDUNDANT-READ PATH A: UNAVAILABLE - CC-proprietary string")
    else:
        print("\n--- SWE-CHAT NON-CC SUBSET: skipped ---")

    print(f"\n--- FROZEN DETECTOR CHECK ---")
    print(f"waste_detectors.py: {wd_check}")
    print(f"claudecode_adapter.py: {adapter_check}")

    # Sample waste events from CC subset
    print("\n--- SAMPLE WASTE EVENTS (CC subset, up to 5) ---")
    shown = 0
    for sig in cc_signals:
        if shown >= 5:
            break
        if not sig["waste_events"]:
            continue
        for ev in sig["waste_events"]:
            if shown >= 5:
                break
            sid_short = sig["session_id"][:8]
            detector = ev["detector"]
            turns = ev["turns"]
            snippet = ""
            if detector == "REPEATED-FAILED-RETRY":
                snippet = ev["evidence"].get("error_snippet", "")[:80]
            else:
                snippet = ev["evidence"].get("content_snippet", "")[:80]
            path = ev["evidence"].get("path", "")
            path_label = f" [PATH {path}]" if path else ""
            print(
                f"  [{detector}{path_label}] {sid_short}... turns={turns}: {repr(snippet)}"
            )
            shown += 1

    if shown == 0:
        print("  (no waste events detected in CC subset)")

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="B5 generalization run: frozen detectors on SWE-chat public data."
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Assume transcripts already in data/swechat_raw/transcripts/.",
    )
    parser.add_argument(
        "--skip-cc-adapt",
        action="store_true",
        help="Assume swechat_cc_adapted.jsonl already exists.",
    )
    parser.add_argument(
        "--skip-noncc",
        action="store_true",
        help="Skip the non-CC phase entirely.",
    )
    parser.add_argument(
        "--max-cc",
        type=int,
        default=None,
        metavar="N",
        help="Limit to first N CC sessions (for testing).",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point: run all B5 generalization phases in order."""
    args = _parse_args()

    sessions_parquet = DATA_DIR / "sessions.parquet"

    # --- Phase 0 ---
    print("\n=== PHASE 0: Metadata ===")
    uuids, cc_total, distinct_users = phase_0_metadata()

    # Apply max-cc cap to UUID list for downstream phases
    target_uuids = uuids if args.max_cc is None else uuids[: args.max_cc]

    # --- Phase 1 ---
    if not args.skip_download and not args.skip_cc_adapt:
        print("\n=== PHASE 1: Download CC Transcripts ===")
        present_uuids = phase_1_download(target_uuids, max_cc=None)  # cap already applied
    else:
        # Scan what's already present
        present_uuids = [
            u for u in target_uuids if (TRANSCRIPTS_DIR / f"{u}.jsonl").exists()
        ]
        print(f"\n=== PHASE 1: Skipped - {len(present_uuids)} transcripts present locally ===")

    # --- Phase 2 ---
    if not args.skip_cc_adapt:
        print("\n=== PHASE 2: Adapt CC Transcripts ===")
        phase_2_adapt_cc(present_uuids)
    else:
        print("\n=== PHASE 2: Skipped - using existing swechat_cc_adapted.jsonl ===")

    # --- Phase 3 ---
    agent_breakdown: dict[str, int] = {}
    if not args.skip_noncc:
        print("\n=== PHASE 3: Download + Adapt Non-CC Sessions ===")
        agent_breakdown = phase_3_noncc(sessions_parquet)
    else:
        print("\n=== PHASE 3: Skipped (--skip-noncc) ===")

    # --- Phase 4 ---
    print("\n=== PHASE 4: Run Detectors on CC Adapted Sessions ===")
    cc_signals, cc_processed = _run_detectors_on_adapted(CC_ADAPTED_PATH, "swechat_cc")
    cc_rfr = sum(1 for r in cc_signals if r["rfr_fired"])
    cc_rr = sum(1 for r in cc_signals if r["rr_fired"])
    cc_pa = sum(1 for r in cc_signals if r["path_a_fired"])
    cc_pb = sum(1 for r in cc_signals if r["path_b_fired"])
    print(
        f"CC: {cc_processed} sessions | RFR fired: {cc_rfr} | "
        f"RR fired: {cc_rr} (PATH A: {cc_pa}, PATH B: {cc_pb})"
    )

    # --- Phase 5 ---
    noncc_signals: list[dict[str, Any]] = []
    if not args.skip_noncc:
        print("\n=== PHASE 5: Run Detectors on Non-CC Adapted Sessions ===")
        noncc_signals, noncc_processed = _run_detectors_on_adapted(NONCC_ADAPTED_PATH, "swechat_noncc")
        noncc_rfr = sum(1 for r in noncc_signals if r["rfr_fired"])
        print(f"Non-CC: {noncc_processed} sessions | RFR fired: {noncc_rfr}")
    else:
        print("\n=== PHASE 5: Skipped (--skip-noncc) ===")

    # --- Phase 6 ---
    print("\n=== PHASE 6: Write Outputs ===")
    compare = phase_6_write_outputs(cc_signals, noncc_signals, distinct_users)

    # --- Phase 7 ---
    print("\n=== PHASE 7: Comparison Report ===")
    phase_7_report(compare, cc_signals)


if __name__ == "__main__":
    main()
