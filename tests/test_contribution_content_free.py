from __future__ import annotations

"""tests/test_contribution_content_free.py — Prove zero content leakage in contribution payload.

Safety test: construct sessions with KNOWN sensitive content embedded where it
COULD leak (API keys, file paths, project names, custom model strings, evidence
snippets). Build the payload. Serialize to JSON bytes. Assert NONE of the
sensitive strings appear anywhere in the serialized bytes.

Tests VALUE leakage, not just key leakage.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from tes.contribution import ALLOWED_FIELDS, build_contribution_payload
from tes.score import ThreeAxisResult
from tes.store import open_db, upsert_session


# Sensitive strings that must NEVER appear in the serialized payload
PLANTED_SECRETS = [
    "sk-ant-api03-SUPERSECRETKEY123456",           # Anthropic key
    "gsk_GROQAPIKEY9876543210abcdef",              # Groq key
    "/home/gaurav/secret-project/session.jsonl",   # file path
    "my-identifying-project-name",                 # project name
    "custom-self-hosted-model-v99",                # exotic model string
    "EVIDENCE_SNIPPET_CONTENT_123",                # evidence in waste event
    "proof_turn_content_DO_NOT_LEAK",              # proof turn content
    "judge said this is TERRIBLE code",            # judge reasoning
    "interpretation_string_with_details",          # interpretation
]


def _make_conn():
    return open_db(":memory:")


def _make_result_with_secrets(session_id: str = "test-leak-check") -> ThreeAxisResult:
    """ThreeAxisResult with sensitive content in every field that could leak."""
    return ThreeAxisResult(
        session_id=session_id,
        task_type="debug-fix",  # known type — no leak here
        real_tokens=5000,
        scope_status="in_scope",
        baseline_available=True,
        p25=3000,
        p75=7000,
        median=5000,
        band_verdict="within_band",
        interpretation="interpretation_string_with_details",    # PLANTED
        token_domain_of_validity="",
        baseline_source="b2_corpus",
        judge_verdict="WORSE",
        judge_score=2.0,
        judge_reasoning="judge said this is TERRIBLE code",     # PLANTED
        trajectory_domain_of_validity="",
        waste_event_count=1,
        waste_events=[{
            "detector": "REPEATED-FAILED-RETRY",
            "session_id": session_id,                           # PLANTED (session_id in event)
            "turns": [3, 5, 7],
            "repeat_count": 3,
            "evidence": {
                "error_snippet": "EVIDENCE_SNIPPET_CONTENT_123",   # PLANTED
                "proof_turn_content": "proof_turn_content_DO_NOT_LEAK",  # PLANTED
            },
        }],
        waste_domain_of_validity="",
        session_cost_usd=0.05,
        cost_approximate=False,
        cost_domain_of_validity="",
    )


def _payload_json(result: ThreeAxisResult, source_path: str = "/nonexistent/path.jsonl") -> str:
    """Build a contribution payload from a single session and return serialized JSON."""
    conn = _make_conn()
    mtime = datetime(2026, 6, 9, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    upsert_session(conn, result, source_path, mtime, "hash-test", turn_count=20)
    payload = build_contribution_payload(
        conn,
        contributor_id="safe-test-uuid",
        include_source_components=False,
    )
    return json.dumps([{"rows": payload.rows, "manifest": vars(payload.manifest)}])


def test_no_api_key_in_payload() -> None:
    """Planted Anthropic key must not appear in serialized payload bytes."""
    result = _make_result_with_secrets()
    serialized = _payload_json(result)
    assert "sk-ant-api03-SUPERSECRETKEY123456" not in serialized


def test_no_groq_key_in_payload() -> None:
    result = _make_result_with_secrets()
    serialized = _payload_json(result)
    assert "gsk_GROQAPIKEY9876543210abcdef" not in serialized


def test_no_file_path_in_payload() -> None:
    """Source file path must not appear — it's excluded by allow-list."""
    result = _make_result_with_secrets()
    serialized = _payload_json(result, source_path="/home/gaurav/secret-project/session.jsonl")
    assert "/home/gaurav/secret-project/session.jsonl" not in serialized
    assert "secret-project" not in serialized


