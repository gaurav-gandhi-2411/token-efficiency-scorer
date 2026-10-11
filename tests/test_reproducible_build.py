# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "_build"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_dist  # noqa: E402
from sdist_normalise import normalise_sdist  # noqa: E402
from wheel_normalise import normalise_wheel  # noqa: E402

CRLF = b"a\r\nb\r\n"
LF = b"a\nb\n"


def _make_sdist(path: Path, *, mtime: int, mode: int, order: list[str]) -> None:
    """A tiny .tar.gz whose metadata varies with the arguments but whose content never does."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name in order:
            data = f"content of {name}".encode()
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode, info.uid, info.uname = (
                len(data),
                mtime,
                mode,
                1000,
                "u",
            )
            tar.addfile(info, io.BytesIO(data))
    path.write_bytes(gzip.compress(raw.getvalue(), mtime=mtime))


def test_normalise_sdist_ignores_mtime_mode_order_owner(tmp_path: Path) -> None:
    a, b = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    _make_sdist(a, mtime=1_000_000, mode=0o666, order=["p/x.py", "p/y.py"])
    _make_sdist(b, mtime=2_000_000, mode=0o644, order=["p/y.py", "p/x.py"])
    normalise_sdist(a, 1_700_000_000)
    normalise_sdist(b, 1_700_000_000)
    assert a.read_bytes() == b.read_bytes()
    with tarfile.open(a) as tar:
        assert [m.name for m in tar.getmembers()] == ["p/x.py", "p/y.py"]
        assert {(m.mtime, m.mode, m.uid, m.gid, m.uname) for m in tar.getmembers()} == {
            (1_700_000_000, 0o644, 0, 0, "")
        }


def test_normalise_sdist_lf_for_generated_files_only(tmp_path: Path) -> None:
    path = tmp_path / "s.tar.gz"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for name in ("p-1/PKG-INFO", "p-1/tes/x.py"):
            info = tarfile.TarInfo(name)
            info.size = len(CRLF)
            tar.addfile(info, io.BytesIO(CRLF))
    path.write_bytes(gzip.compress(raw.getvalue()))
    normalise_sdist(path, 1)
    with tarfile.open(path) as tar:
        got = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}  # type: ignore[union-attr]
    # Source files are never touched (they are LF by .gitattributes); generated ones are LF.
    assert got == {"p-1/PKG-INFO": LF, "p-1/tes/x.py": CRLF}


def _record_line(name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}"


def _make_wheel(path: Path, *, metadata: bytes, system: int, attr: int) -> None:
    files = {"p/a.py": b"x = 1\n", "p-1.dist-info/METADATA": metadata}
    lines = [_record_line(n, d) for n, d in files.items()] + ["p-1.dist-info/RECORD,,"]
    files["p-1.dist-info/RECORD"] = ("\n".join(lines) + "\n").encode()
    with zipfile.ZipFile(path, "w") as z:
        for n, d in files.items():
            zi = zipfile.ZipInfo(n, (2026, 1, 1, 0, 0, 0))
            zi.create_system, zi.external_attr = system, attr
            z.writestr(zi, d)


def test_normalise_wheel_is_platform_independent_and_record_stays_valid(tmp_path: Path) -> None:
    win, lin = tmp_path / "w.whl", tmp_path / "l.whl"
    _make_wheel(win, metadata=CRLF, system=0, attr=0o100666 << 16)
    _make_wheel(lin, metadata=LF, system=3, attr=0o100644 << 16)
    normalise_wheel(win)
    normalise_wheel(lin)
    assert win.read_bytes() == lin.read_bytes()
    with zipfile.ZipFile(win) as z:
        for line in z.read("p-1.dist-info/RECORD").decode().splitlines():
            name, digest, _ = line.split(",")
            if digest:
                assert line == _record_line(name, z.read(name))


@pytest.mark.skipif(
    shutil.which("uv") is None or shutil.which("git") is None, reason="needs uv+git"
)
def test_two_builds_are_byte_identical(tmp_path: Path) -> None:
    first = build_dist.build(tmp_path / "one")
    second = build_dist.build(tmp_path / "two")

    def digest(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    assert [p.name for p in first] == [p.name for p in second]
    assert len(first) == 2
    assert [digest(p) for p in first] == [digest(p) for p in second]
