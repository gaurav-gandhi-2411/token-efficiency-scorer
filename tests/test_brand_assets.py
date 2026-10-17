# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets" / "brand"
STATIC = ROOT / "tracegauge" / "web" / "static" / "brand"
BASE = ROOT / "tracegauge" / "web" / "templates" / "base.html"

ASSET_FILES = [
    "mark.svg",
    "lockup.svg",
    "lockup-light.svg",
    "lockup-dark.svg",
    "favicon.svg",
    "og-preview.svg",
    "og-preview.png",
    "OFL-Sora.txt",
]
PACKAGED_FILES = [
    "favicon.svg",
    "favicon-16-snapped-light.svg",
    "favicon-16-snapped-dark.svg",
    "lockup-dark.svg",
    "OFL-Sora.txt",
]
# Old calibration-era assets, removed when the logo was replaced; nothing may point at them.
REMOVED = ("badge.svg", "wordmark.svg")


def test_brand_assets_exist() -> None:
    for name in ASSET_FILES:
        assert (ASSETS / name).is_file(), name
    for name in PACKAGED_FILES:
        assert (STATIC / name).is_file(), name


def test_svgs_have_no_text_or_font_dependence() -> None:
    # The wordmark and tagline are outlines, so no SVG needs a font at render time.
    svgs = [*ASSETS.glob("*.svg"), *STATIC.glob("*.svg")]
    assert len(svgs) >= 10
    for svg in svgs:
        text = svg.read_text(encoding="utf-8")
        assert not re.search(r"<text|font-family|@font-face", text), svg.name


def test_shipped_copies_match_the_repo_copies() -> None:
    for name in ("favicon.svg", "lockup-dark.svg", "OFL-Sora.txt"):
        assert (ASSETS / name).read_bytes().replace(b"\r\n", b"\n") == (
            STATIC / name
        ).read_bytes().replace(b"\r\n", b"\n"), name


def test_og_preview_png_is_1200x630_rgb() -> None:
    data = (ASSETS / "og-preview.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, depth, color_type = struct.unpack(">IIBB", data[16:26])
    # colour type 2 = truecolour without alpha: the conservative structure the social-preview
    # upload accepts.
    assert (width, height, depth, color_type) == (1200, 630, 8, 2)


def test_sora_licence_text_is_the_ofl() -> None:
    for path in (ASSETS / "OFL-Sora.txt", STATIC / "OFL-Sora.txt"):
        text = path.read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE Version 1.1" in text
        assert "The Sora Project Authors" in text


def test_notice_and_data_sources_attribute_sora() -> None:
    for name in ("NOTICE", "DATA_SOURCES.md"):
        assert "Sora" in (ROOT / name).read_text(encoding="utf-8"), name


def test_dashboard_references_only_shipped_brand_files() -> None:
    names = re.findall(r"filename='brand/([^']+)'", BASE.read_text(encoding="utf-8"))
    assert {"favicon.svg", "favicon-16-snapped-light.svg", "lockup-dark.svg"} <= set(names)
    for name in names:
        assert (STATIC / name).is_file(), name


def test_dashboard_header_uses_the_fixed_dark_lockup() -> None:
    # The header is always dark, so it must not use the OS-scheme-adaptive lockup (which would be
    # dark ink on a dark bar for light-mode users).
    html = BASE.read_text(encoding="utf-8")
    nav = re.search(r'<a href="/" class="nav-brand">.*?</a>', html, re.S)
    assert nav
    assert "brand/lockup-dark.svg" in nav.group(0)


def test_nothing_references_the_removed_assets() -> None:
    scanned = [BASE, ROOT / "README.md", ROOT / "pyproject.toml", ROOT / "MANIFEST.in"]
    for path in scanned:
        text = path.read_text(encoding="utf-8")
        for name in REMOVED:
            assert f"brand/{name}" not in text, (path.name, name)
        assert "generate_og_preview" not in text, path.name


def test_brand_files_are_declared_as_package_data() -> None:
    """Every shipped brand file is matched by a `[tool.setuptools.package-data]` glob, so the
    wheel carries it. (The built wheel itself is checked by the release build's file listing; a
    wheel build in the unit suite would need the build backend installed.)"""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^"tracegauge" = \[(.*)\]\s*$', pyproject, re.M)
    assert match
    patterns = re.findall(r'"([^"]+)"', match.group(1))
    package = ROOT / "tracegauge"
    covered = {
        p.relative_to(package).as_posix() for pattern in patterns for p in package.glob(pattern)
    }
    for name in PACKAGED_FILES:
        assert f"web/static/brand/{name}" in covered, name
    for name in REMOVED:
        assert f"web/static/brand/{name}" not in covered, name
