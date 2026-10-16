# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""The example output blocks in docs/METHODOLOGY.md are generated, not typed.

scripts/gen_readme_examples.py runs the real CLI on synthetic sessions with a frozen clock. This
test regenerates every block and fails when docs/METHODOLOGY.md has drifted from the code's actual
output, so a change to `tracegauge cost`, `budget` or `monitor` output must regenerate the document
in the same change. (The README's own blocks are pinned by tests/test_readme_demo.py.)
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gen_readme_examples as gen  # noqa: E402


@pytest.fixture(scope="module")
def blocks() -> dict[str, str]:
    return gen.generate()


def test_every_generated_block_is_in_the_methodology(blocks: dict[str, str]) -> None:
    doc = (ROOT / "docs" / "METHODOLOGY.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    missing = [k for k, v in blocks.items() if k != "monitor_exit_code" and v not in doc]
    assert not missing, (
        f"docs/METHODOLOGY.md is out of date for {missing}: python scripts/gen_readme_examples.py"
    )


def test_generation_is_deterministic_and_the_alarm_fires(blocks: dict[str, str]) -> None:
    assert gen.generate() == blocks  # frozen clock: same output on every run
    assert blocks["monitor_exit_code"] == "3"  # the documented exit code when the alarm fires
    assert "[ALARM]" in blocks["monitor_alarm"]


def test_readme_has_no_estimated_equivalents_or_unlabelled_old_output() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for needle in ("÷2.26", "order-of-magnitude", "tracegauge==0.11.0", "published `0.11.0`"):
        assert needle not in readme, needle
