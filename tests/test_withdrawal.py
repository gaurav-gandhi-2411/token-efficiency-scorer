from __future__ import annotations

"""tests/test_withdrawal.py — Withdrawal deletes the contributor's rows and
the local contributor_id.txt; refuses without confirmation; honest when the
ID file is missing.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from tes.corpus_client import CorpusConfig, reset_contributor_id, withdraw

_FAKE_CONFIG = CorpusConfig(
    supabase_url="https://fake-project.supabase.co",
    supabase_anon_key="fake-anon-key",
    withdraw_function_url="https://fake-project.supabase.co/functions/v1/withdraw-contributor",
)

_VALID_UUID = "a1b2c3d4-1234-4abc-89ab-1234567890ab"


def _write_id_file(tmp_path: Path, contents: str = _VALID_UUID) -> Path:
    p = tmp_path / "contributor_id.txt"
    p.write_text(contents + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Confirmation is the unconditional gate
# ---------------------------------------------------------------------------


def test_no_network_call_without_confirmation(tmp_path: Path) -> None:
    id_path = _write_id_file(tmp_path)
    with patch("tes.corpus_client.httpx.post") as mock_post:
        result = withdraw(confirmed=False, config=_FAKE_CONFIG, contributor_id_path=id_path)
    mock_post.assert_not_called()
    assert result.deleted is False
    assert result.reason == "not confirmed"
    assert id_path.exists()  # nothing touched


def test_no_network_call_without_confirmation_even_if_configured_and_file_present(
    tmp_path: Path,
) -> None:
    """Redundant-but-explicit: confirmation is checked FIRST, before the file
    is even read — mirrors contribute()'s consent-first ordering."""
    id_path = _write_id_file(tmp_path)
    with patch("tes.corpus_client.httpx.post") as mock_post:
        withdraw(confirmed=False, config=_FAKE_CONFIG, contributor_id_path=id_path)
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# Successful withdrawal: deletes remote rows AND the local ID file
# ---------------------------------------------------------------------------


def test_withdraw_success_deletes_local_id_file(tmp_path: Path) -> None:
    id_path = _write_id_file(tmp_path)

    fake_resp = MagicMock()
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = {"deleted_count": 7}

    with patch("tes.corpus_client.httpx.post", return_value=fake_resp) as mock_post:
        result = withdraw(confirmed=True, config=_FAKE_CONFIG, contributor_id_path=id_path)

    assert mock_post.called
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"] == {"contributor_id": _VALID_UUID}
    assert result.deleted is True
    assert result.deleted_count == 7
    assert not id_path.exists()  # local file removed after confirmed deletion


def test_withdraw_calls_the_edge_function_url_not_the_rest_table(tmp_path: Path) -> None:
    """Withdrawal must go through the Edge Function (service-role deletion
    proxy), never a direct REST call to the table — the anon role has no
    delete policy (see corpus/schema.sql) and would get an RLS-denied error
    if it tried."""
    id_path = _write_id_file(tmp_path)
    fake_resp = MagicMock()
    fake_resp.raise_for_status.return_value = None
    fake_resp.json.return_value = {"deleted_count": 1}

    with patch("tes.corpus_client.httpx.post", return_value=fake_resp) as mock_post:
        withdraw(confirmed=True, config=_FAKE_CONFIG, contributor_id_path=id_path)

    called_url = mock_post.call_args.args[0]
    assert called_url == _FAKE_CONFIG.withdraw_function_url
    assert "/rest/v1/" not in called_url


# ---------------------------------------------------------------------------
# Honest handling when the ID file is missing or malformed
# ---------------------------------------------------------------------------


def test_withdraw_missing_id_file_gives_honest_message(tmp_path: Path) -> None:
    missing_path = tmp_path / "does-not-exist.txt"
    with patch("tes.corpus_client.httpx.post") as mock_post:
        result = withdraw(confirmed=True, config=_FAKE_CONFIG, contributor_id_path=missing_path)
    mock_post.assert_not_called()
    assert result.deleted is False
    assert "cannot" in result.reason.lower() or "no contributor_id" in result.reason.lower()


def test_withdraw_malformed_id_file_refuses(tmp_path: Path) -> None:
    id_path = _write_id_file(tmp_path, contents="not-a-uuid-at-all")
    with patch("tes.corpus_client.httpx.post") as mock_post:
        result = withdraw(confirmed=True, config=_FAKE_CONFIG, contributor_id_path=id_path)
    mock_post.assert_not_called()
    assert result.deleted is False
    assert id_path.exists()  # malformed file left alone, not silently deleted


def test_withdraw_unconfigured_corpus_makes_no_network_call(tmp_path: Path) -> None:
    id_path = _write_id_file(tmp_path)
    with patch("tes.corpus_client.httpx.post") as mock_post:
        result = withdraw(confirmed=True, config=None, contributor_id_path=id_path)
    mock_post.assert_not_called()
    assert result.deleted is False
    assert id_path.exists()


# ---------------------------------------------------------------------------
# reset-id: local-only, no network
# ---------------------------------------------------------------------------


def test_reset_contributor_id_generates_new_uuid_locally(tmp_path: Path) -> None:
    id_path = tmp_path / "contributor_id.txt"
    id_path.write_text(_VALID_UUID + "\n", encoding="utf-8")

    with (
        patch("tes.corpus_client.httpx.post") as mock_post,
        patch("tes.corpus_client.httpx.get") as mock_get,
    ):
        new_id = reset_contributor_id(contributor_id_path=id_path)

    mock_post.assert_not_called()
    mock_get.assert_not_called()
    assert new_id != _VALID_UUID
    assert id_path.read_text(encoding="utf-8").strip() == new_id


def test_reset_contributor_id_creates_parent_dir(tmp_path: Path) -> None:
    id_path = tmp_path / "nested" / "contributor_id.txt"
    new_id = reset_contributor_id(contributor_id_path=id_path)
    assert id_path.exists()
    assert id_path.read_text(encoding="utf-8").strip() == new_id
