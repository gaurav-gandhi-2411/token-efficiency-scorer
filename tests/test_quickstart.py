from __future__ import annotations

"""tests/test_quickstart.py — HH1.2: end-to-end coverage for `tes quickstart`
/ `tracegauge quickstart`. Runs the real scoring pipeline against the bundled
sample transcript (tes/data/quickstart_sample_session.jsonl) -- deliberately
not stubbed, since the entire point of this command is that it works out of
the box against what actually ships in the wheel, with no local judge probe
and no network call.
"""

import pytest

import tes.cli as cli


def test_quickstart_exits_zero_and_prints_a_real_report(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["tes", "quickstart"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0

    out = capsys.readouterr().out
    assert "TOKEN-EFFICIENCY SCORER" in out
    assert "quickstart_sample_session" in out
    assert "COST ANNOTATION" in out
    assert "This ran entirely from what shipped in the installed package" in out


def test_quickstart_never_probes_a_local_judge(monkeypatch, capsys):
    # The whole point of `quickstart` is zero network/local-model dependency
    # -- assert the judge-availability probe is never even called, not just
    # that its result doesn't appear in the output.
    called = []
    monkeypatch.setattr(cli, "is_judge_available", lambda *a, **k: called.append(1) or False)
    monkeypatch.setattr("sys.argv", ["tes", "quickstart"])
    with pytest.raises(SystemExit):
        cli.main()
    assert called == []

    out = capsys.readouterr().out
    assert "TRAJECTORY QUALITY" in out
    assert "UNAVAILABLE (no local judge configured)" in out


def test_quickstart_bundled_fixture_is_reachable_via_importlib_resources():
    from importlib import resources

    sample_path = resources.files("tes.data") / "quickstart_sample_session.jsonl"
    with resources.as_file(sample_path) as concrete_path:
        assert concrete_path.exists()
        content = concrete_path.read_text(encoding="utf-8")
        assert content.strip()
        # 9 lines: 1 user + 4 assistant text/tool-use turns + 3 tool_result +
        # 1 closing assistant summary -- matches the fixture as authored.
        assert len(content.strip().splitlines()) == 9
