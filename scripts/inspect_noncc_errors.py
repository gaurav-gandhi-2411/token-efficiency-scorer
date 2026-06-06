from __future__ import annotations

"""Check tool-result error patterns in non-CC sessions to confirm RFR 0% is real."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

# RFR error detection patterns (from waste_detectors.py)
_ERROR_PATTERN = re.compile(
    r"exit code [1-9]|error:|Error:|ERROR:|command not found|failed:|Failed:|FAILED|"
    r"No such file|Permission denied|Traceback|exception:|Exception:",
    re.IGNORECASE,
)
_TRANSIENT_PATTERNS = re.compile(
    r"zone.*exhausted|no available zone|rate.?limit|429|too many requests|"
    r"quota exceeded|temporarily unavailable|service unavailable|"
    r"gh pr checks.*pending|gh pr checks.*no checks",
    re.IGNORECASE,
)

path = ROOT / "data" / "swechat_noncc_adapted.jsonl"
lines = path.read_text(encoding="utf-8").splitlines()

total_tool_turns = 0
error_tool_turns = 0
transient_only = 0
error_after_bash = 0
sessions_with_bash_error = 0

for line in lines:
    if not line.strip():
        continue
    row = json.loads(line)
    turns = row["digest"]["turns"]
    session_has_bash_error = False

    for i, t in enumerate(turns):
        if t["role"] == "tool":
            total_tool_turns += 1
            snip = t.get("content_snippet", "")
            if _ERROR_PATTERN.search(snip):
                if _TRANSIENT_PATTERNS.search(snip):
                    transient_only += 1
                else:
                    error_tool_turns += 1
                    # Check if preceding AI turn had Bash
                    prev_ai = None
                    for j in range(i - 1, -1, -1):
                        if turns[j]["role"] == "ai":
                            prev_ai = turns[j]
                            break
                    if prev_ai and "Bash" in prev_ai.get("tool_names", []):
                        error_after_bash += 1
                        session_has_bash_error = True

    if session_has_bash_error:
        sessions_with_bash_error += 1

print(f"Total tool turns: {total_tool_turns}")
print(f"Error tool turns (non-transient): {error_tool_turns}")
print(f"Transient-only tool turns: {transient_only}")
print(f"Error turns after Bash call: {error_after_bash}")
print(f"Sessions with at least one Bash error: {sessions_with_bash_error}")

# Sample some error snippets
print("\nSample Bash error snippets (up to 10):")
shown = 0
for line in lines:
    if shown >= 10:
        break
    if not line.strip():
        continue
    row = json.loads(line)
    turns = row["digest"]["turns"]
    for i, t in enumerate(turns):
        if shown >= 10:
            break
        if t["role"] == "tool":
            snip = t.get("content_snippet", "")
            if _ERROR_PATTERN.search(snip) and not _TRANSIENT_PATTERNS.search(snip):
                prev_ai = None
                for j in range(i - 1, -1, -1):
                    if turns[j]["role"] == "ai":
                        prev_ai = turns[j]
                        break
                if prev_ai and "Bash" in prev_ai.get("tool_names", []):
                    print(f"  Session {row['session_id'][:8]}: {repr(snip[:120])}")
                    shown += 1
