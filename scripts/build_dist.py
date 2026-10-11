# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Build the sdist and wheel reproducibly: `python scripts/build_dist.py [outdir]`.

Exports SOURCE_DATE_EPOCH from the commit time of HEAD and runs `uv build`, so the same commit gives
byte-identical artifacts on any OS and from any checkout (see _build/reproducible_backend.py for the
sdist half). Prints the sha256 of each artifact. Used by release.yml and ci.yml; RELEASING.md.
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def commit_epoch(root: Path = ROOT) -> str:
    """Commit time (seconds since epoch) of HEAD in `root`; raises if it cannot be read."""
    out = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%ct"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not out.isdigit():
        raise RuntimeError(f"unexpected git commit time: {out!r}")
    return out


def build(outdir: Path, root: Path = ROOT) -> list[Path]:
    """Run `uv build` for `root` into `outdir` with SOURCE_DATE_EPOCH pinned to HEAD's commit time."""
    env = {**os.environ, "SOURCE_DATE_EPOCH": commit_epoch(root)}
    subprocess.run(["uv", "build", "--out-dir", str(outdir), str(root)], check=True, env=env)
    return sorted(outdir.glob("*.whl")) + sorted(outdir.glob("*.tar.gz"))


def main(argv: list[str]) -> int:
    """CLI entry: build into argv[0] (default dist/) and print `sha256  name` per artifact."""
    outdir = Path(argv[0]) if argv else ROOT / "dist"
    for artifact in build(outdir):
        print(f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
