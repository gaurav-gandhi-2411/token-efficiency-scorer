from __future__ import annotations

"""PEP 517 backend: setuptools, with a byte-reproducible sdist.

Why: setuptools writes the sdist from the working tree, so the tar members carry that machine's
file mtimes, permission bits (0666 on Windows, 0644 on Linux), member order, owner fields and a
gzip header timestamp. The same commit therefore built to different sdist bytes on Windows and
Linux, and the wheel (built from the sdist) was only as stable as that. Everything here is a
rewrite of metadata, never of file contents. Build with SOURCE_DATE_EPOCH set (see
scripts/build_dist.py); when it is unset the member mtimes are left as setuptools wrote them.
"""

import gzip
import io
import os
import tarfile
from pathlib import Path
from typing import Any

from setuptools import build_meta as _setuptools
from setuptools.build_meta import *  # noqa: F403  # re-export the PEP 517 hooks unchanged

__all__ = [*_setuptools.__all__, "normalise_sdist"]


def normalise_sdist(path: Path, epoch: int | None) -> None:
    """Rewrite the .tar.gz at `path` in place with canonical, platform-independent metadata."""
    with tarfile.open(path, "r:gz") as src:
        members = sorted(src.getmembers(), key=lambda m: m.name)
        payload = {m.name: src.extractfile(m).read() for m in members if m.isfile()}  # type: ignore[union-attr]
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as out:
        for m in members:
            info = tarfile.TarInfo(m.name)
            info.type = m.type
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if epoch is not None:
                info.mtime = epoch
            else:
                info.mtime = int(m.mtime)
            if m.isdir():
                info.mode = 0o755
                out.addfile(info)
            else:
                # Windows has no exec bit and Linux does: pin one value. No sdist member needs +x.
                info.mode = 0o644
                info.size = len(payload[m.name])
                out.addfile(info, io.BytesIO(payload[m.name]))
    # gzip header: no filename, mtime 0, fixed level -> same bytes everywhere.
    with open(path, "wb") as fh, gzip.GzipFile("", "wb", 9, fh, mtime=0) as gz:
        gz.write(raw.getvalue())


def build_sdist(sdist_directory: str, config_settings: dict[str, Any] | None = None) -> str:
    """Build the sdist with setuptools, then canonicalise its tar/gzip metadata."""
    name: str = _setuptools.build_sdist(sdist_directory, config_settings)
    sde = os.environ.get("SOURCE_DATE_EPOCH")
    normalise_sdist(Path(sdist_directory) / name, int(sde) if sde else None)
    return name
