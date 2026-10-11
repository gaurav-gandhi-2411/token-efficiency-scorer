from __future__ import annotations

"""The one install-size measure used in README.md and docs/INSTALL.md.

    <venv-python> scripts/measure_install_size.py

Prints the total on-disk size of every file listed in the RECORD of each installed distribution,
excluding pip, setuptools and wheel (they are in every venv and are not part of tracegauge's
footprint). Compiled __pycache__ files that pip lists are included. Run it with the venv's own
interpreter; it measures whatever that environment has installed.
"""

import sys
from importlib import metadata
from pathlib import Path

EXCLUDED = {"pip", "setuptools", "wheel"}
MIB = 1024 * 1024


def measure() -> tuple[int, int, int]:
    """Return (bytes, files, distributions) for everything installed except EXCLUDED."""
    total = files = dists = 0
    for dist in metadata.distributions():
        name = (dist.metadata["Name"] or "").lower().replace("_", "-")
        if name in EXCLUDED or not dist.files:
            continue
        dists += 1
        for f in dist.files:
            path = Path(str(dist.locate_file(f)))
            if path.is_file():
                total += path.stat().st_size
                files += 1
    return total, files, dists


def main() -> int:
    total, files, dists = measure()
    print(
        f"{total / MIB:.1f} MiB ({total:,} bytes) in {files:,} files across {dists} distributions"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
