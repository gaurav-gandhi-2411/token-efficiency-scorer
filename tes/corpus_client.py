from __future__ import annotations

"""tes/corpus_client.py — Opt-in transmission of content-free session aggregates.

THE TRANSMISSION BOUNDARY. This is the only module in tracegauge that sends
session-derived data off-machine without a per-call API key the user typed in
(contrast: the API judge in judge.py, which is also consent-gated but sends
raw content). Everything here sends ONLY the P7 content-free payload
(tes.contribution.build_contribution_payload) — never code, prompts, paths,
or free text.

Three independent safety layers, all unconditional (cannot be bypassed by a
caller forgetting a flag):
  1. consent_given: bool — mirrors tes.judge.score_trajectory_api. False means
     ZERO network activity, checked before anything else.
  2. verify_payload_content_free() — re-verifies the ACTUAL serialized POST
     body (not the in-memory dict) against the allow-list and per-field value
     rules, every time, even if the payload was already built by the
     already-tested contribution.py. Runs BEFORE the httpx call. A failure
     raises ContentLeakGuardError and the network call never happens.
  3. RLS on the Supabase table (corpus/schema.sql) — anon role can INSERT
     only; no SELECT/UPDATE/DELETE. Deletion happens only through the
     withdraw Edge Function, which validates contributor_id server-side.

No new dependency: httpx is already a declared dep (used by tes.judge).
"""

import json
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from tes.contribution import (
    _CONTRIBUTOR_ID_FILE,
    _KNOWN_DETECTOR_NAMES,
    _KNOWN_MODELS,
    _KNOWN_TASK_TYPES,
    ALLOWED_FIELDS,
    build_contribution_payload,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The community corpus destination is NOT embedded as a hardcoded constant:
# no production Supabase project exists yet (this ships pre-launch). A
# caller (CLI or test) must supply a CorpusConfig, normally built from
# TES_CORPUS_URL / TES_CORPUS_ANON_KEY / TES_CORPUS_WITHDRAW_URL env vars.
# This also lets the pre-publish round-trip proof point at a disposable test
# project without touching code.

_BLOCKED_LOG_PATH: Path = Path.home() / ".tes" / "contribution_blocked.log"

_MAX_STRING_LEN = 30  # tightened from 50 — see verify_payload_content_free docstring
_WEEK_BUCKET_RE = re.compile(r"^\d{4}-W\d{2}$")
_SCHEMA_VERSION_RE = re.compile(r"^\d+$")
_TRACEGAUGE_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([.\-][0-9A-Za-z]+)?$")
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE
)

_NUMERIC_FIELDS = frozenset(
    {
        "real_tokens",
        "token_count_input",
        "token_count_output",
        "cache_creation",
        "cache_read",
        "waste_event_count",
        "turn_count",
    }
)


@dataclass(frozen=True)
class CorpusConfig:
    """Non-secret destination config. The anon key is publishable by design —
    it is scoped by RLS (insert-only), not by secrecy."""

    supabase_url: str
    supabase_anon_key: str
    withdraw_function_url: str
    table_name: str = "corpus_contributions"

    @classmethod
    def from_env(cls) -> CorpusConfig | None:
        import os

        url = os.environ.get("TES_CORPUS_URL")
        key = os.environ.get("TES_CORPUS_ANON_KEY")
        withdraw_url = os.environ.get("TES_CORPUS_WITHDRAW_URL")
        if not (url and key and withdraw_url):
            return None
        return cls(supabase_url=url, supabase_anon_key=key, withdraw_function_url=withdraw_url)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ContentLeakGuardError(Exception):
    """Raised when the send-time content-free re-verification fails.

    Raising this MUST happen before any httpx call is made — callers must
    not catch this and retry with the network call anyway.
    """


# ---------------------------------------------------------------------------
# Send-time content-free re-verification (two-pass, on the ACTUAL POST bytes)
# ---------------------------------------------------------------------------


