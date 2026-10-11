"""Session-wide pytest hooks."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests._checkout_guard import enforce

_REPO_ROOT = Path(__file__).resolve().parent.parent


def pytest_sessionstart(session: pytest.Session) -> None:
    # Fail fast, once, before any test runs -- see tests/_checkout_guard.py for why.
    enforce(_REPO_ROOT)


@pytest.fixture(autouse=True, scope="session")
def _isolate_ambient_store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point TES_DB_PATH/HOME/USERPROFILE at a throwaway dir so the environment's own store
    (e.g. a shared TES_DB_PATH holding the quickstart sample session, or ~/.tes/tes.db) can
    never change a test outcome. Session-scoped on purpose: class/module-scoped fixtures (e.g.
    the clustering ones) are built before any function-scoped autouse fixture would run. Tests
    that set their own path via monkeypatch still win. Set TES_TEST_USE_AMBIENT_STORE=1 to
    deliberately run the real-corpus checks (tests/test_cluster_validity.py) against this
    machine's own store."""
    if os.environ.get("TES_TEST_USE_AMBIENT_STORE") == "1":
        yield
        return
    home = tmp_path_factory.mktemp("isolated_home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TES_DB_PATH", str(home / "tes.db"))
        mp.setenv("HOME", str(home))
        mp.setenv("USERPROFILE", str(home))
        yield
