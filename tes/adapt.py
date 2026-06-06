from __future__ import annotations

"""tes/adapt.py — Thin re-export of the frozen CC adapter.

The actual adapter logic lives in scripts/adapters/claudecode_adapter.py
(frozen, battle-tested on 181 pool + 1,053 SWE-chat sessions). This module
re-exports the public API so SDK consumers import from tes/, not scripts/.
"""

import sys
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from adapters.claudecode_adapter import adapt_session  # noqa: E402

__all__ = ["adapt_session"]
