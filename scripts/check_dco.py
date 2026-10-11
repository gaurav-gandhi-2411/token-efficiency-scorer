#!/usr/bin/env python3
# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0
"""Fail if a commit in a range lacks a valid ``Signed-off-by`` trailer (Developer Certificate of Origin).

    python scripts/check_dco.py --base <base-sha> --head <head-sha>

Checks every commit reachable from ``--head`` and not from ``--base``. A sign-off is valid when a
line ``Signed-off-by: Name <email>`` is present and its email equals the commit's author or committer
email (case-insensitive), the same bar the DCO GitHub app applies. Merge commits and commits by bots
(an author or committer name ending in ``[bot]``, e.g. dependabot) are skipped. Exit 0 when all
commits pass, 1 when any fails (each failure is listed), 2 on a git error.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

# Name, then an address in angle brackets; one line, nothing after it.
SIGNOFF_RE = re.compile(
    r"^Signed-off-by:[ \t]+(?P<name>\S.*?)[ \t]+<(?P<email>[^<>@\s]+@[^<>\s]+)>[ \t]*$"
)
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"
_FORMAT = _FIELD_SEP.join(["%H", "%P", "%an", "%ae", "%cn", "%ce", "%B"]) + _RECORD_SEP


@dataclass(frozen=True)
class Commit:
    """The parts of a commit the DCO check needs."""

    sha: str
    parents: int
    author_name: str
    author_email: str
    committer_name: str
    committer_email: str
    message: str


def parse_log(raw: str) -> list[Commit]:
    """Parse ``git log --format=_FORMAT`` output into commits."""
    commits: list[Commit] = []
    for record in raw.split(_RECORD_SEP):
        if not record.strip():
            continue
        sha, parents, an, ae, cn, ce, body = record.strip("\n").split(_FIELD_SEP, 6)
        commits.append(Commit(sha, len(parents.split()), an, ae, cn, ce, body))
    return commits


def is_bot(commit: Commit) -> bool:
    """True for bot-authored commits such as ``dependabot[bot]``."""
    return commit.author_name.endswith("[bot]") or commit.committer_name.endswith("[bot]")


def has_valid_signoff(commit: Commit) -> bool:
    """True if a well-formed Signed-off-by line matches the author or committer email."""
    allowed = {commit.author_email.lower(), commit.committer_email.lower()}
    for line in commit.message.splitlines():
        match = SIGNOFF_RE.match(line.strip())
        if match and match.group("email").lower() in allowed:
            return True
    return False


def failing_commits(commits: list[Commit]) -> list[Commit]:
    """Commits that need a sign-off and do not have a valid one."""
    return [c for c in commits if c.parents <= 1 and not is_bot(c) and not has_valid_signoff(c)]


def read_range(base: str, head: str, *, cwd: str | None = None) -> list[Commit]:
    """Commits in ``base..head`` (oldest first), via git; raises on git failure."""
    out = subprocess.run(  # noqa: S603 -- fixed argv, no shell; refs are passed as data
        ["git", "log", "--reverse", f"--format={_FORMAT}", f"{base}..{head}"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
    ).stdout
    return parse_log(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="exclusive lower bound (e.g. PR base sha)")
    parser.add_argument("--head", default="HEAD", help="inclusive upper bound (default HEAD)")
    args = parser.parse_args(argv)
    try:
        commits = read_range(args.base, args.head)
    except (subprocess.CalledProcessError, OSError) as exc:
        # Fail closed: a range we could not read is not a pass.
        print(f"check_dco: could not read {args.base}..{args.head}: {exc}", file=sys.stderr)
        return 2
    if not commits:
        print(f"check_dco: no commits in {args.base}..{args.head}; nothing to check")
        return 0
    bad = failing_commits(commits)
    for commit in bad:
        print(
            f"FAIL {commit.sha[:12]} ({commit.author_name} <{commit.author_email}>): "
            "missing a valid 'Signed-off-by: Name <email>' trailer"
        )
    checked = sum(1 for c in commits if c.parents <= 1 and not is_bot(c))
    print(f"check_dco: {len(commits)} commit(s) in range, {checked} checked, {len(bad)} failing")
    if bad:
        print("Fix: git rebase --signoff <base>   (or amend with: git commit --amend -s)")
        print("See CONTRIBUTING.md, section 'Sign your commits (DCO)'.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
