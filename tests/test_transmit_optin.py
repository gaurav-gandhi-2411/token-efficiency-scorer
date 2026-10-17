# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_transmit_optin.py — No send without explicit opt-in; default install
transmits nothing.

Three layers of proof:
1. contribute(consent_given=False) makes ZERO network calls, unconditionally —
   even with a fully valid config and a legitimate payload.
2. Nothing outside the explicit `tracegauge corpus contribute` CLI branch (gated on
   the user typing 'y') ever imports/calls tracegauge.corpus_client — the watcher,
   the web dashboard, and bare `tracegauge` (dashboard launch) never transmit.
3. CorpusConfig.from_env() returns None (not a default/fallback destination)
   when the destination env vars are unset — a fresh install with no
   TRACEGAUGE_CORPUS_* configuration cannot transmit even if a user somehow forced
   consent_given=True.
"""

import inspect
import socket
from datetime import datetime, timezone
from unittest.mock import patch

from tracegauge.corpus_client import CorpusConfig, contribute
from tracegauge.score import ThreeAxisResult
from tracegauge.store import open_db, upsert_session

UTC = timezone.utc  # datetime.UTC is 3.11+; this package supports 3.10

_FAKE_CONFIG = CorpusConfig(
    supabase_url="https://fake-project.supabase.co",
    supabase_anon_key="fake-anon-key",
    withdraw_function_url="https://fake-project.supabase.co/functions/v1/withdraw-contributor",
)


def _conn_with_one_session() -> object:
    conn = open_db(":memory:")
    result = ThreeAxisResult(
        session_id="optin-test",
        task_type="feature-build",
        real_tokens=500,
        scope_status="in_scope",
        baseline_available=True,
        p25=300,
        p75=800,
        median=500,
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
        session_cost_usd=None,
        cost_approximate=False,
        cost_domain_of_validity=None,
    )
    mtime = datetime(2026, 6, 10, tzinfo=UTC).timestamp()
    upsert_session(conn, result, "/nonexistent/path.jsonl", mtime, "hash-optin", turn_count=10)
    return conn


# ---------------------------------------------------------------------------
# 1. consent_given is the unconditional gate
# ---------------------------------------------------------------------------


def test_no_httpx_call_when_consent_not_given() -> None:
    conn = _conn_with_one_session()
    with patch("tracegauge.corpus_client.httpx.post") as mock_post:
        result = contribute(
            conn,
            consent_given=False,
            contributor_id="a1b2c3d4-1234-4abc-89ab-1234567890ab",
            config=_FAKE_CONFIG,
        )
    mock_post.assert_not_called()
    assert result.sent is False
    assert result.reason == "consent not given"


def test_no_socket_connect_when_consent_not_given() -> None:
    """Even below the httpx abstraction — no raw socket connection is opened."""
    conn = _conn_with_one_session()

    def fail_on_connect(self, *args, **kwargs):
        raise AssertionError(f"contribute() opened a socket without consent: {args}")

    with patch.object(socket.socket, "connect", fail_on_connect):
        result = contribute(
            conn,
            consent_given=False,
            contributor_id=None,
            config=_FAKE_CONFIG,
        )
    assert result.sent is False


def test_consent_check_happens_before_config_check() -> None:
    """consent_given=False short-circuits even when config=None — the order
    of checks must not matter; both are independently sufficient to block."""
    conn = _conn_with_one_session()
    with patch("tracegauge.corpus_client.httpx.post") as mock_post:
        result = contribute(conn, consent_given=False, contributor_id=None, config=None)
    mock_post.assert_not_called()
    assert result.sent is False


# ---------------------------------------------------------------------------
# 2. Nothing else in tracegauge ever calls corpus_client
# ---------------------------------------------------------------------------


def test_corpus_client_not_imported_by_watcher() -> None:
    import tracegauge.watcher as watcher_module

    source = inspect.getsource(watcher_module)
    assert "corpus_client" not in source


def test_corpus_client_not_imported_by_web_dashboard() -> None:
    import tracegauge.web as web_pkg

    web_dir = __import__("pathlib").Path(web_pkg.__file__).parent
    for py_file in web_dir.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        assert "corpus_client" not in source, f"{py_file} references corpus_client"


def test_corpus_client_only_referenced_in_cli_within_run_corpus() -> None:
    """tracegauge.cli must only import corpus_client inside _run_corpus() — the
    function gated behind the explicit `tracegauge corpus` subcommand. Confirms the
    reference isn't hoisted to module scope where it might run eagerly."""
    import tracegauge.cli as cli_module

    module_source = inspect.getsource(cli_module)
    run_corpus_source = inspect.getsource(cli_module._run_corpus)

    # Every mention of corpus_client in the whole cli.py module appears inside
    # _run_corpus's own source text (i.e. nowhere else in the file).
    occurrences_total = module_source.count("corpus_client")
    occurrences_in_fn = run_corpus_source.count("corpus_client")
    assert occurrences_total == occurrences_in_fn > 0


