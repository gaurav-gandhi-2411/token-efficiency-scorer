# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
from pathlib import Path

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def test_ci_workflow_limits_the_github_token_to_read_by_default() -> None:
    """CodeQL's actions/missing-workflow-permissions: a job without its own block inherits this."""
    text = CI.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert re.search(r"^permissions:\n  contents: read\n", text, flags=re.M), (
        "ci.yml needs a workflow-level `permissions: contents: read`"
    )


def test_no_ci_job_widens_the_token_beyond_what_it_needs() -> None:
    """No job in ci.yml has been given a write scope; if one needs it, change this test with it."""
    text = CI.read_text(encoding="utf-8")
    assert not re.search(r"^\s+[a-z-]+: write\s*$", text, flags=re.M)
