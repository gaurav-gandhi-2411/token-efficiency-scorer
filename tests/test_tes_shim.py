# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

"""The deprecated ``tes`` import name is an alias of ``tracegauge``, not a copy.

Every check runs in a fresh interpreter: the warning fires once per process and the shim rewrites
``sys.modules``, so an in-process test would depend on what earlier tests imported.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MESSAGE_FRAGMENT = "tes is deprecated, import tracegauge"


def _run(code: str, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *flags, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=120,
    )


def test_importing_tracegauge_never_warns() -> None:
    proc = _run(
        "import tracegauge, tracegauge.cli, tracegauge.cost", "-W", "error::DeprecationWarning"
    )
    assert proc.returncode == 0, proc.stderr


def test_importing_tes_warns_once_with_the_migration_message() -> None:
    proc = _run(
        """
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import tes
            import tes.cost
            import tes.web.server
            from tes.adapt import adapt_session  # noqa: F401
        mine = [w for w in caught if issubclass(w.category, DeprecationWarning)
                and "tes is deprecated" in str(w.message)]
        assert len(mine) == 1, [str(w.message) for w in caught]
        assert str(mine[0].message) == "tes is deprecated, import tracegauge; removed in 0.18."
        # stacklevel points at the importer (this script), not at the shim or importlib.
        assert mine[0].filename == "<string>", mine[0].filename
        """
    )
    assert proc.returncode == 0, proc.stderr


def test_every_module_resolves_to_the_same_object_under_both_names() -> None:
    proc = _run(
        """
        import importlib, pkgutil, sys, warnings
        warnings.simplefilter("ignore")
        import tracegauge, tes

        assert tes is tracegauge
        names = [
            m.name
            for m in pkgutil.walk_packages(tracegauge.__path__, "tracegauge.", onerror=lambda n: None)
        ]
        assert len(names) > 20, names
        for real in names:
            if real.endswith(".__main__"):
                continue  # tes.__main__ is its own guarded module; see the python -m test
            old = "tes" + real[len("tracegauge"):]
            try:
                a = importlib.import_module(real)
            except ImportError:
                continue  # optional extra not installed: absent under both names
            b = importlib.import_module(old)
            assert a is b, (real, old)
            assert sys.modules[old] is a
            assert a.__name__ == real and a.__spec__.name == real, (a.__name__, a.__spec__.name)
        print(len(names))
        """
    )
    assert proc.returncode == 0, proc.stderr


def test_every_public_symbol_is_the_same_object_and_monkeypatch_crosses_names() -> None:
    proc = _run(
        """
        import warnings
        warnings.simplefilter("ignore")
        import tracegauge.cost as new
        import tes.cost as old
        for name in dir(new):
            assert getattr(old, name) is getattr(new, name), name
        from tes.adapt import adapt_session as f_old
        from tracegauge.adapt import adapt_session as f_new
        assert f_old is f_new
        new.SENTINEL = 1
        assert old.SENTINEL == 1
        # attribute access without importing the submodule first, like plain tracegauge
        import tes
        assert tes.__version__ == tracegauge_version()
        """.replace("tracegauge_version()", "__import__('tracegauge').__version__")
    )
    assert proc.returncode == 0, proc.stderr


def test_unknown_submodule_is_a_normal_import_error() -> None:
    proc = _run(
        """
        import warnings
        warnings.simplefilter("ignore")
        try:
            import tes.nope
        except ModuleNotFoundError as exc:
            assert exc.name == "tes.nope", exc.name
        else:
            raise SystemExit("expected ModuleNotFoundError")
        """
    )
    assert proc.returncode == 0, proc.stderr


def test_python_dash_m_tes_runs_the_cli() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "tes", "--version"],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "0." in proc.stdout


def test_python_dash_m_tes_submodule_behaves_like_the_real_one() -> None:
    def run(module: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-W", "always", "-m", module, "--help"],
            capture_output=True,
            text=True,
            cwd=REPO,
            timeout=120,
        )

    old, new = run("tes.community_baseline"), run("tracegauge.community_baseline")
    assert new.returncode == 0, new.stderr
    assert old.returncode == 0, old.stderr
    # argparse (3.14+) prints the invoked module name in the usage line, so `-m tes.x` and
    # `-m tracegauge.x` legitimately differ in that one token. Normalise only that token; every other
    # byte of the help text must still match.
    assert old.stdout.replace("-m tes.community_baseline", "-m <module>") == new.stdout.replace(
        "-m tracegauge.community_baseline", "-m <module>"
    )
    assert old.stderr.count(MESSAGE_FRAGMENT) == 1
    assert MESSAGE_FRAGMENT not in new.stderr


def test_shim_is_listed_for_the_wheel() -> None:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'include = ["tracegauge*", "tes*"]' in text


def test_importing_tes_main_does_not_start_the_command() -> None:
    proc = _run(
        """
        import warnings
        warnings.simplefilter("ignore")
        import tes.__main__  # would block on the dashboard if it ran main()
        """
    )
    assert proc.returncode == 0, proc.stderr
