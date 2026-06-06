from __future__ import annotations

"""tes/baselines.py — Token baseline loader and real-token calculator."""

import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from build_baselines import compute_real_tokens  # noqa: E402
from efficiency_score import load_baselines  # noqa: E402

# Bundled baseline — inside tes/data/ so the installed package is self-contained.
BUNDLED_BASELINES_PATH: Path = Path(__file__).parent / "data" / "cc_baselines.json"

__all__ = ["compute_real_tokens", "load_baselines", "BUNDLED_BASELINES_PATH"]
