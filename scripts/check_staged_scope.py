# ruff: noqa: E402 -- pending PR #45's repo-wide E402 config exemption
# landing on master (see that PR for the full reasoning); until then this
# matches every other file's existing pattern under the same constraint.
from __future__ import annotations

"""scripts/check_staged_scope.py — AX2.2: pre-commit guard against an
accidentally-broad `git add`.

A pre-commit hook cannot know a PR's *intended* scope -- that's a human
decision, not something git-visible. What it CAN check is a cheap proxy that
would have caught both of this session's real near-misses (2026-08-21,
CLAUDE.md rule 39c): staging a suspiciously large number of files relative to
what a single deliberate commit normally touches. Not a hard cap -- some
commits (a repo-wide mechanical lint pass, a codemod) genuinely need to touch
many files; those opt in explicitly via LARGE_COMMIT_OK=1 rather than being
permanently blocked.
"""

import os
import subprocess
import sys

THRESHOLD = 20


def main() -> int:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True,
        text=True,
        check=True,
    )
    staged = [line for line in result.stdout.splitlines() if line.strip()]

    if len(staged) <= THRESHOLD:
        return 0

    if os.environ.get("LARGE_COMMIT_OK") == "1":
        print(f"[check_staged_scope] {len(staged)} files staged (LARGE_COMMIT_OK=1 set, allowing).")
        return 0

    print(
        f"[check_staged_scope] {len(staged)} files staged -- more than {THRESHOLD}, "
        "the usual size for a single deliberate change.\n"
        "If this is genuinely intentional (a repo-wide mechanical pass, a codemod), "
        "re-run with LARGE_COMMIT_OK=1 set.\n"
        "If it's not what you meant to commit, run `git restore --staged <path>` on "
        "whatever shouldn't be here and re-commit.\n\n"
        "Staged files:",
        file=sys.stderr,
    )
    for path in staged:
        print(f"  {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
