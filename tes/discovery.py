# Copyright 2026 Gaurav Gandhi
#
# Dual-licensed. You may use this file under the terms of either:
#   - the GNU Affero General Public License v3.0 only (AGPL-3.0-only), or
#   - the Apache License, Version 2.0 (Apache-2.0),
# at your option.
#
# SPDX-License-Identifier: AGPL-3.0-only OR Apache-2.0
#
# AGPL-3.0-only text: see LICENSE in the repository root.
# Apache-2.0 text: see LICENSE-APACHE in the repository root.

from __future__ import annotations

"""tes/discovery.py — Session-file discovery under ~/.claude/projects.

Claude Code writes each subagent's transcript to
``<project>/<session-id>/subagents/agent-<id>.jsonl``. Every record in those files is
flagged ``isSidechain: true``, which ``tes.adapt`` (main-chain only) skips by design, so
scoring one standalone yields 0 tokens / UNAVAILABLE. They are not sessions: their spend
belongs to the parent session (see ``tes.adapt.collect_subagent_usage``). Discovery
therefore excludes them; an explicit path given by the user is never filtered.
"""

from collections.abc import Iterator
from pathlib import Path

SUBAGENTS_DIRNAME = "subagents"


def is_subagent_path(path: Path) -> bool:
    """True iff ``path`` is a subagent transcript (lives in a ``subagents`` directory)."""
    return SUBAGENTS_DIRNAME in path.parts[:-1]


def iter_session_files(cc_path: Path) -> Iterator[Path]:
    """Yield every main-session ``*.jsonl`` under ``cc_path``, skipping subagent transcripts.

    Only the path *below* ``cc_path`` is inspected, so a ``cc_path`` that itself sits
    under a directory named ``subagents`` is not mis-filtered.
    """
    for p in cc_path.rglob("*.jsonl"):
        if is_subagent_path(p.relative_to(cc_path)):
            continue
        yield p


__all__ = ["SUBAGENTS_DIRNAME", "is_subagent_path", "iter_session_files"]
