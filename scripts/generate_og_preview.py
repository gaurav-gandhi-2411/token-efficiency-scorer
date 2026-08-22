from __future__ import annotations

"""Regenerate assets/brand/og-preview.svg and .png.

Run from a throwaway venv with fontTools, brotli, resvg-py, and pillow --
never the project's own dev environment (this script is design tooling,
not shipped code, and has no place in tes's own dependency graph).

Every character in the wordmark/tagline is a real path traced from this
repo's own bundled font files via fontTools, never a live <text> element --
this rasterization pipeline's SVG renderer does not do real font-family
matching (proven during the original AU1 rasterization: identical
font_extents regardless of the requested font name), so text is a shape
problem here, not a typography problem. The bundled font subsets
(tes/web/static/fonts/*.woff2) cover alphanumerics + space only (63
glyphs, no punctuation at all) -- the hyphen and period this file's own
tagline needs are hand-drawn as trivial geometric shapes; every other
character is a real glyph outline.

Usage:
    uv venv --python 3.11 C:/some-throwaway-path
    uv pip install fonttools brotli resvg-py pillow --python C:/some-throwaway-path/Scripts/python.exe
    C:/some-throwaway-path/Scripts/python.exe scripts/generate_og_preview.py
"""

import io
import math
import struct
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image

ROOT = Path(__file__).parent.parent
FONT_DIR = ROOT / "tes" / "web" / "static" / "fonts"
DISPLAY_FONT = str(FONT_DIR / "spacegrotesk-700.woff2")
BODY_FONT = str(FONT_DIR / "ibmplexsans-400.woff2")
OUT_SVG = ROOT / "assets" / "brand" / "og-preview.svg"
OUT_PNG = ROOT / "assets" / "brand" / "og-preview.png"

# Calibration palette (BRAND.md)
INK = "#12140F"
PAPER = "#F0EDE4"
NEEDLE = "#C9622B"
CALIBRATED = "#4F7A5C"
REGRESSION = "#B01E3C"
GRAPHITE = "#5B5D53"
TICK = "#A79F8C"

W, H = 1280, 640

_MANUAL_GLYPHS = {"-", "."}


def _manual_glyph_path(ch: str, font_size: float, cursor_x: float, y: float) -> tuple[str, float]:
    if ch == "-":
        w = font_size * 0.28
        h = font_size * 0.07
        gy = y - font_size * 0.32
        return f"M {cursor_x:.2f} {gy:.2f} h {w:.2f} v {h:.2f} h {-w:.2f} Z", font_size * 0.38
    if ch == ".":
        r = font_size * 0.045
        cx = cursor_x + r
        cy = y - r
        d = (
            f"M {cx - r:.2f} {cy:.2f} "
            f"a {r:.2f} {r:.2f} 0 1 0 {2 * r:.2f} 0 "
            f"a {r:.2f} {r:.2f} 0 1 0 {-2 * r:.2f} 0 Z"
        )
        return d, font_size * 0.22
    raise ValueError(f"no manual glyph for {ch!r}")


def text_to_path(
    font_path: str, text: str, font_size: float, x: float, y: float
) -> tuple[str, float]:
    """Real glyph outlines from `font_path`, baseline-anchored at (x, y) --
    a drop-in for what an SVG <text> element would occupy, minus the live
    text (and minus the rasterizer's font-resolution step entirely)."""
    font = TTFont(font_path)
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    scale = font_size / font["head"].unitsPerEm
    hmtx = font["hmtx"]
    path_parts = []
    cursor_x = x

    for ch in text:
        if ch in _MANUAL_GLYPHS:
            d, advance = _manual_glyph_path(ch, font_size, cursor_x, y)
        else:
            codepoint = ord(ch)
            if codepoint not in cmap:
                raise ValueError(f"glyph for {ch!r} not in font {font_path}")
            glyph_name = cmap[codepoint]
            advance = hmtx[glyph_name][0] * scale
            svg_pen = SVGPathPen(glyph_set)
            transform_pen = TransformPen(svg_pen, (scale, 0, 0, -scale, cursor_x, y))
            glyph_set[glyph_name].draw(transform_pen)
            d = svg_pen.getCommands()
        if d:
            path_parts.append(d)
        cursor_x += advance

    return " ".join(path_parts), cursor_x - x


