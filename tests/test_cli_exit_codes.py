from __future__ import annotations

"""Exit codes are a contract (docs/EXIT_CODES.md): scripts and hooks branch on them (W1A item 6).

`score` on a corrupt file used to print [ERROR] and exit 0; `monitor` printed [ALARM] and exit 0,
so there was no machine-readable pass/fail. These tests run the real CLI (subprocess for the
score paths, main() with the live-session seam stubbed for the alarm) and pin every code.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import tes.cli as cli
from tes.exit_codes import EXIT_ADAPT_ERROR, EXIT_ALARM, EXIT_OK, EXIT_USAGE, epilog

from tests.test_alarm_measured import _live, _self_baseline_active, _self_baseline_building

GOOD_RECORDS: list[dict[str, Any]] = [
    {"type": "user", "message": {"role": "user", "content": "do the thing"}},
    {
        "type": "assistant",
        "message": {
            "id": "m0",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "done"}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 100, "output_tokens": 20},
        },
    },
]


def _good(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in GOOD_RECORDS) + "\n", encoding="utf-8")
    return path


def _corrupt(path: Path) -> Path:
    """Invalid UTF-8: the adapter raises while reading it (a parse error, not an empty session)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not json\n\xff\xfe\x00 garbage\n{broken")
    return path


def _run(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home),
        "APPDATA": str(home),
        "TES_DB_PATH": str(tmp_path / "tes.db"),
        "PYTHONIOENCODING": "utf-8",
    }
    env.pop("ANTHROPIC_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-m", "tes", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        check=False,
    )


def test_codes_are_distinct_and_stable() -> None:
    # Documented numbers: changing one is a breaking change to every script that branches on it.
    assert (EXIT_OK, EXIT_USAGE, EXIT_ALARM, EXIT_ADAPT_ERROR) == (0, 1, 3, 4)


def test_epilog_lists_every_requested_code() -> None:
    text = epilog(EXIT_OK, EXIT_ALARM)
    assert text.startswith("Exit codes:")
    assert "  3  monitor" in text
    assert "docs/EXIT_CODES.md" in text


def test_score_good_file_exits_zero(tmp_path: Path) -> None:
    proc = _run(tmp_path, "score", str(_good(tmp_path / "p" / "ok.jsonl")), "--no-judge")
    assert proc.returncode == EXIT_OK, proc.stderr


def test_score_corrupt_file_exits_adapt_error(tmp_path: Path) -> None:
    proc = _run(tmp_path, "score", str(_corrupt(tmp_path / "p" / "bad.jsonl")), "--no-judge")
    assert proc.returncode == EXIT_ADAPT_ERROR
    assert "[ERROR] Failed to adapt bad.jsonl" in proc.stderr
    assert "1 of 1 session(s) could not be parsed" in proc.stderr


def test_score_missing_path_exits_usage(tmp_path: Path) -> None:
    proc = _run(tmp_path, "score", str(tmp_path / "nope.jsonl"), "--no-judge")
    assert proc.returncode == EXIT_USAGE
    assert "Path not found" in proc.stderr


def test_score_directory_scores_the_rest_but_exits_adapt_error(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    _good(d / "a-good.jsonl")
    _corrupt(d / "b-bad.jsonl")
    _good(d / "c-good.jsonl")
    proc = _run(tmp_path, "score", str(d), "--no-judge", "--json")
    assert proc.returncode == EXIT_ADAPT_ERROR
    # both good sessions were still scored (two JSON documents on stdout)
    assert proc.stdout.count('"session_id"') == 2
    assert "1 of 3 session(s) could not be parsed" in proc.stderr
    assert "b-bad.jsonl" in proc.stderr
    assert "[ERROR]" not in proc.stdout  # JSON mode keeps stdout machine-clean


def test_score_directory_of_good_files_exits_zero(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    _good(d / "a.jsonl")
    _good(d / "b.jsonl")
    assert _run(tmp_path, "score", str(d), "--no-judge").returncode == EXIT_OK


def test_help_epilog_documents_the_codes(tmp_path: Path) -> None:
    out = _run(tmp_path, "score", "--help").stdout
    assert "Exit codes:" in out
    assert "  4  score: at least one session could not be parsed" in out
    assert "  3  monitor" not in out  # only the codes this command can return
    assert "  3  monitor" in _run(tmp_path, "monitor", "--help").stdout


def test_cost_with_unopenable_store_exits_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A directory where the DB file should be: open_db fails. Used to exit 0 after "[ERROR]".
    bad_db = tmp_path / "isdir.db"
    bad_db.mkdir()
    proc = _run(tmp_path, "cost", "--week", "--db-path", str(bad_db))
    assert proc.returncode == EXIT_USAGE
    assert "Cannot open TES store" in proc.stderr


# ----------------------------------------------------------------------------- monitor


def _monitor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, live: Any, baseline: Any) -> int:
    import tes.live_monitor as lm
    import tes.self_baseline as sb

    monkeypatch.setenv("TES_DB_PATH", str(tmp_path / "tes.db"))
    monkeypatch.setattr(lm, "find_active_session", lambda *a, **k: Path("/fake/active.jsonl"))
    monkeypatch.setattr(lm, "score_live_session", lambda *a, **k: live)
    monkeypatch.setattr(sb, "load_or_compute", lambda *a, **k: baseline)
    monkeypatch.setattr("sys.argv", ["tes", "monitor"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return int(exc.value.code or 0)


def test_monitor_alarm_firing_exits_alarm_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _monitor(monkeypatch, tmp_path, _live(), _self_baseline_active())
    assert code == EXIT_ALARM
    assert "[ALARM]" in capsys.readouterr().out


def test_monitor_no_alarm_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _monitor(monkeypatch, tmp_path, _live(), _self_baseline_building())
    assert code == EXIT_OK
    assert "No alarm" in capsys.readouterr().out


def test_monitor_without_active_session_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nothing is running, so there is nothing to alarm on. A SessionEnd hook or cron job that
    # runs `tes monitor` must not read "no session" as a failure.
    import tes.live_monitor as lm

    monkeypatch.setenv("TES_DB_PATH", str(tmp_path / "tes.db"))
    monkeypatch.setattr(lm, "find_active_session", lambda *a, **k: None)
    monkeypatch.setattr("sys.argv", ["tes", "monitor", "--cc-path", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == EXIT_OK
    assert "No active session" in capsys.readouterr().out
