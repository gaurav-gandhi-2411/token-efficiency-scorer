from __future__ import annotations

"""tes/classify.py — Thin re-export of the task classifier."""

import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from task_classifier import classify_session  # noqa: E402

__all__ = ["classify_session"]
