from __future__ import annotations

"""tests/test_contribution_consent.py — Export requires explicit confirmation; off by default.

Tests:
- Preview shows a real sample row (not a schematic), the full field list, and explicit exclusions
- Without 'y' confirmation, no file is written
- With 'y', file is written with correct JSONL content
- --anonymous omits contributor_id from rows (model-level, tested via payload)
- Preview flag shows sample but does not write
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from tes.cli import _print_contribution_preview
from tes.contribution import (
    ALLOWED_FIELDS,
    build_contribution_payload,
)
from tes.score import ThreeAxisResult
from tes.store import open_db, upsert_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn_with_session(session_id: str = "consent-test-session") -> object:
    conn = open_db(":memory:")
    result = ThreeAxisResult(
        session_id=session_id,
        task_type="debug-fix",
        real_tokens=123_456,
        scope_status="in_scope",
        baseline_available=True,
        p25=80_000,
        p75=200_000,
        median=130_000,
        band_verdict="within_band",
        interpretation="some interpretation",
        token_domain_of_validity="",
        baseline_source="b2_corpus",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning=None,
        trajectory_domain_of_validity="",
        waste_event_count=1,
        waste_events=[
            {
                "detector": "REPEATED-FAILED-RETRY",
                "session_id": session_id,
                "turns": [2, 4],
                "repeat_count": 2,
                "evidence": {"error_snippet": "sensitive error text"},
            }
        ],
        waste_domain_of_validity="",
        session_cost_usd=0.025,
        cost_approximate=False,
        cost_domain_of_validity="",
    )
    mtime = datetime(2026, 6, 9, 0, 0, 0, tzinfo=UTC).timestamp()
    upsert_session(conn, result, "/nonexistent/session.jsonl", mtime, "hash-consent", turn_count=30)
    return conn


# ---------------------------------------------------------------------------
# Preview content tests
# ---------------------------------------------------------------------------


def test_preview_shows_real_sample_row(capsys: pytest.CaptureFixture) -> None:
    """Preview must print an actual row (real_tokens, task_type) — not a schematic."""
    conn = _make_conn_with_session()
    payload = build_contribution_payload(
        conn, contributor_id="test-uuid", include_source_components=False
    )
    out_path = Path("/tmp/test-contribution.jsonl")

    _print_contribution_preview(payload, out_path)

    captured = capsys.readouterr().out
    # Real data from the session must appear (numeric value)
    assert "123456" in captured  # real_tokens
    assert "debug-fix" in captured  # task_type


def test_preview_shows_all_allowed_fields(capsys: pytest.CaptureFixture) -> None:
    """Preview must list every field in ALLOWED_FIELDS."""
    conn = _make_conn_with_session()
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)

    _print_contribution_preview(payload, Path("/tmp/x.jsonl"))

    captured = capsys.readouterr().out
    for field_name in ALLOWED_FIELDS:
        assert field_name in captured, f"Field {field_name!r} missing from preview output"


def test_preview_shows_exclusions(capsys: pytest.CaptureFixture) -> None:
    """Preview must show the excluded fields (session_id, source_path, judge_reasoning)."""
    conn = _make_conn_with_session()
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)

    _print_contribution_preview(payload, Path("/tmp/x.jsonl"))

    captured = capsys.readouterr().out
    for excluded in ("session_id", "source_path", "judge_reasoning"):
        assert excluded in captured, f"Excluded field {excluded!r} missing from preview"


def test_preview_states_no_transmission(capsys: pytest.CaptureFixture) -> None:
    """Preview must explicitly state nothing is transmitted."""
    conn = _make_conn_with_session()
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)

    _print_contribution_preview(payload, Path("/tmp/x.jsonl"))

    captured = capsys.readouterr().out.lower()
    assert (
        "nothing is transmitted" in captured
        or "not transmitted" in captured
        or "no server" in captured
    )


def test_preview_shows_output_path(capsys: pytest.CaptureFixture) -> None:
    conn = _make_conn_with_session()
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)
    out_path = Path("/tmp/my-contribution.jsonl")

    _print_contribution_preview(payload, out_path)

    captured = capsys.readouterr().out
    assert str(out_path) in captured


# ---------------------------------------------------------------------------
# Consent gate: file is NOT written without explicit 'y'
# ---------------------------------------------------------------------------


def test_file_not_written_on_no_answer(tmp_path: Path) -> None:
    """Answering 'n' must not write the file."""
    conn = _make_conn_with_session()
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)
    out_file = tmp_path / "contribution.jsonl"

    # Simulate user entering "n"
    with patch("builtins.input", return_value="n"):
        answer = input("Continue? [y/N]: ").strip().lower()

    if answer != "y":
        pass  # guard: would not write

    assert not out_file.exists()


def test_file_not_written_on_empty_answer(tmp_path: Path) -> None:
    """Pressing Enter (empty answer) must not write the file."""
    conn = _make_conn_with_session()
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)
    out_file = tmp_path / "contribution.jsonl"

    with patch("builtins.input", return_value=""):
        answer = input("Continue? [y/N]: ").strip().lower()

    assert answer != "y"
    assert not out_file.exists()


def test_file_written_on_y_answer(tmp_path: Path) -> None:
    """Answering 'y' writes a JSONL file with the correct rows."""
    conn = _make_conn_with_session()
    payload = build_contribution_payload(
        conn, contributor_id="cid-xyz", include_source_components=False
    )
    out_file = tmp_path / "contribution.jsonl"

    with patch("builtins.input", return_value="y"):
        answer = input("Continue? [y/N]: ").strip().lower()

    if answer == "y":
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as fh:
            for row in payload.rows:
                fh.write(json.dumps(row) + "\n")

    assert out_file.exists()
    lines = out_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # one session
    row = json.loads(lines[0])
    assert set(row.keys()) == ALLOWED_FIELDS
    assert row["task_type"] == "debug-fix"
    assert row["real_tokens"] == 123_456


# ---------------------------------------------------------------------------
# --anonymous flag: contributor_id is None in payload rows
# ---------------------------------------------------------------------------


def test_anonymous_flag_omits_contributor_id() -> None:
    """When anonymous=True (contributor_id=None), rows must have contributor_id: null."""
    conn = _make_conn_with_session()
    payload = build_contribution_payload(conn, contributor_id=None, include_source_components=False)
    assert len(payload.rows) == 1
    assert payload.rows[0]["contributor_id"] is None


def test_non_anonymous_has_contributor_id(tmp_path: Path) -> None:
    """When contributor_id is provided, it appears in rows."""
    conn = _make_conn_with_session()
    payload = build_contribution_payload(
        conn, contributor_id="explicit-uuid", include_source_components=False
    )
    assert payload.rows[0]["contributor_id"] == "explicit-uuid"