def arc_path(cx: float, cy: float, r: float, start_deg: float, end_deg: float) -> str:
    def pt(deg: float) -> tuple[float, float]:
        rad = math.radians(deg - 90)
        return cx + r * math.cos(rad), cy + r * math.sin(rad)

    x1, y1 = pt(start_deg)
    x2, y2 = pt(end_deg)
    large_arc = 1 if (end_deg - start_deg) > 180 else 0
    return f"M {x1:.2f} {y1:.2f} A {r} {r} 0 {large_arc} 1 {x2:.2f} {y2:.2f}"


def build_svg() -> str:
    cx, cy, r = 340, 320, 230
    sweep_start, sweep_end = -135, 135

    zone_stops = [-135, -75, 75, 135]
    zone_colors = [REGRESSION, CALIBRATED, REGRESSION]
    band_arcs = "".join(
        f'<path d="{arc_path(cx, cy, r, zone_stops[i], zone_stops[i + 1])}" '
        f'fill="none" stroke="{zone_colors[i]}" stroke-width="14" stroke-linecap="butt" opacity="0.85"/>'
        for i in range(3)
    )

    ticks = []
    n_major, n_minor_per_major = 6, 4
    total_ticks = n_major * n_minor_per_major
    tick_r_outer = r - 26
    for i in range(total_ticks + 1):
        deg = sweep_start + (sweep_end - sweep_start) * i / total_ticks
        is_major = i % n_minor_per_major == 0
        length = 22 if is_major else 12
        width = 4 if is_major else 2
        rad = math.radians(deg - 90)
        x1 = cx + tick_r_outer * math.cos(rad)
        y1 = cy + tick_r_outer * math.sin(rad)
        x2 = cx + (tick_r_outer - length) * math.cos(rad)
        y2 = cy + (tick_r_outer - length) * math.sin(rad)
        ticks.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{GRAPHITE}" stroke-width="{width}" stroke-linecap="round"/>'
        )

    needle_deg = -10
    needle_len = r - 60
    rad = math.radians(needle_deg - 90)
    nx = cx + needle_len * math.cos(rad)
    ny = cy + needle_len * math.sin(rad)
    needle = (
        f'<line x1="{cx}" y1="{cy}" x2="{nx:.2f}" y2="{ny:.2f}" '
        f'stroke="{NEEDLE}" stroke-width="10" stroke-linecap="round"/>'
        f'<circle cx="{cx}" cy="{cy}" r="14" fill="{NEEDLE}"/>'
    )

    name_d, _ = text_to_path(DISPLAY_FONT, "tracegauge", 72, 760, 300)
    tag_d, _ = text_to_path(BODY_FONT, "Measured against your own baseline.", 26, 760, 356)
    text = f'<path d="{name_d}" fill="{PAPER}"/><path d="{tag_d}" fill="{TICK}"/>'

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
<rect width="{W}" height="{H}" fill="{INK}"/>
{band_arcs}{"".join(ticks)}{needle}
{text}
</svg>'''


def main() -> None:
    svg = build_svg()
    OUT_SVG.write_text(svg, encoding="utf-8")
    print(f"wrote {OUT_SVG}")

    import resvg_py

    png_data = resvg_py.svg_to_bytes(svg_path=str(OUT_SVG), width=W, height=H)
    img = Image.open(io.BytesIO(bytes(png_data))).convert("RGB")
    img.save(OUT_PNG, format="PNG")
    print(f"wrote {OUT_PNG}")

    data = OUT_PNG.read_bytes()
    pos = 8
    chunks = []
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunks.append(data[pos + 4 : pos + 8].decode("ascii"))
        pos += 8 + length + 4
    w, h, bitdepth, colortype = struct.unpack(">IIBB", data[16:26])
    print(
        f"verify: {w}x{h}, bitdepth={bitdepth}, colortype={colortype} (2=RGB truecolor), chunks={chunks}"
    )
    assert (w, h, bitdepth, colortype) == (W, H, 8, 2), "PNG constraint check failed"
    assert chunks == ["IHDR", "IDAT", "IEND"], "unexpected PNG chunks -- not maximally conservative"


if __name__ == "__main__":
    main()
