from __future__ import annotations

"""Check a directory of release artifacts against SHA256SUMS files: `verify_dist.py DIST SUMS...`.

Every SUMS file (`sha256  name` per line, as printed by scripts/build_dist.py) must be
byte-identical, and DIST must hold exactly the files they list with exactly those hashes: a
missing file, an extra file, or a changed byte is a refusal (exit 1). release.yml runs this on the
artifact its publish job downloads, so what is uploaded to PyPI is what the preflight built and
compared, never a rebuild. See RELEASING.md.
"""

import hashlib
import sys
from pathlib import Path


def parse_sums(text: str) -> dict[str, str]:
    """`{name: sha256}` from `sha256sum`-style lines; raises on a malformed or duplicate line."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64 or not parts[1]:
            raise ValueError(f"malformed SHA256SUMS line: {line!r}")
        if parts[1] in sums:
            raise ValueError(f"duplicate SHA256SUMS entry: {parts[1]!r}")
        sums[parts[1]] = parts[0]
    if not sums:
        raise ValueError("SHA256SUMS is empty")
    return sums


def verify(dist: Path, sums_files: list[Path]) -> list[str]:
    """Return a list of problems (empty means the artifacts match the checked hashes)."""
    texts = [p.read_bytes() for p in sums_files]
    if not texts:
        return ["no SHA256SUMS file given"]
    if any(t != texts[0] for t in texts[1:]):
        return ["the SHA256SUMS files differ from each other"]
    try:
        expected = parse_sums(texts[0].decode("utf-8"))
    except ValueError as exc:
        return [str(exc)]
    present = {p.name for p in dist.iterdir()} if dist.is_dir() else set()
    problems = [f"missing artifact: {n}" for n in sorted(set(expected) - present)]
    problems += [f"unexpected file in {dist}: {n}" for n in sorted(present - set(expected))]
    for name in sorted(set(expected) & present):
        got = hashlib.sha256((dist / name).read_bytes()).hexdigest()
        if got != expected[name]:
            problems.append(f"{name}: sha256 {got} != checked {expected[name]}")
    return problems


def main(argv: list[str]) -> int:
    """CLI entry: argv = [DIST, SUMS, ...]; prints each artifact hash on success."""
    if len(argv) < 2:
        print("usage: verify_dist.py DIST SUMS [SUMS ...]", file=sys.stderr)
        return 2
    dist = Path(argv[0])
    problems = verify(dist, [Path(a) for a in argv[1:]])
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    for p in sorted(dist.iterdir()):
        print(f"verified {hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
