# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "tracegauge" / "web" / "static" / "fonts"
BASE = ROOT / "tracegauge" / "web" / "templates" / "base.html"


def test_font_files_do_not_use_the_reserved_font_name() -> None:
    # OFL-1.1 clause 3: a Modified Version (these are glyph subsets) may not use "Plex".
    for woff in FONTS.glob("*.woff2"):
        assert "plex" not in woff.name.lower(), woff.name


def test_dashboard_css_does_not_use_the_reserved_font_name() -> None:
    lines = [
        line
        for line in BASE.read_text(encoding="utf-8").splitlines()
        if "font-family" in line or "--font-" in line or "filename=" in line
    ]
    assert lines
    assert not [line for line in lines if "plex" in line.lower()]


def test_every_font_face_url_points_at_a_shipped_file() -> None:
    names = re.findall(r'filename="fonts/([^"]+\.woff2)"', BASE.read_text(encoding="utf-8"))
    assert len(names) == 5
    for name in names:
        assert (FONTS / name).is_file(), name


def test_ofl_text_ships_with_the_fonts() -> None:
    for name, copyright_line in [
        ("OFL-IBM-Plex.txt", "IBM Corp."),
        ("OFL-Space-Grotesk.txt", "Space Grotesk Project Authors"),
    ]:
        text = (FONTS / name).read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE Version 1.1" in text
        assert copyright_line in text


def test_ofl_texts_are_declared_as_package_data() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "web/static/fonts/OFL-*.txt" in pyproject


def test_no_name_record_of_any_shipped_font_contains_the_reserved_font_name() -> None:
    """Every record in every name table, including a variable font's named-instance names
    (name IDs 256+), not just the family/full/PostScript names. Needs fontTools and brotli (WOFF2),
    which are test-only tools, not dependencies of the package (they are in the `dev` group, which
    CI installs with `uv sync --frozen`). Outside CI a missing tool skips with a reason; in CI
    (`CI` set) a missing tool is a failure, so the check cannot silently stop running."""
    import os

    import pytest

    if os.environ.get("CI"):
        import brotli  # noqa: F401  # why: CI must fail, not skip, if the dev group lacks it
        from fontTools import ttLib as ttlib
    else:
        ttlib = pytest.importorskip("fontTools.ttLib")
        pytest.importorskip("brotli")
    checked = 0
    for woff in sorted(FONTS.glob("*.woff2")):
        font = ttlib.TTFont(woff)
        for record in font["name"].names:
            assert "plex" not in str(record).lower(), (woff.name, record.nameID, str(record))
            checked += 1
    assert checked > 50
