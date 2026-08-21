from __future__ import annotations

"""tes/impact.py — Code-impact reconstruction from Edit/Write/MultiEdit/
NotebookEdit tool-call payloads.

Reimplementation from the DOCUMENTED shape of these tool calls (their own
argument names, observed in this project's real Claude Code transcripts and
Anthropic's published tool schemas) — not ported from any external
project's source. No code copied from any competitor.

**Scope, per XX2.1's real-data finding** (see
``docs/audit/COMPETITIVE_GAP_ANALYSIS.md`` WW1.3 — 883 real transcript
files, 434,774 lines scanned): `Edit` (10,175 real occurrences) and `Write`
(3,059) dominate and are fully supported with no ambiguity beyond `Write`'s
own inherent one (see below). `MultiEdit`/`NotebookEdit` are real Claude
Code tool names with ZERO occurrences on this project's own real corpus —
extraction for them is written (best-effort, defensive) but every operation
from either is flagged ``untested_tool_shape=True`` and must never be
presented with the same confidence as Edit/Write-derived numbers.
`apply_patch`/`str_replace_editor` are Codex/computer-use tool names, not
Claude Code ones — out of scope entirely, not attempted.

**The Write ambiguity, tracked not swallowed (XX2.3)**: a `Write` call's
payload contains the new file content but never the content it replaced —
additions are countable, deletions are not. Every ``Write``-derived
``EditOperation`` carries ``prior_content_unknown=True`` so callers can
report what fraction of a total additions figure rests on this assumption,
rather than silently treating a full-file rewrite as if the diff were exact.
"""

import json
from dataclasses import dataclass
from typing import Any

EDIT_TOOL_NAMES = frozenset({"edit", "write", "multiedit", "notebookedit"})
"""Lowercased tool names this module knows how to parse. Any other tool
name is not an edit operation at all (Read, Bash, Grep, etc.)."""

UNTESTED_TOOL_NAMES = frozenset({"multiedit", "notebookedit"})
"""Real Claude Code tool names with zero occurrences in this project's own
real-data check — extraction is written but never exercised against real
payloads. See module docstring."""


@dataclass
class EditOperation:
    path: str
    additions: int
    deletions: int
    tool: str  # the original (non-lowercased) tool name
    prior_content_unknown: bool
    untested_tool_shape: bool


def _line_count(text: str) -> int:
    """Number of lines in text, counting a trailing newline as ending the
    last line rather than starting an empty one (matches how a text editor
    reports line counts, not a naive ``split("\\n")`` count)."""
    if not text:
        return 0
    normalized = text.replace("\r\n", "\n")
    count = normalized.count("\n") + 1
    return count - 1 if normalized.endswith("\n") else count


def _clean_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def extract_edit_operations(tool_name: str, tool_input: dict[str, Any]) -> list[EditOperation]:
    """Extract EditOperation(s) from one tool_use block's name + input.

    Returns [] for any tool this module doesn't recognize, or for a
    recognized tool whose input is missing the fields it needs (fails
    closed — never guesses a path or a line count from partial data).
    """
    if not isinstance(tool_input, dict):
        return []
    name_lower = tool_name.lower()
    if name_lower not in EDIT_TOOL_NAMES:
        return []
    untested = name_lower in UNTESTED_TOOL_NAMES

    if name_lower == "edit":
        path = _clean_path(tool_input.get("file_path"))
        if path is None:
            return []
        old = tool_input.get("old_string", "")
        new = tool_input.get("new_string", "")
        return [
            EditOperation(
                path=path,
                additions=_line_count(str(new)),
                deletions=_line_count(str(old)),
                tool=tool_name,
                prior_content_unknown=False,
                untested_tool_shape=False,
            )
        ]

    if name_lower == "write":
        path = _clean_path(tool_input.get("file_path"))
        if path is None:
            return []
        content = tool_input.get("content", "")
        return [
            EditOperation(
                path=path,
                additions=_line_count(str(content)),
                deletions=0,
                tool=tool_name,
                prior_content_unknown=True,
                untested_tool_shape=False,
            )
        ]

    if name_lower == "multiedit":
        path = _clean_path(tool_input.get("file_path"))
        edits = tool_input.get("edits")
        if path is None or not isinstance(edits, list):
            return []
        ops: list[EditOperation] = []
        for e in edits:
            if not isinstance(e, dict):
                continue
            old = e.get("old_string", "")
            new = e.get("new_string", "")
            ops.append(
                EditOperation(
                    path=path,
                    additions=_line_count(str(new)),
                    deletions=_line_count(str(old)),
                    tool=tool_name,
                    prior_content_unknown=False,
                    untested_tool_shape=untested,
                )
            )
        return ops

    if name_lower == "notebookedit":
        # Best-effort, defensive: the exact real schema is unverified (zero
        # real samples, see module docstring). Tries the field names
        # documented for Claude's NotebookEdit tool; returns [] rather than
        # guessing if none are present.
        path = _clean_path(tool_input.get("notebook_path"))
        if path is None:
            return []
        new_source = tool_input.get("new_source")
        if new_source is None:
            return []
        edit_mode = str(tool_input.get("edit_mode", "replace"))
        if edit_mode == "insert":
            return [
                EditOperation(
                    path=path,
                    additions=_line_count(str(new_source)),
                    deletions=0,
                    tool=tool_name,
                    prior_content_unknown=True,
                    untested_tool_shape=True,
                )
            ]
        # "replace" (default) or "delete": treat as a replace of unknown prior
        # content -- same honest ambiguity as Write, since no old_source is
        # ever supplied by this tool's own documented arguments.
        return [
            EditOperation(
                path=path,
                additions=_line_count(str(new_source)),
                deletions=0,
                tool=tool_name,
                prior_content_unknown=True,
                untested_tool_shape=True,
            )
        ]

    return []  # pragma: no cover — unreachable, name already filtered above


