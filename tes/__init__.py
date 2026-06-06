from __future__ import annotations

"""tes — Token-Efficiency Scorer SDK.

The SDK exposes the validated three-axis scorer as an importable package.
Entry point: from tes import score_session, ThreeAxisResult
"""

from tes.score import (  # noqa: F401
    ThreeAxisResult,
    load_baselines,
    score_session,
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
)

__all__ = [
    "ThreeAxisResult",
    "load_baselines",
    "score_session",
    "TOKEN_DOMAIN_OF_VALIDITY",
    "TRAJECTORY_DOMAIN_OF_VALIDITY",
    "WASTE_DOMAIN_OF_VALIDITY",
]