def _check_field_value(field: str, value: Any) -> str | None:
    """Return an error string if `value` is unsafe for `field`, else None.

    Each of the 14 allowed fields has its own explicit rule — the same
    field-by-field philosophy as build_contribution_payload(). contributor_id
    is a valid UUID4 string (36 chars) and is checked by format, not length;
    every OTHER string field is capped at 30 chars as a blanket safety net
    (the longest legitimate non-UUID value, week_bucket, is 8 chars — 30
    leaves headroom without being wide enough to smuggle a secret).
    """
    if field == "task_type":
        if value not in _KNOWN_TASK_TYPES and value != "other":
            return f"task_type {value!r} not in known set"
        return None
    if field == "model":
        if value is not None and value not in _KNOWN_MODELS and value != "other":
            return f"model {value!r} not in known set"
        if isinstance(value, str) and len(value) > _MAX_STRING_LEN:
            return f"model value exceeds {_MAX_STRING_LEN} chars"
        return None
    if field == "waste_detectors_fired":
        if not isinstance(value, list):
            return "waste_detectors_fired is not a list"
        for item in value:
            if not isinstance(item, str) or item not in _KNOWN_DETECTOR_NAMES:
                return f"waste_detectors_fired contains unknown value {item!r}"
        return None
    if field == "week_bucket":
        if value is not None and not _WEEK_BUCKET_RE.match(str(value)):
            return f"week_bucket {value!r} does not match YYYY-Www"
        return None
    if field == "schema_version":
        if not _SCHEMA_VERSION_RE.match(str(value)):
            return f"schema_version {value!r} does not match \\d+"
        return None
    if field == "tracegauge_version":
        if not _TRACEGAUGE_VERSION_RE.match(str(value)):
            return f"tracegauge_version {value!r} does not match a version string"
        return None
    if field == "contributor_id":
        if value is not None and not _UUID4_RE.match(str(value)):
            return "contributor_id is not a valid UUID4"
        return None
    if field in _NUMERIC_FIELDS:
        if value is not None and not isinstance(value, int):
            return f"{field} value {value!r} is not an int (numeric-as-string attempt?)"
        if isinstance(value, bool):  # bool is an int subclass in Python — reject explicitly
            return f"{field} value is a bool, not a numeric count"
        return None

    # Any string value not covered above (should be unreachable given
    # ALLOWED_FIELDS == the fields handled explicitly here) is still capped.
    if isinstance(value, str) and len(value) > _MAX_STRING_LEN:
        return f"{field} string value exceeds {_MAX_STRING_LEN} chars"
    return None


