# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import gzip
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_dist  # noqa: E402


def _make_sdist(path: Path, *, mtime: float, mode: int, uid: int, order: list[str]) -> None:
    """A .tar.gz whose metadata varies with the arguments but whose content never does."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        top = tarfile.TarInfo("pkg-1")
        top.type, top.mode, top.mtime = tarfile.DIRTYPE, 0o777, mtime
        tar.addfile(top)
        for name in order:
            data = f"content of {name}".encode()
            info = tarfile.TarInfo(f"pkg-1/{name}")
            info.size, info.mtime, info.mode, info.uid, info.uname = (
                len(data),
                mtime,
                mode,
                uid,
                "u",
            )
            tar.addfile(info, io.BytesIO(data))
    with path.open("wb") as fh, gzip.GzipFile("x.tar", "wb", 6, fh, mtime=mtime) as gz:
        gz.write(raw.getvalue())


def test_sdist_metadata_is_canonical(tmp_path: Path) -> None:
    """Two sdists differing only in mtime, mode, owner and gzip header normalise to equal bytes."""
    a, b = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    files = ["a.py", "b.py"]
    _make_sdist(a, mtime=1_700_000_000.123456, mode=0o666, uid=1000, order=files)
    _make_sdist(b, mtime=1_800_000_000.654321, mode=0o755, uid=0, order=files)
    assert a.read_bytes() != b.read_bytes()
    build_dist.normalise_sdist(a, 1_234_567_890)
    build_dist.normalise_sdist(b, 1_234_567_890)
    assert a.read_bytes() == b.read_bytes()
    with tarfile.open(a) as tar:
        for m in tar.getmembers():
            assert int(m.mtime) == 1_234_567_890
            assert (m.uid, m.gid, m.uname) == (0, 0, "")
            assert m.mode == (0o755 if m.isdir() else 0o644)
            assert not m.pax_headers


def test_sdist_contents_are_preserved(tmp_path: Path) -> None:
    """Normalising rewrites metadata only: names, order and file bytes survive."""
    p = tmp_path / "s.tar.gz"
    _make_sdist(p, mtime=1.0, mode=0o600, uid=5, order=["z.py", "a.py"])
    build_dist.normalise_sdist(p, 42)
    with tarfile.open(p) as tar:
        assert [m.name for m in tar.getmembers()] == ["pkg-1", "pkg-1/z.py", "pkg-1/a.py"]
        assert tar.extractfile("pkg-1/a.py").read() == b"content of a.py"  # type: ignore[union-attr]


def test_commit_epoch_reads_head() -> None:
    assert build_dist.commit_epoch(ROOT).isdigit()


def test_setuptools_is_pinned_exactly() -> None:
    """[build-system].requires pins setuptools== (a range is not a reproducible build)."""
    pin = build_dist.pinned_setuptools(ROOT)
    assert pin and pin[0].isdigit()


def test_pinned_setuptools_rejects_a_range(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[build-system]\nrequires = ["setuptools>=70"]\n')
    with pytest.raises(RuntimeError, match="pin setuptools"):
        build_dist.pinned_setuptools(tmp_path)


def test_wheel_generator_reads_the_backend_version(tmp_path: Path) -> None:
    whl = tmp_path / "p-1-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        zf.writestr("p-1.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: setuptools (84.0.0)\n")
    assert build_dist.wheel_generator(whl) == "setuptools (84.0.0)"
