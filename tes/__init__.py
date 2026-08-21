from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__: str = _pkg_version("tracegauge")
except PackageNotFoundError:
    __version__ = "0.0.0.dev0"

"""tes — Token-Efficiency Scorer SDK."""

from tes.judge import JUDGE_SETUP_HINT, JudgeConfig  # noqa: F401
from tes.score import (  # noqa: F401
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
    ThreeAxisResult,
    load_baselines,
    score_session,
)

__all__ = [
    "__version__",
    "ThreeAxisResult",
    "JudgeConfig",
    "JUDGE_SETUP_HINT",
    "load_baselines",
    "score_session",
    "TOKEN_DOMAIN_OF_VALIDITY",
    "TRAJECTORY_DOMAIN_OF_VALIDITY",
    "WASTE_DOMAIN_OF_VALIDITY",
]
