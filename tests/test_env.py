# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

"""TRACEGAUGE_* is primary; the deprecated TES_* names are still honoured."""

from __future__ import annotations

from pathlib import Path

import pytest
from tracegauge import _env, cli, plan, store
from tracegauge._env import deprecated_in_use, deprecation_line, env_value, notice_suppressed


def test_only_the_primary_name_set() -> None:
    assert env_value("DB_PATH", {"TRACEGAUGE_DB_PATH": "a"}) == "a"


def test_only_the_deprecated_name_set() -> None:
    assert env_value("DB_PATH", {"TES_DB_PATH": "b"}) == "b"


def test_both_set_the_primary_wins() -> None:
    assert env_value("DB_PATH", {"TRACEGAUGE_DB_PATH": "a", "TES_DB_PATH": "b"}) == "a"


def test_neither_set() -> None:
    assert env_value("DB_PATH", {}) is None


def test_empty_string_counts_as_unset_in_both_places() -> None:
    assert env_value("DB_PATH", {"TRACEGAUGE_DB_PATH": "", "TES_DB_PATH": "b"}) == "b"
    assert env_value("DB_PATH", {"TRACEGAUGE_DB_PATH": "a", "TES_DB_PATH": ""}) == "a"
    assert env_value("DB_PATH", {"TRACEGAUGE_DB_PATH": "", "TES_DB_PATH": ""}) is None


def test_every_setting_has_both_spellings() -> None:
    for name in _env.SETTINGS:
        assert env_value(name, {"TES_" + name: "x"}) == "x"
        assert env_value(name, {"TRACEGAUGE_" + name: "y", "TES_" + name: "x"}) == "y"


def test_deprecated_in_use_lists_only_names_without_a_primary_twin() -> None:
    values = {
        "TES_DB_PATH": "x",
        "TRACEGAUGE_DB_PATH": "y",  # twin set: nothing to say about DB_PATH
        "TES_PLAN_PATH": "p",
        "TES_PRICE_TABLE": "",  # empty is unset
        "TES_UNRELATED": "z",  # not a setting we read
    }
    assert deprecated_in_use(values) == ["TES_PLAN_PATH"]


def test_deprecation_line_names_the_variable_and_its_replacement() -> None:
    line = deprecation_line({"TES_DB_PATH": "x"})
    assert line == (
        "TES_DB_PATH is deprecated; use TRACEGAUGE_DB_PATH. The TES_ names will be removed in 0.18."
    )
    plural = deprecation_line({"TES_DB_PATH": "x", "TES_PLAN_PATH": "p"})
    assert plural is not None
    assert "are deprecated" in plural
    assert "TRACEGAUGE_DB_PATH, TRACEGAUGE_PLAN_PATH" in plural
    assert "\n" not in plural


def test_deprecation_line_is_silent_when_there_is_nothing_or_it_is_suppressed() -> None:
    assert deprecation_line({}) is None
    assert deprecation_line({"TRACEGAUGE_DB_PATH": "y"}) is None
    assert deprecation_line({"TES_DB_PATH": "x", "TRACEGAUGE_NO_DEPRECATION_NOTICE": "1"}) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), (" True ", True), ("yes", True), ("0", False), ("", False), ("no", False)],
)
def test_notice_suppression_values(value: str, expected: bool) -> None:
    assert notice_suppressed({"TRACEGAUGE_NO_DEPRECATION_NOTICE": value}) is expected


def test_the_store_honours_both_names_and_prefers_the_new_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TRACEGAUGE_DB_PATH", raising=False)
    monkeypatch.setenv("TES_DB_PATH", str(tmp_path / "old.db"))
    assert store.resolve_db_path() == tmp_path / "old.db"
    monkeypatch.setenv("TRACEGAUGE_DB_PATH", str(tmp_path / "new.db"))
    assert store.resolve_db_path() == tmp_path / "new.db"


def test_plan_path_honours_both_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TRACEGAUGE_PLAN_PATH", raising=False)
    monkeypatch.setenv("TES_PLAN_PATH", str(tmp_path / "old.json"))
    assert plan.resolve_plan_config_path() == tmp_path / "old.json"
    monkeypatch.setenv("TRACEGAUGE_PLAN_PATH", str(tmp_path / "new.json"))
    assert plan.resolve_plan_config_path() == tmp_path / "new.json"


def test_cli_prints_the_variable_notice_once_on_stderr_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TRACEGAUGE_DB_PATH", raising=False)
    monkeypatch.delenv("TRACEGAUGE_NO_DEPRECATION_NOTICE", raising=False)
    monkeypatch.setenv("TES_DB_PATH", "x")
    monkeypatch.setattr("sys.argv", ["tracegauge", "--version"])
    with pytest.raises(SystemExit):
        cli.main()
    out, err = capsys.readouterr()
    assert out.startswith("tracegauge ")
    assert err.count("TES_DB_PATH is deprecated") == 1
    assert "TES_DB_PATH" not in out
