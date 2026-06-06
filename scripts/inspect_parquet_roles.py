from __future__ import annotations

"""Check role distribution in conversations.parquet for non-CC session IDs."""

import json
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "swechat_raw"

sessions_df = pd.read_parquet(DATA_DIR / "sessions.parquet")

# Non-CC session IDs
cc_mask = sessions_df["agent"].str.lower().str.contains("claude", na=False)
unknown_mask = sessions_df["agent"].str.lower() == "unknown"
noncc_df = sessions_df[~cc_mask & ~unknown_mask]
noncc_ids = set(noncc_df["session_id"].astype(str).tolist())
print(f"Non-CC sessions in sessions.parquet: {len(noncc_ids)}")
print(f"Agent breakdown: {noncc_df['agent'].value_counts().to_dict()}")

# Load conversations.parquet
print(f"\nLoading conversations.parquet...")
conv_df = pq.read_table(str(DATA_DIR / "conversations.parquet")).to_pandas()
print(f"Total rows: {len(conv_df)}")
print(f"Columns: {list(conv_df.columns)}")

# Filter to non-CC sessions only
noncc_conv = conv_df[conv_df["session_id"].astype(str).isin(noncc_ids)]
print(f"\nNon-CC rows in conversations.parquet: {len(noncc_conv)}")

# Role distribution in non-CC rows
print(f"Role distribution in non-CC conversations:")
for role, cnt in noncc_conv["role"].value_counts().items():
    print(f"  {role!r}: {cnt}")

# Check by agent type
print("\nRole distribution by agent (top 3 agents):")
for agent in noncc_df["agent"].value_counts().head(3).index:
    agent_sessions = set(noncc_df[noncc_df["agent"] == agent]["session_id"].astype(str))
    agent_conv = conv_df[conv_df["session_id"].astype(str).isin(agent_sessions)]
    roles = agent_conv["role"].value_counts().to_dict()
    print(f"  {agent}: {roles}")

# Sample tool_result rows
tool_result_rows = noncc_conv[noncc_conv["role"] == "tool_result"]
print(f"\nTool_result rows in non-CC: {len(tool_result_rows)}")
if not tool_result_rows.empty:
    sample = tool_result_rows.head(3)
    for _, row in sample.iterrows():
        content_preview = str(row.get("content", ""))[:100]
        print(f"  Session {str(row.get('session_id',''))[:8]}: {repr(content_preview)}")
