from __future__ import annotations

"""tes/test_cli_ergonomics.py — 0.6.0 frictionless-UX ergonomics.

These tests exercise the FRONT DOOR only (invocation + defaults). The scoring
engine is stubbed via _score_path_with_api_judge so the tests are fast and assert
*which* session was selected and *how* the judge was resolved — never re-testing
the (frozen) scoring numbers.

Covered:
  - bare `tes` launches the dashboard (= serve)
  - `tes score` with no path scores the newest session
  - `tes score --pick` lists recent sessions and scores the chosen one
  - `tes score --judge` is no longer an ambiguous option (the real friction bug)
  - explicit PATH still wins over the newest-session default
"""

import os

import pytest

import tes.cli as cli


def _make_session(dirpath, name: str, mtime: float) -> object:
    """Create an empty *.jsonl under dirpath with a fixed mtime."""
    p = dirpath / name
    p.write_text("{}\n", encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Point the store at a throwaway DB and stub the judge probe (no real Ollama)."""
    monkeypatch.setenv("TES_DB_PATH", str(tmp_path / "tes.db"))
    monkeypatch.setattr(cli, "is_judge_available", lambda *a, **k: False)
    monkeypatch.setattr(cli, "detect_env_api_key", lambda *a, **k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def capture_scored(monkeypatch):
    """Replace the per-session scorer with a recorder. Returns the call list."""
    calls: list[dict] = []

    def _fake_score(path, baselines, judge_config, use_local_judge, json_mode, **kwargs):
        calls.append(
            {
                "path": path,
                "use_local_judge": use_local_judge,
                "api_judge_config": kwargs.get("api_judge_config"),
                "api_judge_consent": kwargs.get("api_judge_consent"),
            }
        )

    monkeypatch.setattr(cli, "_score_path_with_api_judge", _fake_score)
    return calls


def _run(monkeypatch, argv: list[str]) -> None:
    monkeypatch.setattr("sys.argv", ["tes", *argv])
    try:
        cli.main()
    except SystemExit:
        pass


def test_bare_tes_launches_dashboard(monkeypatch, tmp_path):
    """`tes` with no subcommand starts the localhost dashboard (serve), not help."""
    monkeypatch.setenv("TES_DB_PATH", str(tmp_path / "tes.db"))

    captured: dict = {}

    def _fake_start_server(config):
        captured["config"] = config  # returns immediately (no blocking serve loop)

    class _FakeThread:
        def join(self, timeout=None):  # noqa: D401
            return None

    class _FakeEvent:
        def set(self):
            return None

    def _fake_start_watcher(config):
        return _FakeThread(), _FakeEvent()

    monkeypatch.setattr("tes.web.server.start_server", _fake_start_server)
    monkeypatch.setattr("tes.watcher.start_watcher", _fake_start_watcher)

    _run(monkeypatch, [])  # bare `tes`

    assert "config" in captured, "bare `tes` did not launch the dashboard"
    assert captured["config"].host == "127.0.0.1"  # moat: localhost-only bind preserved
    assert captured["config"].port == 4747


def test_score_no_path_scores_newest(monkeypatch, tmp_path, isolated_store, capture_scored):
    """`tes score` with no path scores the most-recently-modified session."""
    cc = tmp_path / "projects"
    cc.mkdir()
    _make_session(cc, "old.jsonl", mtime=1000.0)
    newest = _make_session(cc, "new.jsonl", mtime=5000.0)
    _make_session(cc, "mid.jsonl", mtime=3000.0)

    _run(monkeypatch, ["score", "--cc-path", str(cc)])

    assert len(capture_scored) == 1
    assert capture_scored[0]["path"] == newest


def test_score_pick_lists_and_selects(monkeypatch, tmp_path, isolated_store, capture_scored):
    """`tes score --pick` offers recent sessions newest-first; choice 2 selects the 2nd."""
    cc = tmp_path / "projects"
    cc.mkdir()
    _make_session(cc, "a.jsonl", mtime=1000.0)  # oldest -> index 3
    second = _make_session(cc, "b.jsonl", mtime=3000.0)  # middle -> index 2
    _make_session(cc, "c.jsonl", mtime=5000.0)  # newest -> index 1

    monkeypatch.setattr("builtins.input", lambda *a, **k: "2")

    _run(monkeypatch, ["score", "--pick", "--cc-path", str(cc)])

    assert len(capture_scored) == 1
    assert capture_scored[0]["path"] == second


def test_score_pick_abort_scores_nothing(monkeypatch, tmp_path, isolated_store, capture_scored):
    """An out-of-range / empty pick aborts cleanly and scores nothing."""
    cc = tmp_path / "projects"
    cc.mkdir()
    _make_session(cc, "a.jsonl", mtime=1000.0)

    monkeypatch.setattr("builtins.input", lambda *a, **k: "")  # default -> 1, but test abort below

    # An out-of-range choice must abort without scoring.
    monkeypatch.setattr("builtins.input", lambda *a, **k: "99")
    _run(monkeypatch, ["score", "--pick", "--cc-path", str(cc)])
    assert capture_scored == []


def test_explicit_path_wins_over_newest(monkeypatch, tmp_path, isolated_store, capture_scored):
    """An explicit PATH is scored even when a newer session exists elsewhere."""
    cc = tmp_path / "projects"
    cc.mkdir()
    _make_session(cc, "newer.jsonl", mtime=9000.0)
    explicit = _make_session(tmp_path, "explicit.jsonl", mtime=10.0)

    _run(monkeypatch, ["score", str(explicit), "--cc-path", str(cc)])

    assert len(capture_scored) == 1
    assert capture_scored[0]["path"] == explicit


def test_judge_flag_not_ambiguous(monkeypatch, tmp_path, isolated_store, capture_scored):
    """`tes score --judge` must NOT raise the argparse 'ambiguous option' error.

    This is the headline friction bug: before 0.6.0, --judge collided with
    --judge-model/--judge-endpoint and argparse aborted with exit code 2.
    """
    cc = tmp_path / "projects"
    cc.mkdir()
    _make_session(cc, "s.jsonl", mtime=1000.0)

    monkeypatch.setattr("sys.argv", ["tes", "score", "--judge", "--cc-path", str(cc)])
    # If --judge were still ambiguous, argparse raises SystemExit(2) before scoring.
    try:
        cli.main()
    except SystemExit as exc:
        assert exc.code in (None, 0), f"--judge errored with exit code {exc.code}"

    assert len(capture_scored) == 1, "scoring did not run — --judge likely still ambiguous"


def test_judge_and_no_judge_mutually_exclusive(monkeypatch, tmp_path, isolated_store):
    """Contradictory --judge / --no-judge fails fast with a clear message (not a crash)."""
    cc = tmp_path / "projects"
    cc.mkdir()
    _make_session(cc, "s.jsonl", mtime=1000.0)

    monkeypatch.setattr("sys.argv", ["tes", "score", "--judge", "--no-judge", "--cc-path", str(cc)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
