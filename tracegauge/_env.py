# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Environment variables: ``TRACEGAUGE_*`` is primary, the old ``TES_*`` names still work.

Every setting is read through :func:`env_value`, which returns the ``TRACEGAUGE_<NAME>`` value if it is
set and non-empty, otherwise the ``TES_<NAME>`` value, otherwise ``None``. An empty string counts
as unset in both places (the same rule the code used for ``TES_*`` before the rename), so an empty
``TRACEGAUGE_DB_PATH`` falls through to ``TES_DB_PATH`` rather than masking it. When both are set to
different values, ``TRACEGAUGE_*`` wins.

The ``TES_*`` names are removed in 0.18. The command line prints one stderr line when it sees one in
use without its ``TRACEGAUGE_*`` replacement (:func:`deprecated_in_use`); the library functions
never print. The line goes to stderr only, so ``--json`` output and pipes are unaffected, and
``TRACEGAUGE_NO_DEPRECATION_NOTICE=1`` silences it (and the ``tes`` command's own notice).
"""

import os
from collections.abc import Mapping

PRIMARY_PREFIX = "TRACEGAUGE_"
LEGACY_PREFIX = "TES_"

#: Every setting the code reads, without its prefix.
SETTINGS = (
    "DB_PATH",
    "PLAN_PATH",
    "PRICE_TABLE",
    "NO_NOTICE",
    "CORPUS_URL",
    "CORPUS_ANON_KEY",
    "CORPUS_WITHDRAW_URL",
    "TEST_USE_AMBIENT_STORE",
)

NO_DEPRECATION_NOTICE = "TRACEGAUGE_NO_DEPRECATION_NOTICE"
_TRUTHY = ("1", "true", "yes")


def env_value(name: str, environ: Mapping[str, str] | None = None) -> str | None:
    """Value of setting ``name`` (without prefix): TRACEGAUGE_<name>, else TES_<name>, else None."""
    env = os.environ if environ is None else environ
    return env.get(PRIMARY_PREFIX + name) or env.get(LEGACY_PREFIX + name) or None


def deprecated_in_use(environ: Mapping[str, str] | None = None) -> list[str]:
    """Names of ``TES_*`` variables that are set (non-empty) while their ``TRACEGAUGE_*`` twin is not."""
    env = os.environ if environ is None else environ
    return [
        LEGACY_PREFIX + name
        for name in SETTINGS
        if env.get(LEGACY_PREFIX + name) and not env.get(PRIMARY_PREFIX + name)
    ]


def notice_suppressed(environ: Mapping[str, str] | None = None) -> bool:
    """True when TRACEGAUGE_NO_DEPRECATION_NOTICE is 1, true or yes (case-insensitive)."""
    env = os.environ if environ is None else environ
    return env.get(NO_DEPRECATION_NOTICE, "").strip().lower() in _TRUTHY


def deprecation_line(environ: Mapping[str, str] | None = None) -> str | None:
    """The one-line stderr notice for deprecated variables in use, or None if there is nothing to say."""
    used = deprecated_in_use(environ)
    if not used or notice_suppressed(environ):
        return None
    names = ", ".join(used)
    return (
        f"{names} {'is' if len(used) == 1 else 'are'} deprecated; use "
        f"{', '.join(PRIMARY_PREFIX + u[len(LEGACY_PREFIX) :] for u in used)}. "
        "The TES_ names will be removed in 0.18."
    )
