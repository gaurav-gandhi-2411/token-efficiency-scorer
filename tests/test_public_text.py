# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Public text must not point at the maintainer's private working files or use internal work-item
jargon. Two scans:

* README.md, CHANGELOG.md and docs/ (prose a reader follows): no private paths or jargon.
* The code and configuration that ships or runs in public: tracegauge/ (including the dashboard
  templates), the tes compatibility package, scripts/, .github/, pyproject.toml, MANIFEST.in and
  _build/. Comments and docstrings there keep the *reason* for a decision but not the internal
  ticket, wave or audit label that prompted it, and nothing names a private machine path or
  cloud project.

A claim in public text has to be reproducible from the repo, or be stated as an unpublished local
measurement; a path on the maintainer's disk or the name of an internal report is neither."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Built from pieces so this file does not trip its own scan (it lives under tests/, which is not
# scanned, but a grep over the repo for the markers should find the rule, not the data).
FORBIDDEN = ("D:" + "\\", "W1" + "A", "W1" + "B", "q-" + "step")

# --- code and configuration scan -------------------------------------------------------------
SOURCE_MARKERS = {
    "work-package label": re.compile(r"\bW1[AB]\b|\bW3\b|\bD[0-9]\b|\bitem [0-9]\b"),
    "audit label": re.compile(r"audit D[0-9]|\bT[0-9] fix\b|q-" + "step"),
    "phase label": re.compile(
        r"\b[Pp]hase[ -]?[0-9]|phase0|\bset B\b|c2-selection|\b[Ww]ave [0-9]+"
    ),
    "ticket code": re.compile(r"\b(?!CC0\b)[A-Z]{2}[0-9](?:\.[0-9])?\b"),
    "private rule or doc": re.compile(r"CLAUDE\.md|\brule [0-9]+[a-z]?\b|gg-portfolio|GG decision"),
    "role name": re.compile(r"\borchestrator\b|\bverifier\b"),
    "private path or project": re.compile(
        r"\b[CDEFG]:\\(?:Users\\|ml-projects)|\bD:\\|aethe[r]art|\bgaura\b"
    ),
}
SOURCE_SUFFIXES = {".py", ".html", ".yml", ".yaml", ".toml", ".sh", ".in", ".md", ".json"}
SOURCE_ROOTS = ("tracegauge", "tes", "scripts", ".github", "_build")
SOURCE_FILES = (
    "pyproject.toml",
    "MANIFEST.in",
    "NOTICE",
    "PRIVACY.md",
    "DATA_SOURCES.md",
    "SECURITY.md",
    "RELEASING.md",
    "CONTRIBUTING.md",
)
# Where a marker is the file's own subject rather than a leaked internal label. Each entry is
# (path, marker name) with the reason; a stale entry (nothing matches any more) fails the test, so
# this list cannot quietly grow into a blanket exemption.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("scripts/generalization_run.py", "phase label"): (
        "the script's own pipeline stages are called Phase 0..7 in its output and comments"
    ),
}


def _public_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "CHANGELOG.md"]
    files += sorted(
        p for p in (ROOT / "docs").rglob("*") if p.is_file() and p.suffix in {".md", ".txt"}
    )
    return files


def _source_files() -> list[Path]:
    files = [ROOT / name for name in SOURCE_FILES]
    for root in SOURCE_ROOTS:
        files += sorted(
            p
            for p in (ROOT / root).rglob("*")
            if p.is_file()
            and p.suffix in SOURCE_SUFFIXES
            and "__pycache__" not in p.parts
            and "node_modules" not in p.parts
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


def _scan_source() -> tuple[list[str], set[tuple[str, str]]]:
    hits: list[str] = []
    used: set[tuple[str, str]] = set()
    for path in _source_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            for name, rx in SOURCE_MARKERS.items():
                if rx.search(line):
                    if (rel, name) in ALLOWLIST:
                        used.add((rel, name))
                    else:
                        hits.append(f"{rel}:{n}: {name}: {line.strip()[:100]}")
    return hits, used


def test_shipped_code_and_config_carry_no_internal_labels() -> None:
    hits, _ = _scan_source()
    assert not hits, (
        "internal label, private path or private project name in code/config; keep the reason, "
        "drop the label:\n" + "\n".join(hits)
    )


def test_every_allowlist_entry_still_matches_something() -> None:
    _, used = _scan_source()
    stale = sorted(set(ALLOWLIST) - used)
    assert not stale, f"allowlist entries that no longer match anything (delete them): {stale}"


def test_source_scan_covers_the_shipped_surfaces() -> None:
    rels = {p.relative_to(ROOT).as_posix() for p in _source_files()}
    assert "tracegauge/cli.py" in rels
    assert "tracegauge/web/templates/base.html" in rels
    assert "tes/__init__.py" in rels
    assert "pyproject.toml" in rels
    assert ".github/workflows/ci.yml" in rels
    assert any(r.startswith("scripts/") for r in rels)


def test_markers_do_fire_on_the_labels_they_exist_for() -> None:
    samples = {
        "work-package label": "# done for W1" + "A D6",
        "audit label": "(audit D" + "8, 2026-10-07)",
        "phase label": "Phase " + "7 scope",
        "ticket code": "# R" + "R1: persisted",
        "private rule or doc": "per CLAUDE" + ".md rule 39c",
        "role name": "the orchestra" + "tor polls",
        "private path or project": "C:" + "\\Users\\x",
    }
    for name, text in samples.items():
        assert SOURCE_MARKERS[name].search(text), (name, text)
    assert not SOURCE_MARKERS["ticket code"].search("licence CC0 1.0")
