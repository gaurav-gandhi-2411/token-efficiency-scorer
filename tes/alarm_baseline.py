from __future__ import annotations

"""tes/alarm_baseline.py -- the comparison distribution for the live alarm.

The alarm used to compare a live session against ``self_baseline``'s band, which is built from
the LEAN half of a user's waste-free sessions over all time: its "p75" sits near the user's own
median, so the alarm fired on most sessions (80% of a real user's last 50, retrospectively) and
kept mixing sessions from earlier model generations. This module builds the threshold the alarm
now uses (study and numbers: docs/ALARM.md):

* a plain percentile of the user's OWN sessions (not the lean half),
* from RECENT sessions only (``window_days``),
* from the SAME MODEL ERA (the model that did most of a session's token work, e.g.
  ``claude-sonnet-5`` vs ``claude-sonnet-5-5``), because token scale moves between generations,
* ignoring rows from the pre-dedupe adapter (their real_tokens are inflated ~2.4x).

A fallback chain with an explicit minimum sample size picks the first tier that has enough data:

    recent_era_type  recent sessions, same era, same task type
    recent_era       recent sessions, same era, any task type
    recent_type      recent sessions, any era, same task type
    recent           recent sessions, any era, any task type
    shipped          the bundled reference band (upper 95% bound of its p75)
    disabled         no tier qualifies: the alarm stays silent and says why

Pure functions over ``PoolRow`` lists, so the study and the tests exercise exactly this code.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any

_MODEL_DATE_SUFFIX = re.compile(r"-\d{8}$")
_MODEL_CONTEXT_SUFFIX = re.compile(r"\[[^\]]*\]$")


def normalize_model(model: str | None) -> str:
    """Model id without a date or context-window suffix: ``claude-sonnet-5-5-20260901[1m]`` ->
    ``claude-sonnet-5-5``. Empty/synthetic ids map to ``""`` (era unknown)."""
    if not model or model.startswith("<"):
        return ""
    name = _MODEL_CONTEXT_SUFFIX.sub("", model.strip())
    return _MODEL_DATE_SUFFIX.sub("", name)


def dominant_model(turns: Iterable[Mapping[str, Any]]) -> str:
    """The normalized model that produced most of the session's real tokens.

    Real tokens per turn = input - cache_read + output (the measure the bands use). Ties break
    to the lexicographically smaller id so the answer is deterministic. ``""`` when no turn names
    a model.
    """
    weight: dict[str, int] = {}
    for t in turns:
        model = normalize_model(t.get("model"))
        if not model:
            continue
        real = (
            int(t.get("token_count_input", 0) or 0)
            - int(t.get("cache_read", 0) or 0)
            + int(t.get("token_count_output", 0) or 0)
        )
        weight[model] = weight.get(model, 0) + max(real, 0)
    if not weight:
        return ""
    return min(weight, key=lambda m: (-weight[m], m))


__all__ = [
    "dominant_model",
    "normalize_model",
]
