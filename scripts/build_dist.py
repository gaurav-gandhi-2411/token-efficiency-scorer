from __future__ import annotations

"""Build the sdist and wheel reproducibly: `python scripts/build_dist.py [outdir]`.

Runs the STOCK setuptools backend (via `uv build`) with SOURCE_DATE_EPOCH pinned to the HEAD
commit time. setuptools honours that variable for the wheel's zip timestamps but not for the
sdist: its tar members keep the checkout's file mtimes (plus a pax float mtime header and a gzip
header timestamp), so two builds of one commit differ in the sdist bytes. `normalise_sdist`
rewrites only that archive metadata (never file contents). The reference build is CI on Linux
(release.yml); a build on another OS can differ in the wheel (zip create_system, METADATA line
endings), so compare hashes only against a CI build. Used by release.yml; see RELEASING.md.
"""

import gzip
import hashlib
import io
import os
import subprocess
import sys
import tarfile
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


def normalise_sdist(path: Path, epoch: int) -> None:
    """Rewrite the .tar.gz at `path` in place with canonical tar and gzip metadata."""
    with tarfile.open(path, "r:gz") as src:
        members = src.getmembers()
        payload = {m.name: src.extractfile(m).read() for m in members if m.isfile()}  # type: ignore[union-attr]
    raw = io.BytesIO()
    # GNU format: no per-member pax headers (those carried the float mtimes).
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as out:
        for m in members:
            info = tarfile.TarInfo(m.name)
            info.type, info.mtime = m.type, epoch
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if m.isdir():
                info.mode = 0o755
                out.addfile(info)
            else:
                info.mode, info.size = 0o644, len(payload[m.name])
                out.addfile(info, io.BytesIO(payload[m.name]))
    # gzip header: no filename, mtime 0, fixed level.
    with path.open("wb") as fh, gzip.GzipFile("", "wb", 9, fh, mtime=0) as gz:
        gz.write(raw.getvalue())


def build(outdir: Path, root: Path = ROOT) -> list[Path]:
    """`uv build` `root` into `outdir` with SOURCE_DATE_EPOCH = HEAD's commit time."""
    epoch = commit_epoch(root)
    env = {**os.environ, "SOURCE_DATE_EPOCH": epoch}
    subprocess.run(["uv", "build", "--out-dir", str(outdir), str(root)], check=True, env=env)
    for sdist in outdir.glob("*.tar.gz"):
        normalise_sdist(sdist, int(epoch))
    return sorted(outdir.glob("*.whl")) + sorted(outdir.glob("*.tar.gz"))


def main(argv: list[str]) -> int:
    """CLI entry: build into argv[0] (default dist/) and print `sha256  name` per artifact."""
    outdir = Path(argv[0]) if argv else ROOT / "dist"
    for artifact in build(outdir):
        print(f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
