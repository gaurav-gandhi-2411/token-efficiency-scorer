# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""scripts/pull_corpora.py writes records derived from real sessions. It must never write into a
path git tracks, nor into a path inside a repo that is not git-ignored (one `git add` from being
committed). The guard resolves `..` and symlinks first, so these tests use throwaway git repos
(hermetic: no dependence on this checkout's index) plus one check on the real default path."""

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
pull_corpora = importlib.import_module("pull_corpora")


@pytest.fixture(autouse=True)
def _isolated_git_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # No user/system git config can change ignore or safe-directory behaviour.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "empty.gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    (r / ".gitignore").write_text("data/corpus_pool/\n", encoding="utf-8")
    (r / "tracked.jsonl").write_text("{}\n", encoding="utf-8")
    (r / "data").mkdir()
    (r / "data" / "corpus_pool").mkdir()
    _git(r, "add", ".gitignore", "tracked.jsonl")
    return r


def test_default_output_is_git_ignored_in_this_repo() -> None:
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    rel = pull_corpora._DEFAULT_OUTPUT.relative_to(ROOT).as_posix()
    result = subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=ROOT, check=False)
    assert result.returncode == 0, f"{rel} must be listed in .gitignore"


def test_default_output_passes_the_guard_with_the_real_gitignore(
    repo: Path,
) -> None:
    shutil.copyfile(ROOT / ".gitignore", repo / ".gitignore")
    rel = pull_corpora._DEFAULT_OUTPUT.relative_to(ROOT)
    out = repo / rel
    assert pull_corpora.check_output_path(out) == out.resolve()


def test_ignored_path_is_allowed(repo: Path) -> None:
    out = repo / "data" / "corpus_pool" / "pool.jsonl"
    assert pull_corpora.check_output_path(out) == out.resolve()


def test_path_outside_any_repo_is_allowed(tmp_path: Path) -> None:
    out = tmp_path / "elsewhere" / "pool.jsonl"
    assert pull_corpora.check_output_path(out) == out.resolve()


def test_tracked_path_is_refused(repo: Path) -> None:
    with pytest.raises(ValueError, match="tracked by git"):
        pull_corpora.check_output_path(repo / "tracked.jsonl")


def test_unignored_path_inside_repo_is_refused_with_a_next_step(repo: Path) -> None:
    with pytest.raises(ValueError, match=r"not git-ignored.*--output"):
        pull_corpora.check_output_path(repo / "pool.jsonl")


def test_dotdot_traversal_is_resolved_before_the_check(repo: Path) -> None:
    # Spelled as if it were under the ignored dir; really the tracked file.
    sneaky = repo / "data" / "corpus_pool" / ".." / ".." / "tracked.jsonl"
    with pytest.raises(ValueError, match="tracked by git"):
        pull_corpora.check_output_path(sneaky)


def test_dotdot_into_unignored_dir_is_refused(repo: Path) -> None:
    sneaky = repo / "data" / "corpus_pool" / ".." / "new.jsonl"
    with pytest.raises(ValueError, match="not git-ignored"):
        pull_corpora.check_output_path(sneaky)


def test_symlink_in_ignored_dir_pointing_at_a_tracked_file_is_refused(repo: Path) -> None:
    link = repo / "data" / "corpus_pool" / "pool.jsonl"
    try:
        link.symlink_to(repo / "tracked.jsonl")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")
    with pytest.raises(ValueError, match="tracked by git"):
        pull_corpora.check_output_path(link)


def test_symlinked_dir_escaping_the_ignored_area_is_refused(repo: Path) -> None:
    # data/corpus_pool/out -> repo root: the real target (repo/pool.jsonl) is not ignored.
    link = repo / "data" / "corpus_pool" / "out"
    try:
        link.symlink_to(repo, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")
    with pytest.raises(ValueError, match="not git-ignored"):
        pull_corpora.check_output_path(link / "pool.jsonl")


def test_main_refuses_before_pulling_anything_and_writes_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _must_not_run(*_a: object, **_k: object) -> None:
        raise AssertionError("pull started despite an unsafe --output")

    monkeypatch.setattr(pull_corpora, "process_local", _must_not_run)
    target = repo / "tracked.jsonl"
    before = target.read_bytes()
    monkeypatch.setattr(
        sys, "argv", ["pull_corpora.py", "--projects", "alpha", "--output", str(target)]
    )
    with pytest.raises(SystemExit) as exc:
        pull_corpora.main()
    assert "refusing to write output" in str(exc.value)
    assert target.read_bytes() == before


def test_dry_run_does_not_need_a_safe_output(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Dry-run never writes, so the guard does not apply; stub the pulls to stay offline.
    monkeypatch.setattr(pull_corpora, "process_local", lambda *a, **k: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pull_corpora.py",
            "--projects",
            "alpha",
            "--dry-run",
            "--skip-public",
            "--output",
            str(repo / "tracked.jsonl"),
        ],
    )
    pull_corpora.main()
    assert (repo / "tracked.jsonl").read_text(encoding="utf-8") == "{}\n"
