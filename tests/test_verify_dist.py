# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import verify_dist  # noqa: E402


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    dist = tmp_path / "dist"
    dist.mkdir()
    lines = []
    for name, data in {"p-1.tar.gz": b"sdist", "p-1-py3-none-any.whl": b"wheel"}.items():
        (dist / name).write_bytes(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text("\n".join(lines) + "\n")
    return dist, sums


def test_matching_artifacts_verify(tmp_path: Path) -> None:
    dist, sums = _setup(tmp_path)
    assert verify_dist.verify(dist, [sums, sums]) == []
    assert verify_dist.main([str(dist), str(sums)]) == 0


def test_changed_byte_is_refused(tmp_path: Path) -> None:
    dist, sums = _setup(tmp_path)
    (dist / "p-1.tar.gz").write_bytes(b"sdisT")
    assert any("p-1.tar.gz: sha256" in p for p in verify_dist.verify(dist, [sums]))
    assert verify_dist.main([str(dist), str(sums)]) == 1


def test_missing_and_extra_files_are_refused(tmp_path: Path) -> None:
    dist, sums = _setup(tmp_path)
    (dist / "p-1.tar.gz").unlink()
    (dist / "extra.whl").write_bytes(b"x")
    problems = verify_dist.verify(dist, [sums])
    assert "missing artifact: p-1.tar.gz" in problems
    assert any("unexpected file" in p and "extra.whl" in p for p in problems)


def test_differing_or_malformed_sums_are_refused(tmp_path: Path) -> None:
    dist, sums = _setup(tmp_path)
    other = tmp_path / "other.txt"
    other.write_text(sums.read_text().replace("a", "b", 1))
    assert verify_dist.verify(dist, [sums, other]) == [
        "the SHA256SUMS files differ from each other"
    ]
    bad = tmp_path / "bad.txt"
    bad.write_text("nothex  name\n")
    assert "malformed" in verify_dist.verify(dist, [bad])[0]
    empty = tmp_path / "empty.txt"
    empty.write_text("")
    assert "empty" in verify_dist.verify(dist, [empty])[0]


def test_missing_dist_directory_is_refused(tmp_path: Path) -> None:
    _, sums = _setup(tmp_path)
    assert verify_dist.verify(tmp_path / "nope", [sums])
