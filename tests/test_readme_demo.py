# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""The README's "What it finds" blocks and the quickstart recording are generated, not typed.

scripts/gen_readme_demo.py runs the real CLI (`quickstart`, and `score` on a seeded synthetic
session with planted cache re-writes). These tests regenerate everything and fail when the README
or the SVG recording has drifted from what the command prints.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gen_readme_demo as gen  # noqa: E402

README = ROOT / "README.md"
SVG = ROOT / "assets" / "demo" / "quickstart.svg"


@pytest.fixture(scope="module")
def out() -> dict[str, str]:
    return gen.generate()


def _text_blocks(markdown: str) -> list[str]:
    return re.findall(r"```text\n(.*?)\n```", markdown.replace("\r\n", "\n"), flags=re.S)


def test_readme_quickstart_block_equals_the_cli_output(out: dict[str, str]) -> None:
    assert out["quickstart_excerpt"] in _text_blocks(README.read_text(encoding="utf-8")), (
        "README quickstart block is stale: python scripts/gen_readme_demo.py"
    )


def test_readme_cache_churn_block_equals_the_cli_output(out: dict[str, str]) -> None:
    block = out["cache_churn"]
    assert block.startswith("── CACHE CHURN") and "3 event(s)" in block
    assert block in _text_blocks(README.read_text(encoding="utf-8")), (
        "README cache-churn block is stale: python scripts/gen_readme_demo.py"
    )


def test_recording_svg_equals_a_fresh_generation(out: dict[str, str]) -> None:
    on_disk = SVG.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert on_disk == out["svg"], (
        "assets/demo/quickstart.svg is stale: "
        "python scripts/gen_readme_demo.py --write-svg assets/demo/quickstart.svg"
    )


def test_every_recorded_line_is_a_line_the_cli_printed(out: dict[str, str]) -> None:
    printed = set(out["quickstart_full"].splitlines())
    shown = gen.quickstart_lines(out["quickstart_full"])
    assert shown and all(ln in printed for ln in shown)
    svg = SVG.read_text(encoding="utf-8")
    for ln in shown:
        assert gen.escape(ln) in svg, ln


def test_recording_is_at_most_twenty_seconds_and_has_no_script_or_external_reference() -> None:
    svg = SVG.read_text(encoding="utf-8")
    assert gen.recording_seconds(svg) <= 20.0
    assert not re.search(r"<script|<image|href=|url\(|@import|http://(?!www\.w3\.org)", svg)
    assert "\\" not in svg.replace("\\n", "")  # no Windows path in the recording


def test_generation_is_deterministic(out: dict[str, str]) -> None:
    again = gen.generate()
    assert {k: again[k] for k in ("quickstart_excerpt", "cache_churn", "svg")} == {
        k: out[k] for k in ("quickstart_excerpt", "cache_churn", "svg")
    }


def test_too_long_a_recording_is_refused() -> None:
    with pytest.raises(ValueError):
        gen._svg(["x"] * 60)  # 1.2 s + 60 * 0.4 s is over the 20 s cap


def test_readme_has_the_sections_in_the_promised_order() -> None:
    headings = re.findall(r"^## (.+)$", README.read_text(encoding="utf-8"), flags=re.M)
    assert headings == [
        "Install and try it",
        "What it finds",
        "Commands",
        "Supported agents",
        "Methodology",
    ]
    assert (ROOT / "docs" / "METHODOLOGY.md").is_file()


def _dollar_rows(block: str, start: str, stop: str) -> tuple[float, float]:
    """Sum of the $ column of the rows between two marker lines, and the stated total."""
    lines = block.splitlines()
    rows = lines[next(i for i, ln in enumerate(lines) if ln.strip().startswith(start)) + 1 :]
    amounts = []
    for ln in rows:
        if ln.strip().startswith(stop):
            break
        m = re.search(r"\$(\d+\.\d\d)", ln)
        if m and re.match(r"^\s+\S.*\s\d+\s+\$", ln):
            amounts.append(float(m.group(1)))
    total = float(re.search(r"\$(\d+\.\d\d)", lines[1]).group(1))  # type: ignore[union-attr]
    return round(sum(amounts), 2), total


def test_readme_says_so_when_displayed_rows_do_not_add_up_to_the_displayed_total(
    out: dict[str, str],
) -> None:
    """Rows are rounded one by one, the total is not; where they differ the README must say why."""
    summed, total = _dollar_rows(out["cache_churn"], "3 event(s)", "Plausibly")
    readme = README.read_text(encoding="utf-8")
    if summed != total:
        assert "rounded independently" in readme and f"${summed:.2f}" in readme
    # the quickstart cost split displays rows that add up exactly; that must stay true or get a note
    rows = re.findall(
        r"^  (?!Total)\S.*?\$(\d+\.\d\d)\s+\d+\.\d%$",
        out["quickstart_excerpt"],
        flags=re.M,
    )
    stated = float(
        re.search(r"Total \(priced\)\s+\$(\d+\.\d\d)", out["quickstart_excerpt"]).group(1)
    )  # type: ignore[union-attr]
    assert round(sum(map(float, rows)), 2) == stated or "rounded independently" in readme