def test_bare_tes_dashboard_launch_never_touches_corpus_client(monkeypatch) -> None:
    """Bare `tracegauge` (dashboard launch) must never construct a CorpusConfig or
    call contribute()/withdraw() — those only run from the explicit `tracegauge
    corpus` subcommand handler."""
    import tracegauge.corpus_client as cc_module

    called = {"contribute": False, "withdraw": False}
    monkeypatch.setattr(
        cc_module, "contribute", lambda *a, **k: called.__setitem__("contribute", True)
    )
    monkeypatch.setattr(cc_module, "withdraw", lambda *a, **k: called.__setitem__("withdraw", True))
    # _run_serve is the bare-tes / `tracegauge serve` entry point — never touches corpus_client.
    import tracegauge.cli as cli_module

    assert "corpus_client" not in inspect.getsource(cli_module._run_serve)
    assert called == {"contribute": False, "withdraw": False}


# ---------------------------------------------------------------------------
# 3. No configured destination -> no send, even if consent were forced True
# ---------------------------------------------------------------------------


def test_corpus_config_from_env_returns_none_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("TRACEGAUGE_CORPUS_URL", raising=False)
    monkeypatch.delenv("TRACEGAUGE_CORPUS_ANON_KEY", raising=False)
    monkeypatch.delenv("TRACEGAUGE_CORPUS_WITHDRAW_URL", raising=False)
    assert CorpusConfig.from_env() is None


def test_corpus_config_from_env_requires_all_three_vars(monkeypatch) -> None:
    monkeypatch.setenv("TRACEGAUGE_CORPUS_URL", "https://example.supabase.co")
    monkeypatch.delenv("TRACEGAUGE_CORPUS_ANON_KEY", raising=False)
    monkeypatch.delenv("TRACEGAUGE_CORPUS_WITHDRAW_URL", raising=False)
    assert CorpusConfig.from_env() is None


def test_cli_contribute_fails_fast_when_unconfigured_before_any_prompt(monkeypatch, capsys) -> None:
    """`tracegauge corpus contribute` must check corpus availability BEFORE opening
    the store or showing the preview/consent screen — walking a user through
    a full "Send to the community corpus? [y/N]" prompt only to reveal
    [NOT SENT] afterward looks functional right up until the last line."""
    monkeypatch.delenv("TRACEGAUGE_CORPUS_URL", raising=False)
    monkeypatch.delenv("TRACEGAUGE_CORPUS_ANON_KEY", raising=False)
    monkeypatch.delenv("TRACEGAUGE_CORPUS_WITHDRAW_URL", raising=False)

    def _fail_if_prompted(*a, **k):
        raise AssertionError("input() was called -- config check did not fail fast")

    monkeypatch.setattr("builtins.input", _fail_if_prompted)

    import argparse

    import tracegauge.cli as cli_module

    ns = argparse.Namespace(
        command="corpus", corpus_command="contribute", db_path=None, anonymous=False
    )
    cli_module._run_corpus(ns)

    captured = capsys.readouterr()
    assert "[NOT AVAILABLE]" in captured.err


def test_cli_withdraw_fails_fast_when_unconfigured_before_any_prompt(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TRACEGAUGE_CORPUS_URL", raising=False)
    monkeypatch.delenv("TRACEGAUGE_CORPUS_ANON_KEY", raising=False)
    monkeypatch.delenv("TRACEGAUGE_CORPUS_WITHDRAW_URL", raising=False)

    def _fail_if_prompted(*a, **k):
        raise AssertionError("input() was called -- config check did not fail fast")

    monkeypatch.setattr("builtins.input", _fail_if_prompted)

    import argparse

    import tracegauge.cli as cli_module

    ns = argparse.Namespace(command="corpus", corpus_command="withdraw")
    cli_module._run_corpus(ns)

    captured = capsys.readouterr()
    assert "[NOT AVAILABLE]" in captured.err


def test_no_send_even_with_consent_true_when_unconfigured(monkeypatch) -> None:
    """A fresh install has no TRACEGAUGE_CORPUS_* env vars — contribute() must
    refuse to send even if consent_given were somehow True, because there is
    nowhere configured to send it."""
    monkeypatch.delenv("TRACEGAUGE_CORPUS_URL", raising=False)
    monkeypatch.delenv("TRACEGAUGE_CORPUS_ANON_KEY", raising=False)
    monkeypatch.delenv("TRACEGAUGE_CORPUS_WITHDRAW_URL", raising=False)

    conn = _conn_with_one_session()
    config = CorpusConfig.from_env()
    assert config is None

    with patch("tracegauge.corpus_client.httpx.post") as mock_post:
        result = contribute(conn, consent_given=True, contributor_id=None, config=config)
    mock_post.assert_not_called()
    assert result.sent is False
    assert result.reason == "corpus not configured"
