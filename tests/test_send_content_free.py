from __future__ import annotations

"""tests/test_send_content_free.py — Prove the ACTUAL POST payload is content-free.

Two things, both required:
1. HAPPY PATH: capture the real bytes passed to httpx.post() for a normal
   session (built the same way P7's test_contribution_content_free.py plants
   secrets) and byte-grep them for every planted secret — none may appear.
2. GUARD PATH: simulate a payload that (hypothetically, e.g. from a future
   bug) contains a planted secret in EVERY field type — a string field, the
   waste_detectors LIST, and a numeric-as-string attempt — and confirm
   verify_payload_content_free() catches EACH one, AND that contribute()
   never calls httpx.post when the guard fails (no network activity on a
   blocked send).
"""

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from tes.contribution import ALLOWED_FIELDS
from tes.corpus_client import (
    ContentLeakGuardError,
    CorpusConfig,
    contribute,
    verify_payload_content_free,
)
from tes.score import ThreeAxisResult
from tes.store import open_db, upsert_session

# Same planted-secret set as tests/test_contribution_content_free.py — the
# send-time guard must hold to the same standard as the P7 build-time guard.
PLANTED_SECRETS = [
    "sk-ant-api03-SUPERSECRETKEY123456",
    "gsk_GROQAPIKEY9876543210abcdef",
    "/home/gaurav/secret-project/session.jsonl",
    "my-identifying-project-name",
    "custom-self-hosted-model-v99",
    "EVIDENCE_SNIPPET_CONTENT_123",
    "proof_turn_content_DO_NOT_LEAK",
    "judge said this is TERRIBLE code",
    "interpretation_string_with_details",
]

_FAKE_CONFIG = CorpusConfig(
    supabase_url="https://fake-project.supabase.co",
    supabase_anon_key="fake-anon-key",
    withdraw_function_url="https://fake-project.supabase.co/functions/v1/withdraw-contributor",
)


def _make_conn_with_secrets(session_id: str = "send-leak-check") -> object:
    conn = open_db(":memory:")
    result = ThreeAxisResult(
        session_id=session_id,
        task_type="debug-fix",
        real_tokens=5000,
        scope_status="in_scope",
        baseline_available=True,
        p25=3000,
        p75=7000,
        median=5000,
        band_verdict="within_band",
        interpretation="interpretation_string_with_details",
        token_domain_of_validity="",
        baseline_source="b2_corpus",
        judge_verdict="WORSE",
        judge_score=2.0,
        judge_reasoning="judge said this is TERRIBLE code",
        trajectory_domain_of_validity="",
        waste_event_count=1,
        waste_events=[
            {
                "detector": "REPEATED-FAILED-RETRY",
                "session_id": session_id,
                "turns": [3, 5, 7],
                "repeat_count": 3,
                "evidence": {
                    "error_snippet": "EVIDENCE_SNIPPET_CONTENT_123",
                    "proof_turn_content": "proof_turn_content_DO_NOT_LEAK",
                },
            }
        ],
        waste_domain_of_validity="",
        session_cost_usd=0.05,
        cost_approximate=False,
        cost_domain_of_validity="",
    )
    mtime = datetime(2026, 6, 9, tzinfo=UTC).timestamp()
    upsert_session(
        conn,
        result,
        "/home/gaurav/secret-project/session.jsonl",
        mtime,
        "hash-send-test",
        turn_count=20,
    )
    return conn


# ---------------------------------------------------------------------------
# Happy path: the actual bytes handed to httpx.post are content-free
# ---------------------------------------------------------------------------


def test_actual_post_body_byte_grep_no_planted_secrets() -> None:
    """Capture the literal bytes passed to httpx.post(content=...) and
    byte-grep them for every planted secret. This is the ACTUAL wire payload
    (content=body is passed verbatim to httpx, no further transformation)."""
    conn = _make_conn_with_secrets()
    captured: dict = {}

    def fake_post(url, *, content, headers, timeout):
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        return resp

    with patch("tes.corpus_client.httpx.post", side_effect=fake_post) as mock_post:
        result = contribute(
            conn,
            consent_given=True,
            contributor_id="a1b2c3d4-1234-4abc-89ab-1234567890ab",
            config=_FAKE_CONFIG,
            include_source_components=False,
        )

    assert mock_post.called, "guard blocked a legitimately content-free payload"
    assert result.sent is True
    body_bytes = captured["content"]
    assert isinstance(body_bytes, bytes)

    for secret in PLANTED_SECRETS:
        assert secret.encode("utf-8") not in body_bytes, f"Leaked in ACTUAL POST body: {secret!r}"
    assert session_id_bytes_absent(body_bytes, "send-leak-check")


