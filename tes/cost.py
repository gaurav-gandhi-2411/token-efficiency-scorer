# Copyright 2026 Gaurav Gandhi
#
# Dual-licensed. You may use this file under the terms of either:
#   - the GNU Affero General Public License v3.0 only (AGPL-3.0-only), or
#   - the Apache License, Version 2.0 (Apache-2.0),
# at your option.
#
# SPDX-License-Identifier: AGPL-3.0-only OR Apache-2.0
#
# AGPL-3.0-only text: see LICENSE in the repository root.
# Apache-2.0 text: see LICENSE-APACHE in the repository root.

from __future__ import annotations

"""tes/cost.py — Dollar-cost computation for Claude Code sessions.

Self-contained (no src/ or scripts/ imports). Prices are loaded from a bundled
JSON table; users can override via TES_PRICE_TABLE env var or ~/.tes/prices.json.
"""

import json
import os
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from tes._digest import SessionDigest, TurnDigest


@dataclass
class TurnCost:
    """Dollar-cost breakdown for a single AI turn."""

    turn_index: int
    model_key: str
    is_approximate: bool
    approximate_reason: str
    fresh_tokens: int
    fresh_cost: float
    cache_read_cost: float
    cache_creation_cost: float
    output_cost: float
    total_usd: float


@dataclass
class SessionCost:
    """Aggregated dollar-cost for a full session."""

    session_id: str
    total_usd: float
    turn_costs: list[TurnCost]
    approximate: bool
    approximate_reasons: list[str]
    domain_of_validity: str
    ai_turn_count: int
    approximate_turn_count: int


def load_price_table(path: str | Path | None = None) -> dict[str, Any]:
    """Load the price table, resolving in priority order:

    1. Explicit ``path`` argument
    2. ``TES_PRICE_TABLE`` environment variable
    3. ``~/.tes/prices.json`` if it exists
    4. Bundled ``tes/data/prices.json``
    """
    if path is not None:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    env_path = os.environ.get("TES_PRICE_TABLE")
    if env_path:
        return json.loads(Path(env_path).read_text(encoding="utf-8"))

    home_override = Path.home() / ".tes" / "prices.json"
    if home_override.exists():
        return json.loads(home_override.read_text(encoding="utf-8"))

    try:
        # importlib.resources path for installed package
        pkg_files = resources.files("tes") / "data" / "prices.json"
        return json.loads(pkg_files.read_text(encoding="utf-8"))
    except (TypeError, FileNotFoundError, AttributeError):
        bundled = Path(__file__).parent / "data" / "prices.json"
        return json.loads(bundled.read_text(encoding="utf-8"))


_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")


def _resolve_model(model_str: str, prices: dict[str, Any]) -> tuple[str, bool, str]:
    """Resolve a raw model string to a price-table key.

    Returns ``(resolved_key, is_approximate, approximate_reason)``.
    ``is_approximate`` is True when the model defaulted (unknown string).
    """
    cleaned = _DATE_SUFFIX_RE.sub("", model_str.strip())

    models: dict[str, Any] = prices["models"]
    default_key: str = prices["default_model"]

    if not cleaned:
        return default_key, True, f"empty model string — defaulted to {default_key}"

    if cleaned in models:
        return cleaned, False, ""

    for pattern in prices.get("model_patterns", []):
        if cleaned.startswith(pattern["prefix"]):
            return pattern["model_key"], False, ""

    reason = f"unknown model '{model_str}' — defaulted to {default_key}"
    return default_key, True, reason


def compute_turn_cost(
    turn: TurnDigest,
    prices: dict[str, Any],
    cache_duration: str = "5min",
) -> TurnCost:
    """Compute the dollar cost for a single AI turn.

    ``cache_duration`` controls which cache-creation multiplier is used:
    ``"5min"`` (default) or ``"1hr"``.
    """
    model_key, is_approximate, approximate_reason = _resolve_model(turn.model, prices)

    input_rate: float = prices["models"][model_key]["input_usd_per_mtok"]
    output_rate: float = prices["models"][model_key]["output_usd_per_mtok"]
    cache_mult: dict[str, float] = prices["cache_multipliers"]

    write_mult = cache_mult["write_1hr"] if cache_duration == "1hr" else cache_mult["write_5min"]

    fresh_tokens = max(0, turn.token_count_input - turn.cache_read - turn.cache_creation)

    fresh_cost = fresh_tokens * input_rate / 1_000_000
    cache_read_cost = turn.cache_read * (input_rate * cache_mult["read"]) / 1_000_000
    cache_creation_cost = turn.cache_creation * (input_rate * write_mult) / 1_000_000
    output_cost = turn.token_count_output * output_rate / 1_000_000
    total = fresh_cost + cache_read_cost + cache_creation_cost + output_cost

    return TurnCost(
        turn_index=turn.turn_index,
        model_key=model_key,
        is_approximate=is_approximate,
        approximate_reason=approximate_reason,
        fresh_tokens=fresh_tokens,
        fresh_cost=fresh_cost,
        cache_read_cost=cache_read_cost,
        cache_creation_cost=cache_creation_cost,
        output_cost=output_cost,
        total_usd=total,
    )


def compute_session_cost(
    digest: SessionDigest,
    prices: dict[str, Any] | None = None,
    cache_duration: str = "5min",
) -> SessionCost:
    """Compute the aggregated dollar cost for a full session.

    Only AI turns (``role == "ai"``) are priced; user/tool/system turns are skipped.
    ``prices`` defaults to the bundled price table when ``None``.
    """
    if prices is None:
        prices = load_price_table()

    price_table_date: str = prices.get("as_of", "unknown")
    default_model: str = prices["default_model"]
    approximate_threshold_pct: int = prices.get("approximate_threshold_pct", 25)

    turn_costs: list[TurnCost] = []
    for turn in digest.turns:
        if turn.role != "ai":
            continue
        turn_costs.append(compute_turn_cost(turn, prices, cache_duration))

    ai_turn_count = len(turn_costs)
    approximate_turn_count = sum(1 for tc in turn_costs if tc.is_approximate)

    # Session-level approximate flag: threshold is STRICTLY greater than the pct.
    session_approximate = False
    if ai_turn_count > 0:
        pct = approximate_turn_count / ai_turn_count * 100
        session_approximate = pct > approximate_threshold_pct

    approximate_reasons = list(
        {tc.approximate_reason for tc in turn_costs if tc.approximate_reason}
    )

    total_usd = sum(tc.total_usd for tc in turn_costs)

    domain_of_validity = (
        f"Computed from measured tokens at per-turn, per-model rates (prices as of {price_table_date}; "
        "bundled — override with TES_PRICE_TABLE env var or ~/.tes/prices.json). "
        "Cache creation defaults to 5-min rate (1.25x input); cache read at 0.1x input. "
        "Output at full rate. Approximate when >"
        f"{approximate_threshold_pct}% of turns "
        f"have unknown model string (defaulted to {default_model}). "
        "API-equivalent token cost; flat-plan users' marginal cost differs. "
        "Cost annotates the token axis — not a score, not part of a composite."
    )

    return SessionCost(
        session_id=digest.session_id,
        total_usd=total_usd,
        turn_costs=turn_costs,
        approximate=session_approximate,
        approximate_reasons=approximate_reasons,
        domain_of_validity=domain_of_validity,
        ai_turn_count=ai_turn_count,
        approximate_turn_count=approximate_turn_count,
    )


__all__ = [
    "TurnCost",
    "SessionCost",
    "load_price_table",
    "compute_turn_cost",
    "compute_session_cost",
]
