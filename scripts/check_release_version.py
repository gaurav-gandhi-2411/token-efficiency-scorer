"""Refuse a release whose version sources disagree, or whose version is already on PyPI.

tracegauge keeps its version in ``pyproject.toml`` ``[project].version`` (``tes.__version__`` is read from
the installed metadata, so it is not a separate source), in ``uv.lock``'s entry for the project, and in
the top released heading of ``CHANGELOG.md``; the git tag that triggers the release must equal it.
RELEASING.md step 1 lists this drift as a known failure (a hardcoded version assertion went stale once,
and a release shipped with no CHANGELOG entry); this makes it a blocking check in ``release.yml`` instead
of a sentence in a document. Modelled on agentgauge's ``scripts/check_release_version.py``.

Checks (any failure exits 1 and names every disagreement):
  * pyproject version == the project's entry in ``uv.lock`` == CHANGELOG.md's first ``## [x.y.z]``;
  * with ``--tag vX.Y.Z``: the tag (leading ``v`` stripped) == that version;
  * with ``--pypi``: the version is NOT already published (a duplicate upload cannot succeed, and
    finding out at the publish step is later than needed).
Anything it cannot determine (file unreadable, version line not found, PyPI unreachable) is exit 2,
never a pass: a guard that skips on error is the failure it exists to prevent.

Plain regexes, no tomllib: the suite runs on Python 3.10, which has none.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

PACKAGE = "tracegauge"
_PROJECT_TABLE = re.compile(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", re.M | re.S)
_VERSION_LINE = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.M)
_LOCK_ENTRY = re.compile(
    r'^\[\[package\]\]\r?\nname = "' + re.escape(PACKAGE) + r'"\r?\nversion = "([^"]+)"', re.M
)
_CHANGELOG_HEADING = re.compile(r"^## \[(\d[^\]\s]*)\]", re.M)


class CannotDetermine(RuntimeError):
    """A version source could not be read: reported as exit 2, never treated as agreement."""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CannotDetermine(f"{path.name}: {exc!r}") from exc


def pyproject_version(root: Path) -> str:
    table = _PROJECT_TABLE.search(_read(root / "pyproject.toml"))
    m = _VERSION_LINE.search(table.group(1)) if table else None
    if not m:
        raise CannotDetermine('pyproject.toml: no `version = "..."` line in [project]')
    return m[1]


def lock_version(root: Path) -> str:
    found = _LOCK_ENTRY.findall(_read(root / "uv.lock"))
    if len(found) != 1:
        raise CannotDetermine(f"uv.lock: expected one `{PACKAGE}` entry, found {len(found)}")
    return found[0]


def changelog_version(root: Path) -> str:
    m = _CHANGELOG_HEADING.search(_read(root / "CHANGELOG.md"))
    if not m:
        raise CannotDetermine("CHANGELOG.md: no `## [x.y.z]` release heading")
    return m[1]


def pypi_has(version: str) -> bool:
    """True if PyPI already serves this version. Any other outcome than a clean 200/404 raises."""
    url = f"https://pypi.org/pypi/{PACKAGE}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=20):  # noqa: S310 -- fixed https URL
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise CannotDetermine(f"PyPI answered HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CannotDetermine(f"PyPI unreachable ({exc!r})") from exc


def check(
    root: Path,
    tag: str | None = None,
    pypi: Callable[[str], bool] | None = None,
) -> list[str]:
    """Every problem found; empty means the release may proceed. Raises CannotDetermine."""
    problems: list[str] = []
    versions = {
        "pyproject.toml": pyproject_version(root),
        "uv.lock": lock_version(root),
        "CHANGELOG.md": changelog_version(root),
    }
    if len(set(versions.values())) != 1:
        problems.append(
            "version sources disagree: "
            + ", ".join(f"{k}={v}" for k, v in versions.items())
            + " (RELEASING.md step 1: bump pyproject, run `uv lock`, add the CHANGELOG entry)"
        )
    version = versions["pyproject.toml"]
    if tag is not None:
        if not re.fullmatch(r"v\d[^\s]*", tag):
            problems.append(f"tag {tag!r} is not of the form vX.Y.Z")
        elif tag[1:] != version:
            problems.append(f"tag {tag} does not match the package version {version}")
    if pypi is not None and pypi(version):
        problems.append(f"{PACKAGE} {version} is already on PyPI; bump the version")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--tag", help="the release tag (vX.Y.Z); omit on a dry run with no tag")
    ap.add_argument("--pypi", action="store_true", help="also refuse a version already on PyPI")
    args = ap.parse_args(argv)
    try:
        problems = check(args.root, args.tag, pypi_has if args.pypi else None)
    except CannotDetermine as exc:
        print(f"::error::cannot determine the release version state: {exc}")
        return 2
    if problems:
        for p in problems:
            print(f"::error::{p}")
        return 1
    print(
        f"release version OK: {pyproject_version(args.root)}"
        + (f" (tag {args.tag})" if args.tag else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
