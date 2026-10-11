from __future__ import annotations

"""Canonical zip metadata + LF METADATA for the wheel (no setuptools import, so tests can load it)."""

import base64
import hashlib
import zipfile
from pathlib import Path


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}"


def normalise_wheel(path: Path) -> None:
    """Rewrite the .whl at `path` in place with platform-independent metadata.

    setuptools on Windows writes METADATA with CRLF and stamps zip entries with create_system 0 and
    mode 0666; on Linux it is LF, create_system 3 and 0644. Pin LF, create_system 3 and 0644 for every
    member, keep member order and timestamps, and refresh RECORD for the one file whose bytes change.
    """
    with zipfile.ZipFile(path) as src:
        entries = [(i, src.read(i)) for i in src.infolist()]
    contents = {i.filename: data for i, data in entries}
    for name, data in contents.items():
        if name.endswith(".dist-info/METADATA"):
            contents[name] = data.replace(b"\r\n", b"\n")
    record = next(n for n in contents if n.endswith(".dist-info/RECORD"))
    lines = contents[record].decode().splitlines()
    out = []
    for line in lines:
        name = line.split(",", 1)[0]
        if name in contents and name != record:
            line = _record_line(name, contents[name])
        out.append(line)
    contents[record] = ("\n".join(out) + "\n").encode()
    with zipfile.ZipFile(path, "w") as dst:
        for info, _ in entries:
            new = zipfile.ZipInfo(info.filename, info.date_time)
            new.create_system = 3
            new.external_attr = 0o100644 << 16
            dst.writestr(new, contents[info.filename], zipfile.ZIP_DEFLATED, compresslevel=9)
