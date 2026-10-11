# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""README.md, CHANGELOG.md and docs/ are public text: they must not point at the maintainer's private
working files or use internal work-item jargon. A claim in them has to be reproducible from the repo,
or be stated as an unpublished local measurement; a path on the maintainer's disk or the name of an
internal report is neither."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Built from pieces so this file does not trip its own scan (it lives under tests/, which is not
# scanned, but a grep over the repo for the markers should find the rule, not the data).
FORBIDDEN = ("D:" + "\\", "W1" + "A", "q-" + "step")


def _public_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "CHANGELOG.md"]
    files += sorted(
        p for p in (ROOT / "docs").rglob("*") if p.is_file() and p.suffix in {".md", ".txt"}
    )
    return files


def test_public_text_has_no_private_paths_or_internal_jargon() -> None:
    hits: list[str] = []
    for path in _public_files():
        for n, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for needle in FORBIDDEN:
                if needle in line:
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {needle!r}")
    assert not hits, "private path or internal jargon in public text:\n" + "\n".join(hits)


def test_the_scan_sees_the_files_it_is_meant_to_scan() -> None:
    names = {p.name for p in _public_files()}
    assert {"README.md", "CHANGELOG.md", "UPGRADING.md", "EXIT_CODES.md"} <= names
