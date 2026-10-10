from __future__ import annotations

"""tes/patterns_extra.py -- the single place that knows what `tes patterns` needs installed.

numpy and scikit-learn (scipy comes with it) are the optional `[patterns]` extra. Every consumer calls
require_patterns_extra() BEFORE it runs tes.intelligence work: `tes patterns`, `tes ask`, and the
dashboard /patterns and /ask routes. tes.intelligence itself imports lazily, so importing it (as the
CLI and the dashboard do at startup) never needs the extra.
"""

import importlib

PATTERNS_EXTRA_MODULES: tuple[str, ...] = ("numpy", "sklearn", "scipy")

PATTERNS_EXTRA_HINT = (
    'Pattern analysis needs the patterns extra: pip install "tracegauge[patterns]"'
    " (adds numpy, scikit-learn, scipy)."
)


class PatternsExtraMissing(RuntimeError):
    """The optional pattern-analysis dependencies are not installed. str() is the one-line hint."""


def require_patterns_extra() -> None:
    """Return normally if the pattern-analysis dependencies import; else raise PatternsExtraMissing.

    The message is one line and says exactly what to run, so the CLI can print it to stderr and
    exit 1 without a traceback.
    """
    for name in PATTERNS_EXTRA_MODULES:
        try:
            importlib.import_module(name)
        except ImportError as exc:
            raise PatternsExtraMissing(PATTERNS_EXTRA_HINT) from exc
