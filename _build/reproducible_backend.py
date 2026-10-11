from __future__ import annotations

"""PEP 517 backend: setuptools, with a byte-reproducible sdist.

Why: setuptools writes the sdist from the working tree, so the tar members carry that machine's
file mtimes, permission bits (0666 on Windows, 0644 on Linux), member order, owner fields and a
gzip header timestamp. The same commit therefore built to different sdist bytes on Windows and
Linux, and the wheel (built from the sdist) was only as stable as that. Everything here is a
rewrite of metadata, never of file contents. Build with SOURCE_DATE_EPOCH set (see
scripts/build_dist.py); when it is unset the member mtimes are left as setuptools wrote them.
"""

import os
from pathlib import Path
from typing import Any

from sdist_normalise import normalise_sdist
from setuptools import build_meta as _setuptools
from setuptools.build_meta import *  # noqa: F403  # re-export the PEP 517 hooks unchanged
from wheel_normalise import normalise_wheel

__all__ = [*_setuptools.__all__, "build_sdist", "build_wheel"]


def build_sdist(sdist_directory: str, config_settings: dict[str, Any] | None = None) -> str:
    """Build the sdist with setuptools, then canonicalise its tar/gzip metadata."""
    name: str = _setuptools.build_sdist(sdist_directory, config_settings)
    sde = os.environ.get("SOURCE_DATE_EPOCH")
    normalise_sdist(Path(sdist_directory) / name, int(sde) if sde else None)
    return name


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    """Build the wheel with setuptools, then canonicalise its zip metadata."""
    name: str = _setuptools.build_wheel(wheel_directory, config_settings, metadata_directory)
    normalise_wheel(Path(wheel_directory) / name)
    return name
