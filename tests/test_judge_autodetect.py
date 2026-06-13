from __future__ import annotations

"""tests/test_judge_autodetect.py — 0.6.0 judge auto-detect + guide, consent preserved.

The 0.6.0 promise: `--judge` finds the best available judge so the user does not
have to configure flags. The NON-NEGOTIABLE: auto-detecting an API key never
sends data. Egress is still gated by explicit per-session consent.

Covered:
  - Ollama present  -> local judge used, no API config built
  - Ollama absent + API key present -> API judge OFFERED, consent screen shown
  - Consent DECLINED -> no API config reaches the scorer (no silent egress)
  - Consent ACCEPTED -> API config + consent flag reach the scorer
  - Ollama absent + no API key -> guidance printed, scoring still runs (token+waste)
  - Unconditional gate re-affirmed: score_trajectory_api(consent_given=False) is a no-op
"""

import socket

import pytest

import tes.cli as cli
import tes.judge as judge


def _make_session(dirpath, name="s.jsonl", mtime=1000.0):
    p = dirpath / name
    p.write_text("{}\n", encoding="utf-8")
    import os

    os.utime(p, (mtime, mtime))
    return p


@pytest.fixture
def capture_scored(monkeypatch):
    calls: list[dict] = []

    def _fake_score(path, baselines, judge_config, use_local_judge, json_mode, **kwargs):
        calls.append(
            {
                "use_local_judge": use_local_judge,
                "api_judge_config": kwargs.get("api_judge_config"),
                "api_judge_consent": kwargs.get("api_judge_consent"),
            }
        )

    monkeypatch.setattr(cli, "_score_path_with_api_judge", _fake_score)
    return calls


def _run(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", ["tes", *argv])
    try:
        cli.main()
    except SystemExit:
        pass


@pytest.fixture
def cc(tmp_path, monkeypatch):
    monkeypatch.setenv("TES_DB_PATH", str(tmp_path / "tes.db"))
    d = tmp_path / "projects"
    d.mkdir()
    _make_session(d)
    return d


def test_judge_uses_ollama_when_available(monkeypatch, cc, capture_scored):
    """--judge with a reachable local model uses the local judge (no API path)."""
    monkeypatch.setattr(cli, "is_judge_available", lambda *a, **k: True)
    monkeypatch.setattr(cli, "detect_env_api_key", lambda *a, **k: "sk-should-not-matter")

    _run(monkeypatch, ["score", "--judge", "--cc-path", str(cc)])

    assert len(capture_scored) == 1
    assert capture_scored[0]["use_local_judge"] is True
    assert capture_scored[0]["api_judge_config"] is None  # never escalates to API when local works


def test_judge_offers_api_when_key_present_consent_declined(monkeypatch, cc, capture_scored):
    """No Ollama + API key present -> API offered; declining means NO config reaches scorer."""
    monkeypatch.setattr(cli, "is_judge_available", lambda *a, **k: False)
    monkeypatch.setattr(cli, "detect_env_api_key", lambda *a, **k: "sk-test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")  # decline the consent

    _run(monkeypatch, ["score", "--judge", "--cc-path", str(cc)])

    assert len(capture_scored) == 1
    # Declined consent => no egress config and consent flag false. Token+waste still scored.
    assert capture_scored[0]["api_judge_config"] is None
    assert capture_scored[0]["api_judge_consent"] is False
    assert capture_scored[0]["use_local_judge"] is False


def test_judge_offers_api_when_key_present_consent_accepted(monkeypatch, cc, capture_scored):
    """Accepting consent passes the API config + consent flag through to the scorer."""
    monkeypatch.setattr(cli, "is_judge_available", lambda *a, **k: False)
    monkeypatch.setattr(cli, "detect_env_api_key", lambda *a, **k: "sk-test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")  # grant consent

    _run(monkeypatch, ["score", "--judge", "--cc-path", str(cc)])

    assert len(capture_scored) == 1
    assert capture_scored[0]["api_judge_config"] is not None
    assert capture_scored[0]["api_judge_consent"] is True


def test_judge_guides_when_nothing_available(monkeypatch, cc, capture_scored, capsys):
    """No Ollama + no API key -> print the simplest next step; still score token+waste."""
    monkeypatch.setattr(cli, "is_judge_available", lambda *a, **k: False)
    monkeypatch.setattr(cli, "detect_env_api_key", lambda *a, **k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    _run(monkeypatch, ["score", "--judge", "--cc-path", str(cc)])

    err = capsys.readouterr().err.lower()
    assert "ollama" in err or "api" in err  # guidance, not a cryptic failure
    assert len(capture_scored) == 1  # token + waste still ran
    assert capture_scored[0]["api_judge_config"] is None


def test_no_silent_egress_consent_gate_is_unconditional(monkeypatch):
    """Re-affirm the moat: score_trajectory_api makes ZERO network calls without consent.

    Even with a valid-looking config, consent_given=False must short-circuit before
    any socket is opened. (The CLI auto-detect can only ever route to this function.)
    """

    def _boom(*a, **k):
        raise AssertionError("network call attempted without consent — silent egress!")

    monkeypatch.setattr(socket.socket, "connect", _boom)

    cfg = judge.ApiJudgeConfig(api_key="sk-test-key")
    result = judge.score_trajectory_api({"session_id": "x", "digest": {"turns": []}}, cfg, consent_given=False)
    assert result is None  # no verdict, no network, no egress


def test_detect_env_api_key_is_detect_only(monkeypatch):
    """detect_env_api_key reports presence but performs no network activity."""

    def _boom(*a, **k):
        raise AssertionError("detect_env_api_key must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-present")
    assert judge.detect_env_api_key() == "sk-present"

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert judge.detect_env_api_key() is None
