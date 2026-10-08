"""scripts/check_release_version.py: the release must not go out with disagreeing versions.

Run against tiny synthetic trees (so every disagreement is constructed on purpose) and once against
the real tree, where the three sources must agree today. Nothing here touches the network: the PyPI
lookup is injected.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_release_version", _ROOT / "scripts" / "check_release_version.py"
)
assert _spec and _spec.loader
crv = importlib.util.module_from_spec(_spec)
sys.modules["check_release_version"] = crv
_spec.loader.exec_module(crv)


def _tree(tmp_path: Path, pyproject="1.2.3", lock="1.2.3", changelog="1.2.3") -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f'[build-system]\nrequires = ["x"]\n\n[project]\nname = "tracegauge"\n'
        f'version = "{pyproject}"\n\n[tool.other]\nversion = "0.0.1"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "flask"\nversion = "9.9.9"\n\n'
        f'[[package]]\nname = "tracegauge"\nversion = "{lock}"\nsource = {{ editable = "." }}\n',
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [Unreleased]\n\n## [{changelog}] - a release\n\n## [0.0.1]\n",
        encoding="utf-8",
    )
    return tmp_path


def test_the_real_tree_is_consistent_today():
    assert crv.check(_ROOT) == []


def test_all_sources_and_the_tag_agree(tmp_path):
    assert crv.check(_tree(tmp_path), tag="v1.2.3") == []


def test_a_version_key_in_another_table_is_not_the_project_version(tmp_path):
    # [tool.other] comes after [project] and carries its own `version`; only [project]'s counts
    assert crv.pyproject_version(_tree(tmp_path)) == "1.2.3"


@pytest.mark.parametrize(
    ("kwargs", "named"),
    [
        ({"pyproject": "1.2.4"}, "pyproject.toml=1.2.4"),
        ({"lock": "1.2.2"}, "uv.lock=1.2.2"),
        ({"changelog": "1.2.2"}, "CHANGELOG.md=1.2.2"),
    ],
)
def test_any_one_source_drifting_is_refused_and_named(tmp_path, kwargs, named):
    problems = crv.check(_tree(tmp_path, **kwargs))

    assert len(problems) == 1 and "disagree" in problems[0] and named in problems[0]


def test_a_bump_with_no_changelog_entry_is_refused(tmp_path):
    # the 0.11.0-style miss: pyproject and lock bumped, CHANGELOG still on the previous release
    tree = _tree(tmp_path, pyproject="1.3.0", lock="1.3.0", changelog="1.2.3")

    problems = crv.check(tree)

    assert len(problems) == 1 and "CHANGELOG.md=1.2.3" in problems[0]


def test_a_tag_that_does_not_match_the_version_is_refused(tmp_path):
    problems = crv.check(_tree(tmp_path), tag="v1.2.4")

    assert problems == ["tag v1.2.4 does not match the package version 1.2.3"]


@pytest.mark.parametrize("tag", ["1.2.3", "release-1.2.3", "v", ""])
def test_a_malformed_tag_is_refused(tmp_path, tag):
    problems = crv.check(_tree(tmp_path), tag=tag)

    assert problems and "not of the form" in problems[0]


def test_a_prerelease_tag_must_equal_the_prerelease_version(tmp_path):
    tree = _tree(tmp_path, pyproject="1.3.0rc1", lock="1.3.0rc1", changelog="1.3.0rc1")

    assert crv.check(tree, tag="v1.3.0rc1") == []
    assert crv.check(tree, tag="v1.3.0") != []


def test_a_version_already_on_pypi_is_refused(tmp_path):
    problems = crv.check(_tree(tmp_path), pypi=lambda v: True)

    assert problems == ["tracegauge 1.2.3 is already on PyPI; bump the version"]


def test_a_version_not_on_pypi_passes_the_pypi_check(tmp_path):
    seen: list[str] = []

    def fake(v: str) -> bool:
        seen.append(v)
        return False

    assert crv.check(_tree(tmp_path), pypi=fake) == []
    assert seen == ["1.2.3"]


def test_every_problem_is_reported_not_just_the_first(tmp_path):
    problems = crv.check(_tree(tmp_path, lock="1.2.4"), tag="v9.9.9", pypi=lambda v: True)

    assert len(problems) == 3


# --- fail closed: what cannot be read is not a pass --------------------------------------------------


def test_a_changelog_with_no_release_heading_is_cannot_determine(tmp_path):
    tree = _tree(tmp_path)
    (tree / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n", encoding="utf-8")

    with pytest.raises(crv.CannotDetermine):
        crv.check(tree)


def test_a_missing_lock_entry_is_cannot_determine(tmp_path):
    tree = _tree(tmp_path)
    (tree / "uv.lock").write_text('version = 1\n\n[[package]]\nname = "flask"\nversion = "1"\n')

    with pytest.raises(crv.CannotDetermine):
        crv.check(tree)


def test_a_duplicated_lock_entry_is_cannot_determine(tmp_path):
    tree = _tree(tmp_path)
    entry = '[[package]]\nname = "tracegauge"\nversion = "1.2.3"\n'
    (tree / "uv.lock").write_text(f"version = 1\n\n{entry}\n{entry}", encoding="utf-8")

    with pytest.raises(crv.CannotDetermine, match="found 2"):
        crv.check(tree)


def test_a_pyproject_without_a_project_version_is_cannot_determine(tmp_path):
    tree = _tree(tmp_path)
    (tree / "pyproject.toml").write_text('[project]\nname = "tracegauge"\n', encoding="utf-8")

    with pytest.raises(crv.CannotDetermine):
        crv.check(tree)


def test_an_unreadable_tree_is_cannot_determine(tmp_path):
    with pytest.raises(crv.CannotDetermine):
        crv.check(tmp_path)  # empty directory


def test_cli_exit_codes(tmp_path, capsys):
    good = _tree(tmp_path)
    assert crv.main(["--root", str(good), "--tag", "v1.2.3"]) == 0
    assert "release version OK: 1.2.3 (tag v1.2.3)" in capsys.readouterr().out

    assert crv.main(["--root", str(good), "--tag", "v1.2.4"]) == 1
    assert "::error::tag v1.2.4 does not match" in capsys.readouterr().out

    assert crv.main(["--root", str(tmp_path / "nowhere")]) == 2
    assert "cannot determine" in capsys.readouterr().out


def test_pypi_lookup_failures_other_than_404_are_cannot_determine(monkeypatch):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", 503, "unavailable", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(crv.urllib.request, "urlopen", boom)
    with pytest.raises(crv.CannotDetermine, match="503"):
        crv.pypi_has("1.2.3")

    def not_found(*a, **k):
        raise urllib.error.HTTPError("u", 404, "nope", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(crv.urllib.request, "urlopen", not_found)
    assert crv.pypi_has("1.2.3") is False

    def offline(*a, **k):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(crv.urllib.request, "urlopen", offline)
    with pytest.raises(crv.CannotDetermine, match="unreachable"):
        crv.pypi_has("1.2.3")
