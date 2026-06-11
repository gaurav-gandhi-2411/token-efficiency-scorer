from __future__ import annotations

"""tes/contribution.py — Allow-listed corpus contribution payload builder.

P7: LOCAL FILE ONLY. NOTHING IS TRANSMITTED.
Payload is built field-by-field from the allow-list (ALLOWED_FIELDS).
Never serializes a session object and removes fields.
"""

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import tes

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

CONTRIBUTION_SCHEMA_VERSION: str = "1"

ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "task_type",
        "real_tokens",
        "token_count_input",
        "token_count_output",
        "cache_creation",
        "cache_read",
        "waste_event_count",
        "waste_detectors_fired",
        "model",
        "turn_count",
        "week_bucket",
        "tracegauge_version",
        "schema_version",
        "contributor_id",
    }
)

_KNOWN_MODELS: frozenset[str] = frozenset(
    {
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5",
        "claude-opus-4-1",
        "claude-opus-4",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
        "claude-3-opus",
        "claude-3-haiku",
        "claude-3-sonnet",
        "claude-3-5-sonnet",
        "claude-3-7-sonnet",
        "claude-3-5-haiku",
        "claude-haiku-3-5",
    }
)

_CONTRIBUTOR_ID_FILE: Path = Path.home() / ".tes" / "contributor_id.txt"

