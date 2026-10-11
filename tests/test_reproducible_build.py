from __future__ import annotations

import gzip
import hashlib
import io
import shutil
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "_build"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_dist  # noqa: E402
from sdist_normalise import normalise_sdist  # noqa: E402


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


@pytest.mark.skipif(
    shutil.which("uv") is None or shutil.which("git") is None, reason="needs uv+git"
)
def test_two_builds_are_byte_identical(tmp_path: Path) -> None:
    first = build_dist.build(tmp_path / "one")
    second = build_dist.build(tmp_path / "two")
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    assert [p.name for p in first] == [p.name for p in second]
    assert len(first) == 2
    assert [digest(p) for p in first] == [digest(p) for p in second]
