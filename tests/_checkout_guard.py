"""Refuse to run the suite against a ``tes`` that is not this checkout's.

pytest's ``pythonpath = [".", "src", "scripts"]`` (pyproject.toml) makes every IN-PROCESS import
resolve to this checkout. A subprocess does not get it: ``tests/test_prior_features_intact.py`` and
others start ``sys.executable`` and import whatever is INSTALLED in that environment. When the venv's
editable install still points at an older checkout (or a worktree that has since been removed), those
tests fail with a confusing assertion about the code under test while every in-process test passes.
Seen in the sibling repo adk-tracegauge twice (2026-10-07): "failures" that were an environment fault,
not a defect. Ported here from ``adk-tracegauge/tests/_checkout_guard.py``.

``enforce`` is called once, at session start, by ``tests/conftest.py``.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

# find_spec LOCATES the package without executing it. Importing it would run the package's whole
# import chain (numpy, scikit-learn, flask): slow on a cold venv, and a crash inside it would be
# reported as "not installed".
_PROBE = (
    "import importlib.util as u; s = u.find_spec('tes'); "
    "print(s.origin if s is not None and s.origin else '')"
)


def installed_package_file(python: str = sys.executable) -> Path | None:
    """The ``tes/__init__.py`` a fresh subprocess of ``python`` would import, or None.

    ``-I`` ignores the working directory and PYTHON* variables, so only the environment's own
    installation can answer. None means the package is not installed there at all.
    """
    proc = subprocess.run(  # noqa: S603 -- fixed argv, no shell, no user input
        [python, "-I", "-W", "ignore", "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip().splitlines()[-1])


def enforce(
    repo_root: Path,
    probe: Callable[[], Path | None] = installed_package_file,
) -> None:
    """Exit the pytest session, with an actionable message, if the install is not this checkout's."""
    pkg = (repo_root / "tes").resolve()
    try:
        found = probe()
    except (subprocess.TimeoutExpired, OSError) as exc:
        # fail closed with a message, not an INTERNALERROR traceback: "could not check" is not "fine"
        pytest.exit(
            f"could not determine which tes the test interpreter ({sys.executable}) "
            f"would import: {type(exc).__name__}: {exc}.\n"
            f"Check that interpreter works, then from {repo_root} run:  uv pip install -e .",
            returncode=4,
        )
    if found is not None and found.resolve().is_relative_to(pkg):
        return
    where = f"resolves to {found.resolve()}" if found is not None else "is not importable at all"
    pytest.exit(
        f"tes in the test interpreter's environment ({sys.executable}) {where}, "
        f"not inside this checkout's {pkg}.\n"
        "Subprocess-based tests import the INSTALLED package, so they would test the wrong code "
        "and fail misleadingly.\n"
        f"Fix: from {repo_root}, run:  uv pip install -e .   (or: uv sync)",
        returncode=4,
    )