def session_id_bytes_absent(body_bytes: bytes, session_id: str) -> bool:
    return session_id.encode("utf-8") not in body_bytes


def test_actual_post_body_keys_are_exactly_allowed_fields() -> None:
    """The wire payload's row keys must be exactly ALLOWED_FIELDS — nothing more."""
    conn = _make_conn_with_secrets()
    captured: dict = {}

    def fake_post(url, *, content, headers, timeout):
        captured["content"] = content
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        return resp

    with patch("tes.corpus_client.httpx.post", side_effect=fake_post):
        contribute(
            conn,
            consent_given=True,
            contributor_id=None,
            config=_FAKE_CONFIG,
            include_source_components=False,
        )

    rows = json.loads(captured["content"])
    assert len(rows) == 1
    assert set(rows[0].keys()) == ALLOWED_FIELDS


# ---------------------------------------------------------------------------
# Guard path: verify_payload_content_free() catches a planted secret in
# EVERY field type
# ---------------------------------------------------------------------------


def _legit_row() -> dict:
    return {
        "task_type": "debug-fix",
        "real_tokens": 5000,
        "token_count_input": 1000,
        "token_count_output": 500,
        "cache_creation": 0,
        "cache_read": 200,
        "waste_event_count": 1,
        "waste_detectors_fired": ["REPEATED-FAILED-RETRY"],
        "model": "claude-opus-4-8",
        "turn_count": 10,
        "week_bucket": "2026-W24",
        "tracegauge_version": "0.9.0",
        "schema_version": "1",
        "contributor_id": "a1b2c3d4-1234-4abc-89ab-1234567890ab",
    }


def test_verify_passes_a_legitimate_row() -> None:
    body = json.dumps([_legit_row()]).encode("utf-8")
    verify_payload_content_free(body)  # must not raise


def test_guard_catches_secret_in_string_field() -> None:
    """Secret planted directly in a categorical string field (task_type)."""
    row = _legit_row()
    row["task_type"] = "sk-ant-api03-SUPERSECRETKEY123456"
    body = json.dumps([row]).encode("utf-8")
    with pytest.raises(ContentLeakGuardError):
        verify_payload_content_free(body)


def test_guard_catches_secret_in_model_field() -> None:
    row = _legit_row()
    row["model"] = "gsk_GROQAPIKEY9876543210abcdef"
    body = json.dumps([row]).encode("utf-8")
    with pytest.raises(ContentLeakGuardError):
        verify_payload_content_free(body)


def test_guard_catches_secret_in_waste_detectors_list() -> None:
    """Secret planted inside the waste_detectors_fired LIST — not a top-level
    string value, so this exercises the list-item check specifically."""
    row = _legit_row()
    row["waste_detectors_fired"] = ["EVIDENCE_SNIPPET_CONTENT_123"]
    body = json.dumps([row]).encode("utf-8")
    with pytest.raises(ContentLeakGuardError):
        verify_payload_content_free(body)


def test_guard_catches_numeric_as_string_attempt() -> None:
    """A numeric field (real_tokens) replaced with a string — must be caught
    by type, since a string here could carry arbitrary smuggled content."""
    row = _legit_row()
    row["real_tokens"] = "sk-ant-api03-SUPERSECRETKEY123456"
    body = json.dumps([row]).encode("utf-8")
    with pytest.raises(ContentLeakGuardError):
        verify_payload_content_free(body)


def test_guard_catches_numeric_as_string_in_every_numeric_field() -> None:
    """Omnibus: every numeric field individually rejects a string value."""
    numeric_fields = [
        "real_tokens",
        "token_count_input",
        "token_count_output",
        "cache_creation",
        "cache_read",
        "waste_event_count",
        "turn_count",
    ]
    for field in numeric_fields:
        row = _legit_row()
        row[field] = "smuggled-content-string"
        body = json.dumps([row]).encode("utf-8")
        with pytest.raises(ContentLeakGuardError):
            verify_payload_content_free(body)


def test_guard_catches_contributor_id_not_uuid4() -> None:
    row = _legit_row()
    row["contributor_id"] = "not-a-real-uuid-just-some-string"
    body = json.dumps([row]).encode("utf-8")
    with pytest.raises(ContentLeakGuardError):
        verify_payload_content_free(body)


