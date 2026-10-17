# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
from pathlib import Path

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"

# PyYAML is not a declared dev dependency, so this reads the YAML shapes a permissions block can
# take by hand: block form, flow form `{contents: write}` and the `write-all` scalar.
_WRITE_PERMISSION = re.compile(
    r"^\s*permissions\s*:\s*(?:write-all\b|\{[^}\n]*:\s*write\b[^}\n]*\})"  # scalar or flow form
    r"|^\s*[A-Za-z-]+\s*:\s*write\s*(?:#.*)?$",  # block-form scope line
    flags=re.M,
)


def _widened(text: str) -> bool:
    """True when the workflow text grants any write scope, in any YAML spelling."""
    return bool(_WRITE_PERMISSION.search(text.replace("\r\n", "\n")))


def test_ci_workflow_limits_the_github_token_to_read_by_default() -> None:
    """CodeQL's actions/missing-workflow-permissions: a job without its own block inherits this."""
    text = CI.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert re.search(r"^permissions:\n  contents: read\n", text, flags=re.M), (
        "ci.yml needs a workflow-level `permissions: contents: read`"
    )


def test_no_ci_job_widens_the_token_beyond_what_it_needs() -> None:
    """No job in ci.yml has been given a write scope; if one needs it, change this test with it."""
    assert not _widened(CI.read_text(encoding="utf-8"))


def test_negative_control_every_spelling_of_a_write_grant_is_flagged() -> None:
    samples = [
        "jobs:\n  a:\n    permissions:\n      contents: write\n",
        "jobs:\n  a:\n    permissions:\n      contents:   write   \n",
        "jobs:\n  a:\n    permissions:\n      contents: write # why\n",
        "permissions: write-all\n",
        "jobs:\n  a:\n    permissions: write-all\n",
        "jobs:\n  a:\n    permissions: {contents: write}\n",
        "jobs:\n  a:\n    permissions: { contents:  write , id-token: read }\n",
        "jobs:\n  a:\n    permissions:\n      id-token: write\r\n",
    ]
    for sample in samples:
        assert _widened(sample), sample


def test_negative_control_read_only_workflows_are_not_flagged() -> None:
    samples = [
        "permissions:\n  contents: read\n",
        "permissions: {contents: read}\n",
        "permissions: read-all\n",
        "jobs:\n  a:\n    steps:\n      - run: echo write\n",
    ]
    for sample in samples:
        assert not _widened(sample), sample
