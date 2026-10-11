from __future__ import annotations

"""Canonical tar.gz metadata for the sdist (kept free of setuptools so tests can import it)."""

import gzip
import io
import tarfile
from pathlib import Path


def _is_generated_text(name: str) -> bool:
    """True for the files setuptools generates (PKG-INFO, setup.cfg, *.egg-info/*)."""
    base = name.split("/", 1)[-1]
    return base in ("PKG-INFO", "setup.cfg") or ".egg-info/" in base


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
            if m.isfile() and _is_generated_text(m.name):
                # setuptools writes these in text mode: CRLF on Windows, LF on Linux.
                payload[m.name] = payload[m.name].replace(b"\r\n", b"\n")
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
