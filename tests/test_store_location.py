# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

"""The on-disk location stays ~/.tes after the rename to tracegauge, and nothing writes elsewhere.

Moving it would orphan every user's scored history; docs/MIGRATING_FROM_TES.md says it will not move.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "tracegauge"


def test_every_default_path_is_under_dot_tes_in_the_home_directory(tmp_path: Path) -> None:
    code = (
        "import pathlib, sys\n"
        "from tracegauge import store, plan, contribution, corpus_client\n"
        "home = pathlib.Path.home()\n"
        "paths = {\n"
        "    'db': store.resolve_db_path(),\n"
        "    'plan': plan.resolve_plan_config_path(),\n"
        "    'contributor_id': contribution._CONTRIBUTOR_ID_FILE,\n"
        "    'blocked_log': corpus_client._BLOCKED_LOG_PATH,\n"
        "}\n"
        "for name, p in paths.items():\n"
        "    assert p.parent == home / '.tes', (name, p)\n"
        "print('ok')\n"
    )
    home = str(tmp_path)
    # Every root that Path.home() or the db lookup may consult is redirected into tmp_path, and the
    # variables that would override the default are removed: the subprocess can only see a throwaway.
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith(("TES_", "TRACEGAUGE_"))}
    env.update({"HOME": home, "USERPROFILE": home})
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


def test_no_module_builds_a_path_under_a_directory_other_than_dot_tes_or_claude() -> None:
    """Every Path.home() in the package is followed by .tes or .claude; no .tracegauge directory."""
    offenders: list[str] = []
    for py in sorted(PKG.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if "Path.home()" in line and not re.search(
                r'Path\.home\(\)\s*/\s*"\.(tes|claude)"', line
            ):
                offenders.append(f"{py.name}:{i}: {line.strip()}")
            if re.search(r"""["']\.tracegauge["']""", line):
                offenders.append(f"{py.name}:{i}: {line.strip()}")
    assert not offenders, offenders
