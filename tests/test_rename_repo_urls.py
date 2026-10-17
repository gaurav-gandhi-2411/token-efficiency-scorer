# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

"""scripts/rename_repo_urls.py rewrites the old slug, keeps line endings, and is idempotent.

Every test builds a throwaway git repository under tmp_path; nothing touches this checkout.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rename_repo_urls.py"
_spec = importlib.util.spec_from_file_location("rename_repo_urls", SCRIPT)
assert _spec and _spec.loader
rr = importlib.util.module_from_spec(_spec)
sys.modules["rename_repo_urls"] = rr
_spec.loader.exec_module(rr)

OWNER = "octo"
OLD = "old-name"
NEW = "new-name"


def _git(root: Path, *args: str) -> None:
    env = {
        **os.environ,
        "HOME": str(root),
        "USERPROFILE": str(root),
        "GIT_CONFIG_GLOBAL": str(root / "gitconfig"),
        "GIT_CONFIG_SYSTEM": str(root / "gitconfig-system"),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.org",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.org",
    }
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    files = {
        "README.md": (
            f"[home](https://github.com/{OWNER}/{OLD})\r\n"
            f"![x](https://raw.githubusercontent.com/{OWNER}/{OLD}/main/a.png)\r\n"
            f"pip install git+https://github.com/{OWNER}/{OLD}.git\r\n"
        ),
        "RELEASING.md": f"gh run list --repo {OWNER}/{OLD} --limit 1\ngit clone git@github.com:{OWNER}/{OLD}.git\ncd {OLD}\n",
        "pyproject.toml": f'Homepage = "https://github.com/{OWNER}/{OLD}"\nOther = "https://github.com/{OWNER}/{OLD}-extras"\n',
        "docs/site.md": f"https://{OWNER}.github.io/{OLD}/guide\n",
        "CHANGELOG.md": f"[0.1]: https://github.com/{OWNER}/{OLD}/compare/v0.0...v0.1\n",
        "research/note.md": f"see https://github.com/{OWNER}/{OLD}/blob/main/x.md\n",
        "data/log.jsonl": f'{{"path": "/home/u/{OLD}/x"}}\n',
        "notes.md": f"the {OLD} project, not a link\n",
        "docs/RENAME_REPO.md": f"quotes the old slug {OWNER}/{OLD} on purpose\n",
        "pic.png": "\0\0binary " + f"{OWNER}/{OLD}",
    }
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode("utf-8", errors="surrogateescape"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _run(root: Path, *flags: str) -> int:
    return rr.main(["--owner", OWNER, "--old", OLD, "--new", NEW, "--root", str(root), *flags])


def test_check_fails_while_the_old_slug_is_in_live_files(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(repo, "--check") == 1
    out = capsys.readouterr().out
    assert "README.md:1" in out
    assert "pyproject.toml:1" in out
    assert "CHANGELOG.md" not in out  # historical: counted, not listed as a failure
    assert "research/note.md" not in out


def test_apply_rewrites_every_form_and_keeps_line_endings(repo: Path) -> None:
    assert _run(repo, "--apply") == 0
    readme = (repo / "README.md").read_bytes().decode()
    assert readme.count("\r\n") == 3 and "\n" not in readme.replace("\r\n", "")
    assert f"github.com/{OWNER}/{NEW})" in readme
    assert f"raw.githubusercontent.com/{OWNER}/{NEW}/main/a.png" in readme
    assert f"git+https://github.com/{OWNER}/{NEW}.git" in readme
    rel = (repo / "RELEASING.md").read_text(encoding="utf-8")
    assert f"--repo {OWNER}/{NEW} " in rel
    assert f"git@github.com:{OWNER}/{NEW}.git" in rel
    assert rel.endswith(f"cd {NEW}\n")
    assert f"{OWNER}.github.io/{NEW}/guide" in (repo / "docs/site.md").read_text(encoding="utf-8")


def test_a_longer_name_that_starts_with_the_old_one_is_not_touched(repo: Path) -> None:
    _run(repo, "--apply")
    text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    assert f"github.com/{OWNER}/{OLD}-extras" in text
    assert f'github.com/{OWNER}/{NEW}"' in text


def test_historical_files_binary_files_and_bare_mentions_are_left_alone(repo: Path) -> None:
    before = {
        rel: (repo / rel).read_bytes()
        for rel in ("CHANGELOG.md", "research/note.md", "data/log.jsonl", "notes.md", "pic.png")
    }
    _run(repo, "--apply")
    for rel, data in before.items():
        assert (repo / rel).read_bytes() == data, rel


def test_apply_is_idempotent_and_check_passes_afterwards(repo: Path) -> None:
    assert _run(repo, "--apply") == 0
    snapshot = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert _run(repo, "--apply") == 0
    assert {p: p.read_bytes() for p in snapshot} == snapshot
    assert _run(repo, "--check") == 0


def test_include_historical_rewrites_the_records_too(repo: Path) -> None:
    assert _run(repo, "--apply", "--include-historical") == 0
    assert f"{OWNER}/{NEW}/compare" in (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"{OWNER}/{NEW}/blob" in (repo / "research/note.md").read_text(encoding="utf-8")


def test_inventory_lists_file_line_and_replacement(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(repo, "--inventory") == 0
    out = capsys.readouterr().out
    assert f"| `README.md:1` | slug | `{OWNER}/{OLD}` | {OWNER}/{NEW} |" in out
    assert "`notes.md:1` | bare" in out
    assert "`CHANGELOG.md` lines 1" in out


def test_a_non_git_directory_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "x.md").write_text(f"{OWNER}/{OLD}\n", encoding="utf-8")
    assert _run(tmp_path, "--check") == 2
    assert "could not read" in capsys.readouterr().err


def test_identical_old_and_new_is_refused(repo: Path) -> None:
    assert (
        rr.main(["--owner", OWNER, "--old", OLD, "--new", OLD, "--root", str(repo), "--check"]) == 2
    )


def test_the_rename_checklist_itself_is_never_scanned_or_rewritten(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (repo / "docs" / "RENAME_REPO.md").read_bytes()
    assert _run(repo, "--inventory", "--include-historical") == 0
    assert "RENAME_REPO" not in capsys.readouterr().out
    _run(repo, "--apply", "--include-historical")
    assert (repo / "docs" / "RENAME_REPO.md").read_bytes() == before
