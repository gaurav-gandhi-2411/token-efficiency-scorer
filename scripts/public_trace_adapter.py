from __future__ import annotations

"""public_trace_adapter.py — Adapt SWE-chat public trajectory data to the layer1_outputs digest schema.

SWE-chat (SALT-NLP/SWE-chat on HuggingFace) captures real coding agent sessions from
Claude Code, OpenCode, Gemini CLI, Cursor, and Factory AI Droid agents collected via the
Entire.io CLI checkpoint logger.

The digest schema consumed by frozen waste detectors requires:
  - role: "ai" | "user" | "tool"
  - tool_names: list[str]  — CC-native names ("Bash", "Read", "Write", "Edit")
  - content_snippet: str   — first 300 chars verbatim (no synthetic line numbers)
  - token_count_input, token_count_output, cache_read, h2_duplicate: all 0/False for public data

PATH B integrity constraint: content_snippet MUST NOT receive synthetic \\d+\\t prefixes.
The CC Read tool natively emits line-numbered output; non-CC sessions lack this format,
making PATH B unavailable on non-CC sessions — that is a research finding, not a bug.

Tool name mapping for non-CC agents is approximate; spurious detector fires from incorrect
mapping are expected and documented findings rather than bugs in the detectors.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SNIPPET_MAX_CHARS: int = 300
_TASK_DESC_MAX_CHARS: int = 800

# Known CC-native tool names from SWE-chat paper Table 7.
# Non-CC tool names mapped to their nearest CC equivalent for cross-agent
# comparability. This mapping is intentionally conservative: only tool names
# with clear semantic equivalents are mapped; the rest pass through as-is.
#
# Sources:
#   - Table 7 in the SWE-chat paper (arXiv:2604.20779): CC tools are "Read",
#     "Edit", "Write", "Glob", "Grep", "Bash", "WebFetch", "Task", etc.
#   - non-CC tool names observed: "read_file", "write_file", "run_command",
#     "create_file", "edit_file", "list_directory", "search_files".
#   - Gemini CLI uses snake_case variants; OpenCode and Cursor may use similar.
_NON_CC_TOOL_MAP: dict[str, str] = {
    # read equivalents
    "read_file": "Read",
    "view_file": "Read",
    "read_file_content": "Read",
    # write/create equivalents
    "write_file": "Write",
    "create_file": "Write",
    "write_to_file": "Write",
    # edit equivalents
    "edit_file": "Edit",
    "replace_in_file": "Edit",
    "str_replace_editor": "Edit",
    "apply_diff": "Edit",
    # bash/shell equivalents
    "run_command": "Bash",
    "execute_command": "Bash",
    "bash": "Bash",
    "shell": "Bash",
    "run_bash": "Bash",
    "computer": "Bash",
    # glob equivalents
    "list_directory": "Glob",
    "list_dir": "Glob",
    "find_files": "Glob",
    # grep equivalents
    "search_files": "Grep",
    "search_in_files": "Grep",
    "grep": "Grep",
    "ripgrep": "Grep",
}

# CC-native tool names (no mapping needed for these).
_CC_NATIVE_TOOLS: frozenset[str] = frozenset(
    {
        "Read",
        "Write",
        "Edit",
        "MultiEdit",
        "Bash",
        "Glob",
        "Grep",
        "WebFetch",
        "WebSearch",
        "Task",
        "TaskCreate",
        "TaskUpdate",
        "TaskOutput",
        "Agent",
        "SendMessage",
        "NotebookEdit",
        "NotebookRead",
        "TodoWrite",
        "TodoRead",
        "ToolSearch",
        "AskUserQuestion",
        "EnterPlanMode",
        "ExitPlanMode",
        "Skill",
    }
)

# Role values in conversations.parquet as inferred from the SWE-chat paper
# (arXiv:2604.20779, Section 2.1 and Appendix C.1):
#   "user"         — human prompt turn
#   "assistant"    — agent text response turn (may include thinking traces)
#   "tool_use"     — agent tool invocation
#   "tool_result"  — environment / tool response
#   "metadata"     — session metadata rows (skip)
_ROLE_USER = "user"
_ROLE_ASSISTANT = "assistant"
_ROLE_TOOL_USE = "tool_use"
_ROLE_TOOL_RESULT = "tool_result"
_ROLE_METADATA = "metadata"

# turn_type value that marks thinking/reasoning traces (skip these).
_TURN_TYPE_THINKING = "assistant_thinking"

# ---------------------------------------------------------------------------
# Schema probe
# ---------------------------------------------------------------------------


def probe_schema(data_dir: Path) -> dict[str, Any]:
    """Load sessions.parquet and a sample of conversations.parquet, return schema info.

    Requires valid HuggingFace authentication for gated dataset access.
    Files must be pre-downloaded to data_dir (sessions.parquet, conversations.parquet).

    Returns a dict with keys:
        sessions_columns, sessions_shape, sessions_head,
        conversations_columns, conversations_shape, conversations_head,
        role_values, turn_type_values, cc_tool_names, noncc_tool_names,
        agent_field, cc_session_count, noncc_session_count.
    """
    sessions_path = data_dir / "sessions.parquet"
    convs_path = data_dir / "conversations.parquet"

    result: dict[str, Any] = {}

    if sessions_path.exists():
        sessions_df = pd.read_parquet(sessions_path)
        result["sessions_columns"] = list(sessions_df.columns)
        result["sessions_shape"] = sessions_df.shape
        result["sessions_head"] = sessions_df.head(3).to_dict(orient="records")

        # Heuristic: find the agent-identifying column.
        candidate_cols = [
            c
            for c in sessions_df.columns
            if any(kw in c.lower() for kw in ["agent", "tool", "framework", "model", "client"])
        ]
        result["agent_column_candidates"] = candidate_cols

        if candidate_cols:
            agent_col = candidate_cols[0]
            result["agent_field"] = agent_col
            result["agent_values"] = sessions_df[agent_col].value_counts().to_dict()
            cc_mask = (
                sessions_df[agent_col].str.lower().str.contains("claude.?code|anthropic", na=False)
            )
            result["cc_session_count"] = int(cc_mask.sum())
            result["noncc_session_count"] = int((~cc_mask).sum())
        else:
            result["agent_field"] = None
    else:
        result["sessions_columns"] = []
        result["sessions_error"] = f"File not found: {sessions_path}"

    if convs_path.exists():
        import pyarrow.parquet as pq

        table = pq.read_table(convs_path).slice(0, 1000)
        convs_df = table.to_pandas()
        result["conversations_columns"] = list(convs_df.columns)
        result["conversations_shape"] = convs_df.shape

        if "role" in convs_df.columns:
            result["role_values"] = convs_df["role"].unique().tolist()

        if "turn_type" in convs_df.columns:
            result["turn_type_values"] = convs_df["turn_type"].dropna().unique().tolist()

        # Tool name discovery by role.
        if "role" in convs_df.columns and "tool_name" in convs_df.columns:
            tool_rows = convs_df[convs_df["role"] == _ROLE_TOOL_USE]
            result["all_tool_names"] = tool_rows["tool_name"].dropna().unique().tolist()

        result["conversations_head"] = convs_df.head(5).to_dict(orient="records")
    else:
        result["conversations_columns"] = []
        result["conversations_error"] = f"File not found: {convs_path}"

    return result


# ---------------------------------------------------------------------------
# Session identification
# ---------------------------------------------------------------------------


def identify_cc_sessions(
    sessions_df: pd.DataFrame,
    agent_col: str,
) -> tuple[set[str], set[str]]:
    """Return (cc_session_ids, noncc_session_ids) based on agent field.

    Identification is case-insensitive substring match on "claude" or "anthropic"
    in the agent column value.
    """
    cc_mask = (
        sessions_df[agent_col].str.lower().str.contains("claude.?code|claude|anthropic", na=False)
    )
    id_col = "session_id" if "session_id" in sessions_df.columns else sessions_df.columns[0]
    cc_ids: set[str] = set(sessions_df.loc[cc_mask, id_col].astype(str).tolist())
    noncc_ids: set[str] = set(sessions_df.loc[~cc_mask, id_col].astype(str).tolist())
    return cc_ids, noncc_ids


# ---------------------------------------------------------------------------
# Turn adaptation
# ---------------------------------------------------------------------------


def _map_tool_name(name: str, tool_name_map: dict[str, str] | None) -> str:
    """Map a non-CC tool name to its CC equivalent, or return name unchanged."""
    if tool_name_map is None:
        return name
    return tool_name_map.get(name, name)


def _make_turn(
    turn_index: int,
    role: str,
    tool_names: list[str],
    content_snippet: str,
) -> dict[str, Any]:
    return {
        "turn_index": turn_index,
        "role": role,
        "tool_names": tool_names,
        "content_snippet": content_snippet,
        "token_count_input": 0,
        "token_count_output": 0,
        "cache_read": 0,
        "h2_duplicate": False,
    }


def adapt_swechat_session(
    session_id: str,
    conv_rows: pd.DataFrame,
    agent_type: str,
    tool_name_map: dict[str, str] | None,
) -> dict[str, Any]:
    """Adapt one session's rows to the layer1_outputs digest record format.

    conv_rows must be pre-sorted by the ordering field (turn_id or equivalent).
    tool_name_map should be None for CC sessions (tool names pass through unchanged).
    For non-CC sessions, pass _NON_CC_TOOL_MAP; unmapped names pass through as-is.

    The function DOES NOT add synthetic line numbers to content_snippet — PATH B
    fires only when the source data natively contains \\d+\\t-prefixed content,
    which CC sessions produce natively via their Read tool output format.
    """
    turns: list[dict[str, Any]] = []
    turn_index = 0
    task_description = "N/A"
    task_set = False

    # Determine column names defensively — the actual schema may vary.
    has_turn_type = "turn_type" in conv_rows.columns
    has_tool_name = "tool_name" in conv_rows.columns
    content_col = next(
        (c for c in ["content", "text", "message", "body"] if c in conv_rows.columns),
        None,
    )
    role_col = "role" if "role" in conv_rows.columns else conv_rows.columns[0]

    for _, row in conv_rows.iterrows():
        role: str = str(row.get(role_col, ""))
        turn_type: str = str(row.get("turn_type", "")) if has_turn_type else ""
        raw_content: str = str(row.get(content_col, "") if content_col else "")

        # Skip metadata rows and thinking/reasoning traces.
        if role == _ROLE_METADATA:
            continue
        if turn_type == _TURN_TYPE_THINKING:
            continue
        # Skip entirely empty content that adds no signal.
        if not raw_content.strip() and role not in (_ROLE_TOOL_USE,):
            continue

        if role == _ROLE_TOOL_USE:
            raw_tool_name = str(row.get("tool_name", "") if has_tool_name else "")
            mapped = _map_tool_name(raw_tool_name, tool_name_map) if raw_tool_name else ""
            turns.append(_make_turn(turn_index, "ai", [mapped] if mapped else [], ""))
            turn_index += 1

        elif role == _ROLE_TOOL_RESULT:
            # Pass content verbatim — no line-number injection.
            snippet = raw_content[:_SNIPPET_MAX_CHARS]
            turns.append(_make_turn(turn_index, "tool", [], snippet))
            turn_index += 1

        elif role == _ROLE_ASSISTANT:
            snippet = raw_content[:_SNIPPET_MAX_CHARS]
            turns.append(_make_turn(turn_index, "ai", [], snippet))
            turn_index += 1

        elif role == _ROLE_USER:
            snippet = raw_content[:_SNIPPET_MAX_CHARS]
            if not task_set and snippet.strip():
                task_description = raw_content[:_TASK_DESC_MAX_CHARS]
                task_set = True
            turns.append(_make_turn(turn_index, "user", [], snippet))
            turn_index += 1

        # Any other role: skip (future-proof against schema additions).

    turn_count = len(turns)
    digest: dict[str, Any] = {
        "session_id": session_id,
        "domain": "unknown",
        "resolved": False,
        "total_tokens": 0,
        "turn_count": turn_count,
        "h2_duplicate_count": 0,
        "cache_hit_rate": 0.0,
        "p25_token_ratio": 1.0,
        "output_tokens_available": False,
        "task_description": task_description,
        "turns": turns,
    }

    return {
        "session_id": session_id,
        "source": "swechat_cc" if tool_name_map is None else "swechat_noncc",
        "agent_type": agent_type,
        "turn_count": turn_count,
        "digest": digest,
    }


# ---------------------------------------------------------------------------
# Batch adaptation
# ---------------------------------------------------------------------------


def _discover_ordering_col(conv_df: pd.DataFrame) -> str | None:
    """Return the column name used for turn ordering within a session."""
    for candidate in ["turn_id", "turn_index", "index", "seq", "sequence", "order", "id"]:
        if candidate in conv_df.columns:
            return candidate
    return None


def _discover_session_col(conv_df: pd.DataFrame) -> str | None:
    """Return the column name that links conversation rows to sessions."""
    for candidate in ["session_id", "session", "conversation_id", "conv_id"]:
        if candidate in conv_df.columns:
            return candidate
    return None


def _adapt_batch(
    conv_df: pd.DataFrame,
    session_ids: set[str],
    agent_type: str,
    tool_name_map: dict[str, str] | None,
    max_sessions: int | None,
    output_path: Path,
) -> int:
    """Write adapted records to output_path JSONL. Returns count written."""
    session_col = _discover_session_col(conv_df)
    order_col = _discover_ordering_col(conv_df)

    if session_col is None:
        print(
            "[adapter] WARNING: cannot identify session column in conversations.parquet",
            file=sys.stderr,
        )
        return 0

    written = 0
    ids_to_process = sorted(session_ids)
    if max_sessions is not None:
        ids_to_process = ids_to_process[:max_sessions]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as fh:
        for sid in ids_to_process:
            session_rows = conv_df[conv_df[session_col].astype(str) == str(sid)].copy()
            if session_rows.empty:
                continue
            if order_col:
                session_rows = session_rows.sort_values(order_col)

            record = adapt_swechat_session(sid, session_rows, agent_type, tool_name_map)
            fh.write(json.dumps(record) + "\n")
            written += 1

    return written


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point.

    Usage:
        python scripts/public_trace_adapter.py --schema-only
        python scripts/public_trace_adapter.py --max-sessions 50
    """
    parser = argparse.ArgumentParser(
        description="Adapt SWE-chat public trajectory data to layer1_outputs digest schema."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "data" / "swechat_raw",
        help="Directory containing downloaded SWE-chat parquet files.",
    )
    parser.add_argument(
        "--output-cc",
        type=Path,
        default=ROOT / "data" / "swechat_cc_adapted.jsonl",
        help="Output JSONL for CC sessions.",
    )
    parser.add_argument(
        "--output-noncc",
        type=Path,
        default=ROOT / "data" / "swechat_noncc_adapted.jsonl",
        help="Output JSONL for non-CC sessions.",
    )
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Probe and print schema only; do not adapt.",
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=None,
        metavar="N",
        help="Limit to first N sessions of each type (for testing).",
    )
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    schema_info = probe_schema(data_dir)

    print("\n=== Schema Probe Results ===")
    print(f"sessions.parquet columns: {schema_info.get('sessions_columns', 'N/A')}")
    print(f"sessions.parquet shape:   {schema_info.get('sessions_shape', 'N/A')}")
    print(f"agent field identified:   {schema_info.get('agent_field', 'N/A')}")
    print(f"agent values: {schema_info.get('agent_values', {})}")
    print(f"CC session count:         {schema_info.get('cc_session_count', 'N/A')}")
    print(f"non-CC session count:     {schema_info.get('noncc_session_count', 'N/A')}")
    print(f"\nconversations.parquet columns: {schema_info.get('conversations_columns', 'N/A')}")
    print(f"role values:     {schema_info.get('role_values', 'N/A')}")
    print(f"turn_type values: {schema_info.get('turn_type_values', 'N/A')}")
    print(f"tool names found: {schema_info.get('all_tool_names', 'N/A')}")

    if args.schema_only:
        return

    sessions_path = data_dir / "sessions.parquet"
    convs_path = data_dir / "conversations.parquet"

    if not sessions_path.exists() or not convs_path.exists():
        print(
            "[adapter] ERROR: parquet files not found. "
            "Download from SALT-NLP/SWE-chat on HuggingFace first.",
            file=sys.stderr,
        )
        sys.exit(1)

    sessions_df = pd.read_parquet(sessions_path)
    agent_col: str | None = schema_info.get("agent_field")

    if agent_col is None:
        print(
            "[adapter] ERROR: could not identify agent field in sessions.parquet.",
            file=sys.stderr,
        )
        sys.exit(1)

    cc_ids, noncc_ids = identify_cc_sessions(sessions_df, agent_col)

    import pyarrow.parquet as pq

    conv_df = pq.read_table(convs_path).to_pandas()

    # Determine unique non-CC agent types for reporting.
    session_col = _discover_session_col(conv_df)
    agent_counts: dict[str, int] = {}
    if session_col:
        noncc_df = sessions_df[sessions_df["session_id"].astype(str).isin(noncc_ids)]
        agent_counts = noncc_df[agent_col].value_counts().to_dict()

    # Identify which non-CC tools are actually present and unmapped.
    all_tool_names: list[str] = schema_info.get("all_tool_names", [])
    cc_native_present = [t for t in all_tool_names if t in _CC_NATIVE_TOOLS]
    mapped_noncc = {t: _NON_CC_TOOL_MAP[t] for t in all_tool_names if t in _NON_CC_TOOL_MAP}
    unmapped = [
        t for t in all_tool_names if t not in _CC_NATIVE_TOOLS and t not in _NON_CC_TOOL_MAP
    ]

    # Adapt CC sessions (no tool name mapping).
    cc_written = _adapt_batch(
        conv_df, cc_ids, "claude_code", None, args.max_sessions, args.output_cc
    )

    # Adapt non-CC sessions (apply mapping).
    noncc_written = _adapt_batch(
        conv_df, noncc_ids, "noncc_agent", _NON_CC_TOOL_MAP, args.max_sessions, args.output_noncc
    )

    # Summary table.
    print(f"\nCC sessions:     {cc_written} (adapted to {args.output_cc})")
    print(f"Non-CC sessions: {noncc_written} (adapted to {args.output_noncc})")
    print(f"  Non-CC agents: {agent_counts}")
    print(f"Tool name mapping used for non-CC: {mapped_noncc}")
    print(f"Unmapped tool names (passed through): {unmapped}")
    print(f"CC-native tools confirmed present:    {cc_native_present}")
    print()
    print(
        "PATH A note: Will fire on CC sessions natively (CC Read tool produces "
        "'File unchanged since last read')"
    )
    print("PATH A note: UNAVAILABLE on non-CC sessions (no CC-proprietary verdict string)")
    print("PATH B note: Will fire on CC sessions where native Read output has \\d+\\t prefix")
    print("PATH B note: UNAVAILABLE on non-CC sessions without native line-numbered read output")


if __name__ == "__main__":
    main()
