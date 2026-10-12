from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = (Path(__file__).resolve().parent.parent / ".github/workflows/release.yml").read_text(
    encoding="utf-8"
)


def _job(name: str) -> str:
    """The text of one top-level job in release.yml."""
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][\w-]*:\n|\Z)", WORKFLOW, re.M | re.S)
    assert m, f"job {name} not found"
    return m.group(1)


def test_publish_uploads_the_preflight_artifact_and_never_rebuilds() -> None:
    publish = _job("publish")
    assert "build_dist.py" not in publish and "uv build" not in publish
    assert "name: dist-a" in publish
    assert (
        "verify_dist.py dist sums/sha256-a/SHA256SUMS.txt sums/sha256-b/SHA256SUMS.txt" in publish
    )
    # the hash check must come before the upload to PyPI
    assert publish.index("verify_dist.py") < publish.index("gh-action-pypi-publish")


def test_publish_runs_only_on_push_and_needs_the_artifact_verification() -> None:
    publish = _job("publish")
    assert "if: github.event_name == 'push'" in publish
    assert "verify-artifact" in publish.split("runs-on:")[0]


def test_artifact_verification_runs_on_dispatch_too() -> None:
    verify = _job("verify-artifact")
    assert not re.search(r"^    if:", verify, re.M), "must run on workflow_dispatch as well"
    assert "name: dist-a" in verify and "name: dist-b" in verify


def test_preflight_build_uploads_the_hashed_dist() -> None:
    build = _job("preflight-build")
    assert "name: dist-${{ matrix.build }}" in build
    assert "path: dist/" in build
