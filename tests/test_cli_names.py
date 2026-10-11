# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

"""The command is ``tracegauge``; ``tes`` is a deprecated alias that warns on stderr only."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tracegauge import __version__, cli

NOTICE = "tes is deprecated; use tracegauge (or tgauge). It will be removed in 0.18."


def _argv(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["x", *argv])


def test_version_names_tracegauge_whatever_the_invocation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _argv(monkeypatch, "--version")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"tracegauge {__version__}"


def test_tes_prints_one_notice_to_stderr_and_nothing_extra_to_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TRACEGAUGE_NO_DEPRECATION_NOTICE", raising=False)
    _argv(monkeypatch, "--version")
    with pytest.raises(SystemExit):
        cli.tes_main()
    out, err = capsys.readouterr()
    assert out.strip() == f"tracegauge {__version__}"
    assert err.strip().splitlines() == [NOTICE]


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_the_notice_is_suppressible(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    monkeypatch.setenv("TRACEGAUGE_NO_DEPRECATION_NOTICE", value)
    _argv(monkeypatch, "--version")
    with pytest.raises(SystemExit):
        cli.tes_main()
    assert capsys.readouterr().err == ""


def test_an_empty_or_zero_setting_does_not_suppress(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for value in ("", "0"):
        monkeypatch.setenv("TRACEGAUGE_NO_DEPRECATION_NOTICE", value)
        _argv(monkeypatch, "--version")
        with pytest.raises(SystemExit):
            cli.tes_main()
        assert NOTICE in capsys.readouterr().err


def test_tracegauge_itself_never_prints_the_notice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TRACEGAUGE_NO_DEPRECATION_NOTICE", raising=False)
    _argv(monkeypatch, "--version")
    with pytest.raises(SystemExit):
        cli.main()
    assert capsys.readouterr().err == ""


def test_json_stdout_is_untouched_by_the_notice(tmp_path: Path) -> None:
    """Through the real `python -m tes` path: stdout stays parseable JSON, notice on stderr once."""
    sample = Path(__file__).resolve().parent.parent / "tracegauge" / "data"
    sample = sample / "quickstart_sample_session.jsonl"
    env_home = str(tmp_path)
    import os

    env = {
        **os.environ,
        "HOME": env_home,
        "USERPROFILE": env_home,
        "TRACEGAUGE_DB_PATH": str(tmp_path / "tg.db"),
    }
    env.pop("TRACEGAUGE_NO_DEPRECATION_NOTICE", None)
    proc = subprocess.run(
        [sys.executable, "-m", "tes", "score", str(sample), "--no-judge", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    json.loads(proc.stdout)
    assert proc.stderr.count(NOTICE) == 1