# ---------------------------------------------------------------------------
# Corpus-wide aggregation (XX2.5/AB3.2) — plain transparent counts, no
# composite "risk score". Recommended against a weighted composite in
# docs/audit/COMPETITIVE_GAP_ANALYSIS.md WW2.2: a hand-weighted blend of
# signals presented as one number is exactly the "adds a number without
# uncertainty attached" dilution that document flags. Every figure here is
# a direct count or a plainly-labeled fraction, nothing inferred.
# ---------------------------------------------------------------------------


@dataclass
class FileChurn:
    path: str
    edits: int
    additions: int
    deletions: int
    sessions_touched: int


@dataclass
class ImpactReport:
    sessions_with_data: int  # edit_operations column present (possibly [])
    sessions_legacy: int  # edit_operations column NULL -- scored before this existed
    total_operations: int
    total_additions: int
    total_deletions: int
    prior_content_unknown_additions: int  # additions from Write/NotebookEdit ops specifically
    untested_tool_shape_operations: int  # ops from MultiEdit/NotebookEdit specifically
    top_files: list[FileChurn]
    top_directories: list[FileChurn]

    @property
    def prior_content_unknown_pct(self) -> float | None:
        return (
            100.0 * self.prior_content_unknown_additions / self.total_additions
            if self.total_additions
            else None
        )

    @property
    def untested_tool_shape_pct(self) -> float | None:
        return (
            100.0 * self.untested_tool_shape_operations / self.total_operations
            if self.total_operations
            else None
        )


def _directory_of(path: str) -> str:
    normalized = path.replace("\\", "/")
    idx = normalized.rfind("/")
    return normalized[:idx] if idx > 0 else "."


def compute_impact_report(rows: list[dict[str, Any]], top_n: int = 10) -> ImpactReport:
    """Aggregate persisted edit_operations across session rows (from
    tes.store.list_sessions) into a corpus-wide report.

    ``rows`` entries need only an ``edit_operations`` key (the raw
    JSON-or-None column value) -- this function does not touch the
    database or any source file, so it works identically whether the rows
    came from the real store or an isolated scratch one.
    """
    sessions_with_data = 0
    sessions_legacy = 0
    total_operations = 0
    total_additions = 0
    total_deletions = 0
    prior_content_unknown_additions = 0
    untested_tool_shape_operations = 0

    files: dict[str, dict[str, Any]] = {}

    for row in rows:
        raw = row.get("edit_operations")
        if raw is None:
            sessions_legacy += 1
            continue
        sessions_with_data += 1
        try:
            ops = json.loads(raw)
        except (TypeError, ValueError):
            continue
        session_id = row.get("session_id", "")
        for op in ops:
            total_operations += 1
            total_additions += op.get("additions", 0)
            total_deletions += op.get("deletions", 0)
            if op.get("prior_content_unknown"):
                prior_content_unknown_additions += op.get("additions", 0)
            if op.get("untested_tool_shape"):
                untested_tool_shape_operations += 1

            path = op.get("path")
            if not path:
                continue
            rec = files.setdefault(
                path, {"edits": 0, "additions": 0, "deletions": 0, "sessions": set()}
            )
            rec["edits"] += 1
            rec["additions"] += op.get("additions", 0)
            rec["deletions"] += op.get("deletions", 0)
            rec["sessions"].add(session_id)

    file_rows = [
        FileChurn(
            path=p,
            edits=r["edits"],
            additions=r["additions"],
            deletions=r["deletions"],
            sessions_touched=len(r["sessions"]),
        )
        for p, r in files.items()
    ]
    top_files = sorted(file_rows, key=lambda f: f.edits, reverse=True)[:top_n]

    dir_agg: dict[str, dict[str, Any]] = {}
    for p, r in files.items():
        d = _directory_of(p)
        rec = dir_agg.setdefault(d, {"edits": 0, "additions": 0, "deletions": 0, "sessions": set()})
        rec["edits"] += r["edits"]
        rec["additions"] += r["additions"]
        rec["deletions"] += r["deletions"]
        rec["sessions"] |= r["sessions"]
    dir_rows = [
        FileChurn(
            path=d,
            edits=r["edits"],
            additions=r["additions"],
            deletions=r["deletions"],
            sessions_touched=len(r["sessions"]),
        )
        for d, r in dir_agg.items()
    ]
    top_directories = sorted(dir_rows, key=lambda f: f.edits, reverse=True)[:top_n]

    return ImpactReport(
        sessions_with_data=sessions_with_data,
        sessions_legacy=sessions_legacy,
        total_operations=total_operations,
        total_additions=total_additions,
        total_deletions=total_deletions,
        prior_content_unknown_additions=prior_content_unknown_additions,
        untested_tool_shape_operations=untested_tool_shape_operations,
        top_files=top_files,
        top_directories=top_directories,
    )


__all__ = [
    "EditOperation",
    "EDIT_TOOL_NAMES",
    "UNTESTED_TOOL_NAMES",
    "extract_edit_operations",
    "FileChurn",
    "ImpactReport",
    "compute_impact_report",
]
