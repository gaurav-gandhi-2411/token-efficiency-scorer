from __future__ import annotations

"""tes — Token-Efficiency Scorer SDK."""

from tes.judge import JUDGE_SETUP_HINT, JudgeConfig  # noqa: F401
from tes.score import (  # noqa: F401
    ThreeAxisResult,
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
    load_baselines,
    score_session,
)

__all__ = [
    "ThreeAxisResult",
    "JudgeConfig",
    "JUDGE_SETUP_HINT",
    "load_baselines",
    "score_session",
    "TOKEN_DOMAIN_OF_VALIDITY",
    "TRAJECTORY_DOMAIN_OF_VALIDITY",
    "WASTE_DOMAIN_OF_VALIDITY",
]