def verify_payload_content_free(serialized_body: bytes) -> None:
    """Re-verify the ACTUAL serialized POST body is content-free. Raises
    ContentLeakGuardError on any violation. Must run on the exact bytes about
    to be sent — not the in-memory row dicts — so nothing introduced by
    serialization (or a future code change) slips past this gate.

    Pass 1 (key-space): every key in every row must be exactly ALLOWED_FIELDS
    (extra OR missing keys abort — a row that's missing a field is not the
    payload build_contribution_payload() produces, and is refused rather than
    guessed at).

    Pass 2 (value-space): every value is checked by _check_field_value().
    """
    try:
        rows = json.loads(serialized_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ContentLeakGuardError(f"POST body is not valid UTF-8 JSON: {exc}") from exc

    if not isinstance(rows, list):
        raise ContentLeakGuardError("POST body must be a JSON array of rows")

    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ContentLeakGuardError(f"row {i} is not a JSON object")

        row_keys = set(row.keys())
        if row_keys != ALLOWED_FIELDS:
            extra = row_keys - ALLOWED_FIELDS
            missing = ALLOWED_FIELDS - row_keys
            raise ContentLeakGuardError(
                f"row {i} key-space mismatch — extra={sorted(extra)} missing={sorted(missing)}"
            )

        for field, value in row.items():
            error = _check_field_value(field, value)
            if error is not None:
                raise ContentLeakGuardError(f"row {i} field {field!r}: {error}")


def _write_non_transmitted_log(reason: str) -> None:
    """Append a local record of a blocked send. The log itself never transmits."""
    _BLOCKED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": datetime.now(tz=UTC).isoformat(),
        "event": "NON_TRANSMITTED — content-free guard blocked a send",
        "reason": reason,
    }
    with open(_BLOCKED_LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Consent notice
# ---------------------------------------------------------------------------

_CONSENT_SEP = "─" * 72

_NEVER_SENT_BLOCK = (
    "WHAT IS NEVER SENT:\n"
    "  session_id, source_path, prompts, code, error text, evidence snippets,\n"
    "  proof-turn content, judge_reasoning, interpretation, precise timestamps,\n"
    "  any free-text field.\n"
    "  In plain words: numbers and categories only — no text, no code, no\n"
    "  content from your sessions."
)


def build_corpus_consent_notice(sample_row: dict, contributor_id: str | None) -> str:
    """Build the opt-in consent screen shown after the P7 payload preview.

    Shows: the 14 fields, the WHAT IS NEVER SENT block, the destination named
    as a third party, the use, the contributor_id explanation, and the
    withdrawal warning as its own prominent, visually distinct line.
    """
    fields_block = "\n".join(f"  {f}" for f in sorted(ALLOWED_FIELDS))
    cid_line = (
        f"Your contributor_id: {contributor_id}\n"
        "  A random ID generated on this machine — not your name, email, or\n"
        "  hostname. It links your rows together so you can withdraw them later."
        if contributor_id
        else "Contributing anonymously — no contributor_id will be attached to your rows.\n"
        "  (Anonymous rows cannot be individually withdrawn later — see below.)"
    )
    return (
        f"{_CONSENT_SEP}\n"
        "CONTRIBUTE TO THE TRACEGAUGE COMMUNITY CORPUS (opt-in)\n"
        f"{_CONSENT_SEP}\n\n"
        "This is transmitted to and stored in a database hosted on Supabase\n"
        "(a third-party cloud provider), operated by the tracegauge project.\n\n"
        "FIELDS SENT (all content-free):\n"
        f"{fields_block}\n\n"
        f"{_NEVER_SENT_BLOCK}\n\n"
        "USE: pooled with other contributors' rows to compute cross-developer\n"
        'percentile baselines (e.g. "your context-resend efficiency for\n'
        'infra-deploy is in the 60th percentile across N developers"), which\n'
        "you can then see alongside (never replacing) your own local self-baseline.\n\n"
        f"{cid_line}\n\n"
        "YOUR ROW (real data, exactly what would be sent):\n"
        f"{json.dumps(sample_row, indent=2, default=str)}\n\n"
        f"{_CONSENT_SEP}\n"
        "WITHDRAWAL: run `tes corpus withdraw` at any time to permanently\n"
        "delete every row tied to your contributor_id. This requires the file\n"
        "~/.tes/contributor_id.txt — if you delete or lose that file, your\n"
        "past rows can no longer be linked to you and CANNOT be withdrawn.\n"
        f"{_CONSENT_SEP}"
    )


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ContributeResult:
    sent: bool
    row_count: int
    reason: str | None = None  # set when sent is False


@dataclass
class WithdrawResult:
    deleted: bool
    deleted_count: int | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# Public API: contribute (the send)
# ---------------------------------------------------------------------------


def contribute(
    conn: Any,
    *,
    consent_given: bool,
    contributor_id: str | None,
    config: CorpusConfig | None,
    include_source_components: bool = True,
    timeout_s: float = 15.0,
) -> ContributeResult:
    """Build the content-free payload and, only on explicit consent, POST it.

    consent_given MUST be True before any network call is attempted — checked
    FIRST, unconditionally, mirroring tes.judge.score_trajectory_api. When
    False, this function does not build a network request of any kind.
    """
    if not consent_given:
        return ContributeResult(sent=False, row_count=0, reason="consent not given")

    if config is None:
        return ContributeResult(sent=False, row_count=0, reason="corpus not configured")

    payload = build_contribution_payload(
        conn,
        contributor_id=contributor_id,
        include_source_components=include_source_components,
    )
    if payload.manifest.row_count == 0:
        return ContributeResult(sent=False, row_count=0, reason="no sessions to contribute")

    body = json.dumps(payload.rows).encode("utf-8")

    try:
        verify_payload_content_free(body)
    except ContentLeakGuardError as exc:
        _write_non_transmitted_log(str(exc))
        print(
            "Content-free verification failed — transmission aborted",
            file=sys.stderr,
        )
        return ContributeResult(sent=False, row_count=0, reason=str(exc))

    resp = httpx.post(
        f"{config.supabase_url}/rest/v1/{config.table_name}",
        content=body,
        headers={
            "apikey": config.supabase_anon_key,
            "Authorization": f"Bearer {config.supabase_anon_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        timeout=timeout_s,
    )
    resp.raise_for_status()
    return ContributeResult(sent=True, row_count=payload.manifest.row_count)


# ---------------------------------------------------------------------------
# Public API: withdraw
# ---------------------------------------------------------------------------


def withdraw(
    *,
    confirmed: bool,
    config: CorpusConfig | None,
    contributor_id_path: Path = _CONTRIBUTOR_ID_FILE,
    timeout_s: float = 15.0,
) -> WithdrawResult:
    """Delete every row tied to the local contributor_id, via the Edge
    Function (service-role deletion proxy — the anon role cannot DELETE
    directly; see corpus/schema.sql). Requires explicit confirmation.

    On confirmed success, deletes the local contributor_id.txt so a future
    contribution generates a fresh, unlinked ID rather than silently
    re-contributing under an ID the user just asked to be forgotten.
    """
    if not confirmed:
        return WithdrawResult(deleted=False, reason="not confirmed")

    if not contributor_id_path.exists():
        return WithdrawResult(
            deleted=False,
            reason=(
                "no contributor_id.txt found — prior rows (if any) can no longer "
                "be linked to you and cannot be withdrawn"
            ),
        )

    contributor_id = contributor_id_path.read_text(encoding="utf-8").strip()
    if not _UUID4_RE.match(contributor_id):
        return WithdrawResult(deleted=False, reason="local contributor_id.txt is malformed")

    if config is None:
        return WithdrawResult(deleted=False, reason="corpus not configured")

    resp = httpx.post(
        config.withdraw_function_url,
        json={"contributor_id": contributor_id},
        headers={
            "Authorization": f"Bearer {config.supabase_anon_key}",
            "Content-Type": "application/json",
        },
        timeout=timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()
    deleted_count = data.get("deleted_count", 0)

    contributor_id_path.unlink()
    print(f"Deleted local {contributor_id_path} — a future contribution will use a new ID.")

    return WithdrawResult(deleted=True, deleted_count=deleted_count)


def reset_contributor_id(contributor_id_path: Path = _CONTRIBUTOR_ID_FILE) -> str:
    """Generate a fresh random contributor_id, overwriting the local file.

    Prior rows under the old ID become unlinked (they still exist in the
    corpus but can no longer be withdrawn via `tes corpus withdraw`, since
    that command reads the current file). This is a LOCAL-ONLY operation —
    no network call.
    """
    new_id = str(uuid.uuid4())
    contributor_id_path.parent.mkdir(parents=True, exist_ok=True)
    contributor_id_path.write_text(new_id + "\n", encoding="utf-8")
    return new_id


__all__ = [
    "CorpusConfig",
    "ContentLeakGuardError",
    "ContributeResult",
    "WithdrawResult",
    "verify_payload_content_free",
    "build_corpus_consent_notice",
    "contribute",
    "withdraw",
    "reset_contributor_id",
]
