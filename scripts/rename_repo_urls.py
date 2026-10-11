#!/usr/bin/env python3
# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0
"""Rewrite (or check for) a GitHub repository's old owner/name in the tracked files.

    python scripts/rename_repo_urls.py --new tracegauge --check       # exit 1 if the old slug remains
    python scripts/rename_repo_urls.py --new tracegauge --apply       # rewrite in place
    python scripts/rename_repo_urls.py --new tracegauge --inventory   # markdown table of every hit

What counts as the old name: the slug ``<owner>/<old>`` wherever it appears (github.com and
raw.githubusercontent.com URLs, ``git@github.com:`` and ``.git`` remotes, ``gh --repo`` arguments),
the GitHub Pages form ``<owner>.github.io/<old>``, and a ``cd <old>`` line in a clone instruction.
A bare ``<old>`` elsewhere (a prose mention, a local directory name inside a log) is reported by
``--inventory`` as "manual" and never rewritten: it may be a path on someone's disk, not a link.

Historical records are skipped by default (CHANGELOG.md, docs/releases/, docs/audit/, research/,
data/, the status notes). Reason: after the rename GitHub redirects every old blob, compare, issue
and clone URL (https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository),
so those links keep working, and rewriting a published release entry or a dated research note
would falsify what it said when it was written. ``--include-historical`` rewrites them too.

Line endings are preserved byte for byte (CRLF stays CRLF, LF stays LF); the script only swaps the
matched text, so it is idempotent: a second ``--apply`` changes nothing.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_OWNER = "gaurav-gandhi-2411"
DEFAULT_OLD = "token-efficiency-scorer"

# Prefixes of tracked paths that record history rather than live links.
HISTORICAL_PREFIXES = (
    "CHANGELOG.md",
    "docs/releases/",
    "docs/audit/",
    "research/",
    "data/",
    "CURRENT_STATE.md",
    "PLAN.md",
    "NEXT_PHASE.md",
    "spec.md",
    "docs/RENAME_REPO.md",
)
TEXT_SUFFIXES = {
    ".md", ".py", ".toml", ".yml", ".yaml", ".in", ".txt", ".cfg", ".json", ".html", ".sh", ".svg",
    ".sql", ".ts", ".jsonl", ".log", "",
}  # fmt: skip


@dataclass(frozen=True)
class Hit:
    """One occurrence: where it is, what kind, and the text it would become."""

    path: str
    line: int
    kind: str  # "slug", "pages", "cd" (rewritten) or "bare" (reported only)
    old: str
    new: str


def build_patterns(owner: str, old: str) -> dict[str, re.Pattern[str]]:
    """Regexes for each rewritten form. The trailing guard stops ``old-suffix`` from matching."""
    o, n = re.escape(owner), re.escape(old)
    return {
        "slug": re.compile(rf"(?<![\w.-]){o}/{n}(?![\w-])", re.IGNORECASE),
        "pages": re.compile(rf"(?<![\w.-]){o}\.github\.io/{n}(?![\w-])", re.IGNORECASE),
        "cd": re.compile(rf"(?m)^(?P<lead>[ \t]*cd[ \t]+){n}(?P<tail>[ \t]*\r?)$"),
    }


def replacement(kind: str, owner: str, new_owner: str, new: str, match: re.Match[str]) -> str:
    """Text that replaces ``match`` for the given kind."""
    if kind == "slug":
        return f"{new_owner}/{new}"
    if kind == "pages":
        return f"{new_owner}.github.io/{new}"
    return f"{match.group('lead')}{new}{match.group('tail')}"


def tracked_files(root: Path) -> list[str]:
    """Tracked paths via git; raises (fail closed) if ``root`` is not a git work tree."""
    out = subprocess.run(  # noqa: S603 -- fixed argv, no shell
        ["git", "-C", str(root), "ls-files", "-z"],  # noqa: S607
        check=True,
        capture_output=True,
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def is_historical(path: str) -> bool:
    """True for paths that record history and are skipped by default."""
    return any(path == p or path.startswith(p) for p in HISTORICAL_PREFIXES)


def read_text(path: Path) -> str | None:
    """File text as UTF-8 with line endings untouched, or None for binary/unreadable files."""
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_text(text: str, path: str, owner: str, old: str, new: str, new_owner: str) -> list[Hit]:
    """Every rewrite hit and every bare mention in one file's text."""
    pats = build_patterns(owner, old)
    hits: list[Hit] = []
    covered: list[tuple[int, int]] = []
    for kind, rx in pats.items():
        for m in rx.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            hits.append(
                Hit(path, line, kind, m.group(0), replacement(kind, owner, new_owner, new, m))
            )
            covered.append((m.start(), m.end()))
    bare = re.compile(rf"(?<![\w-]){re.escape(old)}(?![\w-])")
    for m in bare.finditer(text):
        if any(a <= m.start() < b for a, b in covered):
            continue
        line = text.count("\n", 0, m.start()) + 1
        hits.append(Hit(path, line, "bare", m.group(0), "(manual: not a link)"))
    return sorted(hits, key=lambda h: (h.path, h.line, h.kind))


