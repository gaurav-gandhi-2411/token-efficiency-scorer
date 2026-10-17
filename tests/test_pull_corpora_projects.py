# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "pull_corpora_under_test", ROOT / "scripts" / "pull_corpora.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # why: @dataclass looks the module up by name
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The script module with its projects root redirected into a throwaway directory."""
    module = _load()
    root = tmp_path / "projects"
    (root / "alpha").mkdir(parents=True)
    (root / "beta").mkdir()
    monkeypatch.setattr(module, "_LOCAL_PROJECTS_ROOT", root)
    return module


def test_refuses_without_projects_or_all(pc: ModuleType) -> None:
    with pytest.raises(SystemExit) as exc:
        pc.main(["--dry-run"])
    assert exc.value.code == 2


def test_all_requires_yes_and_reads_nothing(
    pc: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        pc.main(["--all", "--skip-public", "--dry-run"])
    assert "--yes" in str(exc.value)
    assert "2 project(s)" in capsys.readouterr().err


def test_projects_and_all_are_mutually_exclusive(pc: ModuleType) -> None:
    with pytest.raises(SystemExit) as exc:
        pc.main(["--projects", "alpha", "--all", "--yes"])
    assert exc.value.code == 2


def test_unknown_project_is_an_error_not_a_skip(pc: ModuleType) -> None:
    with pytest.raises(SystemExit) as exc:
        pc.main(["--projects", "alpha", "typo", "--skip-public", "--dry-run"])
    assert "typo" in str(exc.value)


def test_only_named_projects_are_listed(pc: ModuleType) -> None:
    assert [p.name for p in pc._list_project_dirs(["beta"])] == ["beta"]
    assert [p.name for p in pc._list_project_dirs(None)] == ["alpha", "beta"]


def test_content_warning_is_printed(pc: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    pc.main(["--projects", "alpha", "--skip-public", "--dry-run"])
    assert "never be committed" in capsys.readouterr().err


def test_default_output_is_git_ignored() -> None:
    if shutil.which("git") is None:
        pytest.skip("git not available")
    default = _load()._DEFAULT_OUTPUT
    rel = default.relative_to(ROOT)
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", str(rel)], cwd=ROOT, capture_output=True, check=False
    )
    if result.returncode == 128:
        pytest.skip("not a git checkout")
    assert result.returncode == 0, f"{rel} is not git-ignored"