_FIELDS_EXCLUDED: list[str] = [
    "session_id",
    "source_path",
    "scored_at",
    "evidence_snippets",
    "content_snippets",
    "judge_reasoning",
    "interpretation",
    "prompts",
    "code_content",
    "proof_turn_content",
    "precise_timestamps",
    "free_text_fields",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ContributionManifest:
    schema_version: str
    tracegauge_version: str
    row_count: int
    fields_included: list[str]     # sorted list of the 14 allowed field names
    fields_excluded: list[str]     # explicit list of excluded content-bearing fields
    contributor_id: str | None
    built_at_week: str             # ISO week only, NOT precise timestamp


@dataclass
class ContributionPayload:
    rows: list[dict]
    manifest: ContributionManifest


# ---------------------------------------------------------------------------
# contributor_id management
# ---------------------------------------------------------------------------


def get_or_create_contributor_id() -> str:
    """Read contributor UUID from ~/.tes/contributor_id.txt, creating it if absent.

    The UUID is random-opaque only — no hostname, username, or identifying info.
    """
    _CONTRIBUTOR_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _CONTRIBUTOR_ID_FILE.exists():
        stored = _CONTRIBUTOR_ID_FILE.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    new_id = str(uuid.uuid4())
    _CONTRIBUTOR_ID_FILE.write_text(new_id + "\n", encoding="utf-8")
    return new_id


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _week_bucket_from_mtime(mtime: float) -> str:
    """Convert a POSIX float mtime to an ISO week string like '2026-W23'."""
    iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _allowed_model(raw_model: str | None) -> str:
    """Strip date suffix and check against _KNOWN_MODELS; return 'other' if unrecognized."""
    if not raw_model:
        return "other"
    stripped = re.sub(r"-\d{8}$", "", raw_model)
    return stripped if stripped in _KNOWN_MODELS else "other"


def _extract_waste_detectors(waste_events: list[dict]) -> list[str]:
    """Return sorted unique detector_type names from waste_events — never evidence/content."""
    names = {
        event["detector_type"]
        for event in waste_events
        if isinstance(event, dict) and "detector_type" in event
    }
    return sorted(names)


def _null_components() -> dict[str, None]:
    return {
        "token_count_input": None,
        "token_count_output": None,
        "cache_creation": None,
        "cache_read": None,
        "model": None,
    }


def _get_source_components(source_path: str) -> dict[str, int | None]:
    """Return token component aggregates from re-adapting source JSONL.

    Returns all-None dict if source is inaccessible or re-adaptation fails.
    This mirrors the backfill_cost() pattern in store.py: re-adapt from source
    because token_count_input, cache_creation, cache_read, model are not stored columns.
    """
    try:
        from tes.adapt import adapt_session
        from tes._digest import reconstruct_digest

        p = Path(source_path)
        if not p.exists():
            return _null_components()
        record = adapt_session(p)
        digest = reconstruct_digest(record["digest"])
        ai_turns = [t for t in digest.turns if t.role == "ai"]
        raw_model = ai_turns[0].model if ai_turns else None
        return {
            "token_count_input": sum(t.token_count_input for t in ai_turns),
            "token_count_output": sum(t.token_count_output for t in ai_turns),
            "cache_creation": sum(t.cache_creation for t in ai_turns),
            "cache_read": sum(t.cache_read for t in ai_turns),
            "model": _allowed_model(raw_model),
        }
    except Exception:
        return _null_components()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_contribution_payload(
    conn: sqlite3.Connection,
    *,
    contributor_id: str | None,
    include_source_components: bool = True,
) -> ContributionPayload:
    """Build the allow-listed corpus contribution payload from the SQLite store.

    Each row is constructed field-by-field from ALLOWED_FIELDS only.
    No dict unpacking is used — a new store column cannot leak into the payload.

    Parameters
    ----------
    conn:
        Open SQLite connection to the TES store.
    contributor_id:
        Pre-resolved contributor UUID, or None when anonymous=True.
    include_source_components:
        When True, re-adapt source JSONL files to populate token_count_input,
        token_count_output, cache_creation, cache_read, and model.
        When False (or source is inaccessible), those fields are None.
    """
    from tes.store import list_sessions

    session_rows = list_sessions(conn, limit=100_000)
    rows: list[dict] = []

    tracegauge_version = tes.__version__
    # Use the current wall-clock week for the manifest timestamp (not per-row mtime).
    now_iso = datetime.now(tz=timezone.utc).isocalendar()
    built_at_week = f"{now_iso.year}-W{now_iso.week:02d}"

    for session_row in session_rows:
        source_path: str | None = session_row.get("source_path")
        mtime: float | None = session_row.get("source_mtime")

        if include_source_components and source_path:
            components = _get_source_components(source_path)
        else:
            components = _null_components()

        # Build the payload row FIELD-BY-FIELD from the 14 allow-listed fields.
        # NEVER use {**session_row} — explicit construction prevents future column leakage.
        payload_row: dict = {
            "task_type": session_row.get("task_type"),
            "real_tokens": session_row.get("real_tokens"),
            "token_count_input": components["token_count_input"],
            "token_count_output": components["token_count_output"],
            "cache_creation": components["cache_creation"],
            "cache_read": components["cache_read"],
            "waste_event_count": session_row.get("waste_event_count"),
            "waste_detectors_fired": _extract_waste_detectors(
                session_row.get("waste_events") or []
            ),
            "model": components["model"],
            "turn_count": session_row.get("turn_count"),
            "week_bucket": _week_bucket_from_mtime(mtime) if mtime is not None else None,
            "tracegauge_version": tracegauge_version,
            "schema_version": CONTRIBUTION_SCHEMA_VERSION,
            "contributor_id": contributor_id,
        }
        rows.append(payload_row)

    manifest = ContributionManifest(
        schema_version=CONTRIBUTION_SCHEMA_VERSION,
        tracegauge_version=tracegauge_version,
        row_count=len(rows),
        fields_included=sorted(ALLOWED_FIELDS),
        fields_excluded=_FIELDS_EXCLUDED,
        contributor_id=contributor_id,
        built_at_week=built_at_week,
    )
    return ContributionPayload(rows=rows, manifest=manifest)


__all__ = [
    "CONTRIBUTION_SCHEMA_VERSION",
    "ALLOWED_FIELDS",
    "ContributionManifest",
    "ContributionPayload",
    "get_or_create_contributor_id",
    "build_contribution_payload",
]