def rewrite_text(text: str, owner: str, old: str, new: str, new_owner: str) -> str:
    """Apply every rewrite pattern; bare mentions are left alone."""
    for kind, rx in build_patterns(owner, old).items():
        text = rx.sub(lambda m, k=kind: replacement(k, owner, new_owner, new, m), text)
    return text


def collect(root: Path, args: argparse.Namespace) -> tuple[list[Hit], list[Hit]]:
    """(hits in live files, hits in skipped historical files)."""
    live: list[Hit] = []
    skipped: list[Hit] = []
    for rel in tracked_files(root):
        text = read_text(root / rel)
        if text is None:
            continue
        hits = scan_text(text, rel, args.owner, args.old, args.new, args.new_owner or args.owner)
        if not hits:
            continue
        (skipped if is_historical(rel) and not args.include_historical else live).extend(hits)
    return live, skipped


def cmd_apply(root: Path, args: argparse.Namespace) -> int:
    """Rewrite live files in place; then re-scan and fail if any rewrite hit remains."""
    changed = 0
    for rel in tracked_files(root):
        if is_historical(rel) and not args.include_historical:
            continue
        path = root / rel
        text = read_text(path)
        if text is None:
            continue
        out = rewrite_text(text, args.owner, args.old, args.new, args.new_owner or args.owner)
        if out != text:
            path.write_bytes(out.encode("utf-8"))
            changed += 1
    live, _ = collect(root, args)
    left = [h for h in live if h.kind != "bare"]
    print(f"rewrote {changed} file(s); {len(left)} old reference(s) remain in live files")
    for h in left:
        print(f"  {h.path}:{h.line}: {h.old}")
    return 1 if left else 0


def cmd_check(root: Path, args: argparse.Namespace) -> int:
    """Exit 1 and list file:line if any live file still holds the old slug."""
    live, skipped = collect(root, args)
    left = [h for h in live if h.kind != "bare"]
    for h in left:
        print(f"{h.path}:{h.line}: {h.kind}: {h.old} -> {h.new}")
    manual = sum(1 for h in live if h.kind == "bare")
    print(
        f"{len(left)} old reference(s) in live files; {manual} bare mention(s) to review by hand; "
        f"{len(skipped)} mention(s) in skipped historical files"
    )
    return 1 if left else 0


def cmd_inventory(root: Path, args: argparse.Namespace) -> int:
    """Markdown table of every hit; historical and data files are grouped per file."""
    live, skipped = collect(root, args)
    print("| Where | Kind | Old | Replacement |")
    print("|---|---|---|---|")
    for h in live:
        print(
            f"| `{h.path}:{h.line}` | {h.kind} | `{h.old}` | {h.new if h.kind != 'bare' else h.new} |"
        )
    by_file: dict[str, list[Hit]] = {}
    for h in skipped:
        by_file.setdefault(h.path, []).append(h)
    for path, hs in sorted(by_file.items()):
        lines = ", ".join(sorted({str(h.line) for h in hs}, key=int))
        kinds = "/".join(sorted({h.kind for h in hs}))
        print(f"| `{path}` lines {lines} | {kinds} (historical, skipped) | | left as written |")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--old", default=DEFAULT_OLD, help="old repository name")
    ap.add_argument("--new", required=True, help="new repository name")
    ap.add_argument("--owner", default=DEFAULT_OWNER, help="current owner")
    ap.add_argument("--new-owner", default=None, help="new owner if the repository also moves")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--include-historical", action="store_true")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--inventory", action="store_true")
    args = ap.parse_args(argv)
    if args.old == args.new and not args.new_owner:
        print("rename_repo_urls: --old and --new are identical", file=sys.stderr)
        return 2
    try:
        if args.apply:
            return cmd_apply(args.root, args)
        if args.check:
            return cmd_check(args.root, args)
        return cmd_inventory(args.root, args)
    except (subprocess.CalledProcessError, OSError) as exc:
        # Fail closed: a tree we could not list is not a clean tree.
        print(f"rename_repo_urls: could not read {args.root}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
