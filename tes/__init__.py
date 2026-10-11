# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Deprecated alias: ``tes`` is ``tracegauge``. Import ``tracegauge`` instead.

The package was renamed from ``tes`` to ``tracegauge`` in 0.16. This module keeps existing
``import tes`` / ``from tes.x import y`` / ``python -m tes`` working for two minor versions (0.16 and
0.17) and is removed in 0.18.

It is an alias, not a copy: importing this module replaces ``sys.modules["tes"]`` with the real
``tracegauge`` module, and a meta-path finder serves every ``tes.<sub>`` import from the already-
imported ``tracegauge.<sub>`` module object. So ``tes.cost is tracegauge.cost``, a monkeypatch
through either name is seen through both, and ``isinstance`` checks keep working. Only this
importing of ``tes`` warns; ``import tracegauge`` never does.
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import warnings
from types import ModuleType
from typing import Any

_OLD = "tes"
_NEW = "tracegauge"
MESSAGE = "tes is deprecated, import tracegauge; removed in 0.18."

# What ``python -m tes`` runs: the real command line, exactly as ``python -m tracegauge``. Guarded
# like tracegauge/__main__.py: merely importing tes.__main__ must not start the command.
_MAIN_SOURCE = "from tracegauge.cli import main\nif __name__ == '__main__':\n    main()\n"


class _MainLoader(importlib.abc.Loader):
    """Serve ``tes.__main__`` (what ``python -m tes`` imports) with the deprecated entry point."""

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return None

    def exec_module(self, module: ModuleType) -> None:
        exec(compile(_MAIN_SOURCE, f"<{_OLD}.__main__>", "exec"), module.__dict__)

    def get_code(self, fullname: str) -> Any:
        return compile(_MAIN_SOURCE, f"<{_OLD}.__main__>", "exec")


class _AliasLoader(importlib.abc.Loader):
    """Load ``tes.<sub>`` as the existing ``tracegauge.<sub>`` module object."""

    def __init__(self, target: str) -> None:
        self._target = target
        self._real_spec: importlib.machinery.ModuleSpec | None = None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        module = importlib.import_module(self._target)
        # Remember the real spec now: module_from_spec overwrites it right after this returns.
        self._real_spec = module.__spec__
        return module

    def exec_module(self, module: ModuleType) -> None:
        # module_from_spec stamped the alias spec onto the real module; put its own spec back so
        # ``tracegauge.<sub>.__spec__`` (used by reload, pickle, importlib.resources) is unchanged.
        if self._real_spec is not None:
            module.__spec__ = self._real_spec


class _AliasFinder(importlib.abc.MetaPathFinder):
    """Resolve ``tes`` and ``tes.<sub>...`` to the matching ``tracegauge`` module."""

    def find_spec(
        self, fullname: str, path: Any = None, target: ModuleType | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        if not fullname.startswith(_OLD + "."):
            return None
        if fullname == _OLD + ".__main__":
            return importlib.machinery.ModuleSpec(fullname, _MainLoader())
        real_name = _NEW + fullname[len(_OLD) :]
        try:
            real = importlib.util.find_spec(real_name)
        except (ImportError, ValueError):
            return None
        if real is None:
            return None
        return importlib.machinery.ModuleSpec(
            fullname,
            _AliasLoader(real_name),
            is_package=real.submodule_search_locations is not None,
        )


def _install() -> None:
    if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _AliasFinder())


_install()
warnings.warn(MESSAGE, DeprecationWarning, stacklevel=2)
# Replace this module with the real one: ``import tes`` now returns ``tracegauge`` itself, and
# ``from tes import X`` reads X off ``tracegauge``.
sys.modules[_OLD] = importlib.import_module(_NEW)
