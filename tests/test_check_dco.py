# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("check_dco", ROOT / "scripts" / "check_dco.py")
assert _SPEC is not None and _SPEC.loader is not None
dco = importlib.util.module_from_spec(_SPEC)
sys.modules["check_dco"] = dco
_SPEC.loader.exec_module(dco)

ME = "Dev One <dev@example.org>"


def _commit(
    msg: str,
    *,
    an: str = "Dev One",
    ae: str = "dev@example.org",
    parents: int = 1,
) -> object:
    return dco.Commit("a" * 40, parents, an, ae, an, ae, msg)


def test_valid_signoff_passes() -> None:
    assert dco.has_valid_signoff(_commit(f"feat: x\n\nbody\n\nSigned-off-by: {ME}\n"))


def test_missing_signoff_fails() -> None:
    assert not dco.has_valid_signoff(_commit("feat: x\n\nbody\n"))


@pytest.mark.parametrize(
    "line",
    [
        "Signed-off-by: Dev One",  # no address
        "Signed-off-by: <dev@example.org>",  # no name
        "Signed-off-by: Dev One <not-an-email>",
        "signed-off-by: Dev One <dev@example.org> trailing",  # wrong case and trailing text
        "Signed-off-by:Dev One <dev@example.org>",  # no space after the colon
    ],
)
def test_malformed_signoff_fails(line: str) -> None:
    assert not dco.has_valid_signoff(_commit(f"feat: x\n\n{line}\n"))


def test_signoff_by_someone_else_fails() -> None:
    assert not dco.has_valid_signoff(
        _commit("feat: x\n\nSigned-off-by: Other <other@example.org>\n")
    )


def test_email_match_is_case_insensitive() -> None:
    assert dco.has_valid_signoff(_commit("feat: x\n\nSigned-off-by: Dev One <DEV@Example.org>\n"))


def test_merge_and_bot_commits_are_skipped() -> None:
    merge = _commit("Merge branch x", parents=2)
    bot = _commit(
        "build(deps): bump",
        an="dependabot[bot]",
        ae="49699333+dependabot[bot]@users.noreply.github.com",
    )
    plain = _commit("feat: no signoff")
    assert dco.failing_commits([merge, bot, plain]) == [plain]


def _git(repo: Path, *args: str, **extra: str) -> str:
    # Every root redirected into tmp_path so the test never reads or writes real git config.
    env = {
        **os.environ,
        "HOME": str(repo),
        "USERPROFILE": str(repo),
        "GIT_CONFIG_GLOBAL": str(repo / "gitconfig"),
        "GIT_CONFIG_SYSTEM": str(repo / "gitconfig-system"),
        "GIT_AUTHOR_NAME": "Dev One",
        "GIT_AUTHOR_EMAIL": "dev@example.org",
        "GIT_COMMITTER_NAME": "Dev One",
        "GIT_COMMITTER_EMAIL": "dev@example.org",
        **extra,
    }
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "f.txt").write_text("0\n")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-q", "-m", "base commit, not checked")
    return r


def _add(repo: Path, n: int, msg: str, name: str = "f.txt", **env: str) -> None:
    (repo / name).write_text(f"{n}\n")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", msg, **env)


def test_real_git_range_good_passes_and_bad_fails(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _add(repo, 1, f"feat: signed\n\nSigned-off-by: {ME}")
    good_head = _git(repo, "rev-parse", "HEAD")
    assert dco.failing_commits(dco.read_range(base, good_head, cwd=str(repo))) == []

    _add(repo, 2, "feat: forgot the sign-off")
    commits = dco.read_range(base, "HEAD", cwd=str(repo))
    assert [c.message.splitlines()[0] for c in dco.failing_commits(commits)] == [
        "feat: forgot the sign-off"
    ]


def test_real_git_range_skips_merge_and_dependabot(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "side")
    bot = "49699333+dependabot[bot]@users.noreply.github.com"
    _add(
        repo,
        1,
        "build(deps): bump x",
        "g.txt",
        GIT_AUTHOR_NAME="dependabot[bot]",
        GIT_AUTHOR_EMAIL=bot,
        GIT_COMMITTER_NAME="dependabot[bot]",
        GIT_COMMITTER_EMAIL=bot,
    )
    _git(repo, "checkout", "-q", "main")
    _add(repo, 2, f"feat: main work\n\nSigned-off-by: {ME}")
    (repo / "f.txt").write_text("merged\n")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    commits = dco.read_range(base, "HEAD", cwd=str(repo))
    assert any(c.parents == 2 for c in commits)
    assert dco.failing_commits(commits) == []


def test_main_exit_codes(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _add(repo, 1, "feat: unsigned")
    monkeypatch.chdir(repo)
    assert dco.main(["--base", base, "--head", "HEAD"]) == 1
    assert "FAIL" in capsys.readouterr().out
    assert dco.main(["--base", "no-such-ref", "--head", "HEAD"]) == 2  # fail closed
    assert dco.main(["--base", "HEAD", "--head", "HEAD"]) == 0  # empty range


BOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"


def _bot_commit(**kw: str) -> object:
    fields = {
        "an": "dependabot[bot]",
        "ae": BOT_EMAIL,
        "cn": "dependabot[bot]",
        "ce": BOT_EMAIL,
    }
    fields.update(kw)
    return dco.Commit(
        "b" * 40, 1, fields["an"], fields["ae"], fields["cn"], fields["ce"], "build(deps): bump x"
    )


def test_a_real_bot_identity_is_exempt() -> None:
    assert dco.failing_commits([_bot_commit()]) == []


def test_a_bot_authored_commit_merged_through_the_github_ui_is_exempt() -> None:
    web = _bot_commit(cn="GitHub", ce="noreply@github.com")
    assert dco.failing_commits([web]) == []


def test_a_name_ending_in_bot_alone_does_not_exempt() -> None:
    spoof = _bot_commit(ae="dev@example.org", ce="dev@example.org")
    assert dco.failing_commits([spoof]) == [spoof]


def test_a_bot_name_with_a_lookalike_email_does_not_exempt() -> None:
    for email in (
        "dependabot[bot]@users.noreply.github.com",  # no numeric id
        "49699333+dependabot[bot]@users.noreply.github.com.evil.example",
        "49699333+dependabot[bot]@example.org",
        "49699333+dependabot@users.noreply.github.com",  # not a [bot] account
    ):
        spoof = _bot_commit(ae=email, ce=email)
        assert dco.failing_commits([spoof]) == [spoof], email


def test_a_bot_email_with_a_human_name_does_not_exempt() -> None:
    spoof = _bot_commit(an="Dev One", cn="Dev One")
    assert dco.failing_commits([spoof]) == [spoof]


def test_a_human_committer_on_a_bot_authored_commit_does_not_exempt() -> None:
    spoof = _bot_commit(cn="Dev One", ce="dev@example.org")
    assert dco.failing_commits([spoof]) == [spoof]
