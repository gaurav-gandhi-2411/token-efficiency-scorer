"""Every place tracegauge's version lives must agree, with no hardcoded copy to go stale.

``pyproject.toml`` ``[project].version`` is the single source. ``tes.__version__`` is read from the
installed metadata, ``uv.lock`` carries its own entry for the project, and ``CHANGELOG.md``'s first
``## [x.y.z]`` heading must be the release being prepared. Until 0.14.0 ``tests/test_packaging.py``
asserted the version as a literal string, which RELEASING.md step 1 called out as the thing that
"silently goes stale if you forget it"; that literal is gone and this module replaces it with a
comparison that needs no edit at release time.

If this fails right after bumping ``pyproject.toml`` locally with ``installed != pyproject``, the venv's
editable install still carries the old version: run ``uv sync`` (CI always syncs fresh).
"""

from __future__ import annotations

import re
from importlib.metadata import version as installed_version
from pathlib import Path

import tes

_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    table = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    assert table, "pyproject.toml has no [project] table"
    m = re.search(r'^version\s*=\s*"([^"]+)"\s*$', table.group(1), re.M)
    assert m, "pyproject.toml [project] has no static version"
    return m[1]


def test_installed_metadata_and_dunder_version_equal_pyproject():
    expected = _pyproject_version()

    assert installed_version("tracegauge") == expected, (
        "installed tracegauge metadata differs from pyproject.toml: run `uv sync`"
    )
    assert tes.__version__ == expected


def test_uv_lock_carries_the_same_version():
    text = (_ROOT / "uv.lock").read_text(encoding="utf-8")
    found = re.findall(
        r'^\[\[package\]\]\r?\nname = "tracegauge"\r?\nversion = "([^"]+)"', text, re.M
    )

    assert found == [_pyproject_version()]


def test_changelogs_top_release_heading_is_this_version():
    text = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## \[(\d[^\]\s]*)\]", text, re.M)

    assert m, "CHANGELOG.md has no `## [x.y.z]` release heading"
    assert m[1] == _pyproject_version(), (
        "CHANGELOG.md's newest release heading is not the version in pyproject.toml: "
        "a version bump needs its CHANGELOG entry (RELEASING.md step 1)"
    )
