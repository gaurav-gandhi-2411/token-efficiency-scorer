#!/usr/bin/env python3
# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0
"""Add (or check) the Apache-2.0 SPDX header on first-party Python files.

    python scripts/spdx_headers.py            # add the header where it is missing
    python scripts/spdx_headers.py --check    # exit 1 and list files that lack it

Covers tes/, scripts/ and tests/. The header is two comment lines, placed after any shebang or
encoding line, so ``from __future__ import annotations`` stays the first non-comment line. Files that
carry the older "AGPL-3.0-only OR Apache-2.0" dual-licence block get that block replaced.

``tes/_waste_detectors.py`` is skipped on purpose: it is byte-frozen (tests/test_prior_features_intact.py
fails on any diff), and the repository LICENSE already covers it.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOTS = ("tes", "scripts", "tests")
# Byte-frozen project invariant; adding a header would trip its own guard test.
EXCLUDE = frozenset({"tes/_waste_detectors.py"})
HEADER_LINES = (
    "# Copyright 2026 Gaurav Gandhi",
    "# SPDX-License-Identifier: Apache-2.0",
)
SPDX_LINE = "# SPDX-License-Identifier: Apache-2.0"
# How many leading lines may hold the header before the file counts as lacking one.
_HEAD_WINDOW = 12
_CODING = re.compile(r"^#.*coding[:=]\s*[-\w.]+")
# The 0.10.1-0.15.0 dual-licence block: from its copyright line to the last "text:" pointer line.
_DUAL_BLOCK = re.compile(
    r"\A# Copyright 2026 Gaurav Gandhi\n#\n# Dual-licensed\..*?# Apache-2\.0 text: see LICENSE-APACHE"
    r" in the repository root\.\n",
    re.DOTALL,
)


def python_files(root: Path = REPO_ROOT) -> list[Path]:
    """First-party Python files to carry the header, sorted for stable output."""
    found: list[Path] = []
    for top in ROOTS:
        for path in sorted((root / top).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if "__pycache__" in path.parts or rel in EXCLUDE:
                continue
            found.append(path)
    return found


def has_header(text: str) -> bool:
    """True if the Apache-2.0 SPDX line is among the first lines of ``text``."""
    return any(line.strip() == SPDX_LINE for line in text.splitlines()[:_HEAD_WINDOW])


def add_header(text: str) -> str:
    """Return ``text`` with the header added (LF input; callers handle line endings)."""
    if has_header(text):
        return text
    header = "\n".join(HEADER_LINES) + "\n"
    if _DUAL_BLOCK.match(text):
        return _DUAL_BLOCK.sub(lambda _m: header, text, count=1)
    lines = text.split("\n")
    at = 0
    if lines and lines[0].startswith("#!"):
        at = 1
    if len(lines) > at and _CODING.match(lines[at]):
        at += 1
    head = "\n".join(lines[:at])
    rest = "\n".join(lines[at:])
    out = (head + "\n" if head else "") + header
    if rest.strip():
        out += "\n" + rest if not rest.startswith("\n") else rest
    return out


def process(path: Path, *, write: bool) -> bool:
    """Return True if ``path`` lacked the header; rewrite it (CRLF-safe) when ``write``."""
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    crlf = "\r\n" in text
    lf = text.replace("\r\n", "\n")
    if has_header(lf):
        return False
    if write:
        new = add_header(lf)
        if crlf:
            new = new.replace("\n", "\r\n")
        path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + new.encode("utf-8"))
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report files lacking the header")
    args = parser.parse_args(argv)
    missing = [p for p in python_files() if process(p, write=not args.check)]
    verb = "missing the SPDX header" if args.check else "updated"
    for path in missing:
        print(f"{path.relative_to(REPO_ROOT).as_posix()}: {verb}")
    print(f"{len(missing)} file(s) {verb}")
    return 1 if (args.check and missing) else 0


if __name__ == "__main__":
    sys.exit(main())