def test_guard_catches_week_bucket_bad_format() -> None:
    row = _legit_row()
    row["week_bucket"] = "not-a-week-bucket-string-at-all"
    body = json.dumps([row]).encode("utf-8")
    with pytest.raises(ContentLeakGuardError):
        verify_payload_content_free(body)


def test_guard_catches_extra_key_smuggled_in() -> None:
    """Pass-1 key-space check: an extra key (e.g. source_path) must abort,
    even if every OTHER key is valid."""
    row = _legit_row()
    row["source_path"] = "/home/gaurav/secret-project/session.jsonl"
    body = json.dumps([row]).encode("utf-8")
    with pytest.raises(ContentLeakGuardError):
        verify_payload_content_free(body)


def test_guard_catches_missing_key() -> None:
    row = _legit_row()
    del row["schema_version"]
    body = json.dumps([row]).encode("utf-8")
    with pytest.raises(ContentLeakGuardError):
        verify_payload_content_free(body)


def test_guard_rejects_non_array_body() -> None:
    body = json.dumps({"rows": [_legit_row()]}).encode("utf-8")
    with pytest.raises(ContentLeakGuardError):
        verify_payload_content_free(body)


# ---------------------------------------------------------------------------
# Guard path, end-to-end through contribute(): httpx.post is NEVER called
# ---------------------------------------------------------------------------


def _conn_with_one_session() -> object:
    conn = open_db(":memory:")
    result = ThreeAxisResult(
        session_id="guard-e2e-test",
        task_type="debug-fix",
        real_tokens=1000,
        scope_status="in_scope",
        baseline_available=True,
        p25=500,
        p75=1500,
        median=1000,
        band_verdict="within_band",
        interpretation="",
        token_domain_of_validity="",
        baseline_source="b2_corpus",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning=None,
        trajectory_domain_of_validity="",
        waste_event_count=0,
        waste_events=[],
        waste_domain_of_validity="",
        session_cost_usd=0.01,
        cost_approximate=False,
        cost_domain_of_validity="",
    )
    mtime = datetime(2026, 6, 9, tzinfo=UTC).timestamp()
    upsert_session(conn, result, "/nonexistent/path.jsonl", mtime, "hash-guard-e2e", turn_count=5)
    return conn


def test_contribute_never_calls_httpx_post_when_guard_blocks_poisoned_payload() -> None:
    """Simulate a future bug that makes build_contribution_payload return a
    poisoned row. contribute() must catch it at send time and MUST NOT call
    httpx.post — the network call is the point of no return, and it never
    happens for a payload that fails the guard."""
    conn = _conn_with_one_session()
    poisoned_payload = MagicMock()
    poisoned_payload.manifest.row_count = 1
    poisoned_payload.rows = [{**_legit_row(), "task_type": "sk-ant-api03-SUPERSECRETKEY123456"}]

    with patch("tes.corpus_client.build_contribution_payload", return_value=poisoned_payload):
        with patch("tes.corpus_client.httpx.post") as mock_post:
            result = contribute(
                conn,
                consent_given=True,
                contributor_id="safe-uuid",
                config=_FAKE_CONFIG,
            )

    mock_post.assert_not_called()
    assert result.sent is False
    assert result.reason is not None


def test_contribute_writes_non_transmitted_log_on_guard_failure(tmp_path, monkeypatch) -> None:
    """A blocked send writes a LOCAL log entry explaining what tripped the
    guard — the log itself never transmits."""
    import tes.corpus_client as cc_module

    log_path = tmp_path / "contribution_blocked.log"
    monkeypatch.setattr(cc_module, "_BLOCKED_LOG_PATH", log_path)

    conn = _conn_with_one_session()
    poisoned_payload = MagicMock()
    poisoned_payload.manifest.row_count = 1
    poisoned_payload.rows = [{**_legit_row(), "model": "EVIDENCE_SNIPPET_CONTENT_123"}]

    with patch("tes.corpus_client.build_contribution_payload", return_value=poisoned_payload):
        with patch("tes.corpus_client.httpx.post") as mock_post:
            contribute(
                conn,
                consent_given=True,
                contributor_id="safe-uuid",
                config=_FAKE_CONFIG,
            )

    mock_post.assert_not_called()
    assert log_path.exists()
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert "NON_TRANSMITTED" in entry["event"]
    # By design the reason names the offending field AND value (useful for the
    # user to see what almost got sent) — this is safe ONLY because the log is
    # local-only and never transmitted. Assert that diagnostic content, and
    # separately assert the log file itself is never touched by anything that
    # sends network traffic (mock_post.assert_not_called() above).
    assert "model" in entry["reason"]
    assert log_path.parent == tmp_path