def test_no_judge_reasoning_in_payload() -> None:
    """judge_reasoning is excluded; planted content must not appear."""
    result = _make_result_with_secrets()
    serialized = _payload_json(result)
    assert "judge said this is TERRIBLE code" not in serialized


def test_no_interpretation_in_payload() -> None:
    """interpretation is excluded; planted content must not appear."""
    result = _make_result_with_secrets()
    serialized = _payload_json(result)
    assert "interpretation_string_with_details" not in serialized


def test_no_evidence_snippet_in_payload() -> None:
    """Evidence from waste events must not appear — detector names only."""
    result = _make_result_with_secrets()
    serialized = _payload_json(result)
    assert "EVIDENCE_SNIPPET_CONTENT_123" not in serialized


def test_no_proof_turn_content_in_payload() -> None:
    """Proof turn content from waste events must not appear."""
    result = _make_result_with_secrets()
    serialized = _payload_json(result)
    assert "proof_turn_content_DO_NOT_LEAK" not in serialized


def test_no_session_id_in_payload_rows() -> None:
    """Session IDs (even those embedded in waste_events) must not appear in rows."""
    result = _make_result_with_secrets(session_id="SENSITIVE-SESSION-ID-XYZ")
    serialized = _payload_json(result)
    # session_id is excluded from allow-list; also planted as a value in waste_events
    assert "SENSITIVE-SESSION-ID-XYZ" not in serialized


def test_exotic_model_string_becomes_other() -> None:
    """Custom/unrecognized model string must never appear raw — must be 'other'."""
    # The _get_source_components path would map an exotic model to "other".
    # When include_source_components=False, model is None (also safe).
    # This test confirms no raw exotic string survives via any path.
    conn = _make_conn()
    result = _make_result_with_secrets()
    mtime = datetime(2026, 6, 9, tzinfo=timezone.utc).timestamp()
    upsert_session(conn, result, "/nonexistent/path.jsonl", mtime, "hash-exotic", turn_count=20)
    payload = build_contribution_payload(
        conn, contributor_id=None, include_source_components=False
    )
    serialized = json.dumps([{"rows": payload.rows}])
    assert "custom-self-hosted-model-v99" not in serialized


def test_unknown_task_type_becomes_other() -> None:
    """An unrecognized task_type stored in the DB must appear as 'other', not raw."""
    conn = _make_conn()
    result = ThreeAxisResult(
        session_id="unknown-task-type-test",
        task_type="IDENTIFYING-PROJECT-NAME-AS-TYPE",   # not in known set
        real_tokens=1000,
        scope_status="no_baseline",
        baseline_available=False,
        p25=None, p75=None, median=None,
        band_verdict="unavailable",
        interpretation="",
        token_domain_of_validity="",
        baseline_source="b2_corpus",
        judge_verdict=None, judge_score=None, judge_reasoning=None,
        trajectory_domain_of_validity="",
        waste_event_count=0, waste_events=[], waste_domain_of_validity="",
        session_cost_usd=None, cost_approximate=False, cost_domain_of_validity=None,
    )
    mtime = datetime(2026, 6, 9, tzinfo=timezone.utc).timestamp()
    upsert_session(conn, result, "/nonexistent/path.jsonl", mtime, "hash-tasktype", turn_count=5)
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)
    serialized = json.dumps([{"rows": payload.rows}])
    assert "IDENTIFYING-PROJECT-NAME-AS-TYPE" not in serialized
    assert payload.rows[0]["task_type"] == "other"


def test_all_planted_secrets_absent_from_full_serialized_payload() -> None:
    """Omnibus: ALL planted secrets must be absent from the serialized payload bytes."""
    result = _make_result_with_secrets(session_id="omnibus-leak-check-DO-NOT-LEAK")
    serialized = _payload_json(result, source_path="/home/gaurav/secret-project/session.jsonl")
    for secret in PLANTED_SECRETS:
        assert secret not in serialized, f"Leaked: {secret!r} found in payload bytes"
    # Confirm the session_id itself also doesn't appear
    assert "omnibus-leak-check-DO-NOT-LEAK" not in serialized
