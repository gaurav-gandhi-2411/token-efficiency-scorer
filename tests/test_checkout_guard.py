"""The stale-editable-install guard, exercised in both directions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests._checkout_guard import _PROBE, enforce, installed_package_file

_REPO = Path(__file__).resolve().parent.parent


def test_install_inside_this_checkout_is_accepted():
    inside = _REPO / "tes" / "__init__.py"

    enforce(_REPO, probe=lambda: inside)  # must not raise


def test_install_from_another_checkout_exits_with_the_fix(tmp_path):
    stale = tmp_path / "older-checkout" / "tes" / "__init__.py"

    with pytest.raises(pytest.exit.Exception) as excinfo:
        enforce(_REPO, probe=lambda: stale)

    msg = str(excinfo.value)
    assert str(stale.resolve()) in msg  # says what it found
    assert str(_REPO / "tes") in msg  # and what it expected
    assert "pip install -e ." in msg  # and what to run
    assert excinfo.value.returncode == 4


def test_a_package_that_is_not_installed_exits_too():
    with pytest.raises(pytest.exit.Exception) as excinfo:
        enforce(_REPO, probe=lambda: None)

    assert "not importable at all" in str(excinfo.value)


def test_a_sibling_directory_with_the_same_prefix_is_not_mistaken_for_this_checkout():
    # "<repo>/tes-old/..." starts with the string "<repo>/tes" but is a different directory.
    lookalike = _REPO / "tes-old" / "tes" / "__init__.py"

    with pytest.raises(pytest.exit.Exception):
        enforce(_REPO, probe=lambda: lookalike)


def test_the_real_probe_finds_this_checkouts_package_in_the_running_environment():
    # Not mocked: a fresh subprocess of the interpreter running this suite. This is the same check
    # the session-start hook made, so a stale venv would already have stopped the session.
    found = installed_package_file(sys.executable)

    assert found is not None
    assert found.resolve().is_relative_to((_REPO / "tes").resolve())


def test_a_missing_interpreter_raises_instead_of_passing_the_guard_silently(tmp_path):
    bogus = tmp_path / "no-such-python"

    with pytest.raises(FileNotFoundError):
        installed_package_file(str(bogus))


def test_a_probe_that_cannot_answer_exits_with_a_message_not_a_traceback():
    def hung() -> Path | None:
        raise subprocess.TimeoutExpired(cmd="probe", timeout=1)

    with pytest.raises(pytest.exit.Exception) as excinfo:
        enforce(_REPO, probe=hung)

    assert "could not determine which tes" in str(excinfo.value)
    assert "TimeoutExpired" in str(excinfo.value)
    assert excinfo.value.returncode == 4


def test_the_probe_locates_the_package_without_executing_it(tmp_path):
    # A package whose import explodes: importing it would fail, find_spec only locates it. The planted
    # copy is put ahead of the editable install with a meta-path finder, because a flat-layout editable
    # install can outrank PYTHONPATH and the test must not depend on that ordering.
    pkg = tmp_path / "tes"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("raise RuntimeError('imported')\n", encoding="utf-8")
    plant = (
        "import sys, importlib.machinery as m\n"
        "class F:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        f"        return m.PathFinder.find_spec(name, [{str(tmp_path)!r}]) if name == 'tes' else None\n"
        "sys.meta_path.insert(0, F())\n"
    )

    proc = subprocess.run(  # noqa: S603 -- fixed argv
        [sys.executable, "-c", plant + _PROBE],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()) == pkg / "__init__.py"
