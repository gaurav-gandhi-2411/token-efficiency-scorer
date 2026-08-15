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
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from tes._digest import SessionDigest, TurnDigest

#: A price entry older than this is flagged by check_price_table_staleness,
#: not silently trusted. 90 days mirrors adk-tracegauge's own
#: STALE_THRESHOLD_DAYS (same reasoning: Claude pricing has no published
#: change cadence to derive this number from precisely; 90 days is a
#: deliberately conservative round number). See check_price_table_staleness.
STALE_THRESHOLD_DAYS = 90


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
    # Added 0.10.2 (S1 fix): True iff total_usd is a real, confidently-computed
    # dollar figure from a resolved price-table entry. False means the model
    # string did not resolve — total_usd is 0.0 (never a guessed/default rate)
    # and approximate_reason names the model and the remedy. See
    # _resolve_model's docstring: no code path may set priced=True while
    # is_approximate=True, or vice versa — the two are always in lockstep.
    priced: bool = True
    # Added 0.10.2 (S1 fix): non-empty iff server-side tool usage (e.g. web
    # search, $10/1,000 searches) was detected on this turn's raw usage data
    # but is NOT reflected in total_usd — tes has no verified per-search
    # billing wired through compute_turn_cost yet. Never silently omitted:
    # this field (and SessionCost.server_tool_warnings) is the loud signal
    # that total_usd is missing real, known-nonzero cost. Empty string when
    # no server-side tool usage was detected on this turn.
    server_tool_warning: str = ""


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
    # Added 0.10.2 (S1 fix): distinct, human-readable warnings for turns whose
    # server-side tool usage is excluded from total_usd — see
    # TurnCost.server_tool_warning. Empty list when no turn detected any.
    server_tool_warnings: list[str] = field(default_factory=list)


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


def _resolve_model(model_str: str, prices: dict[str, Any]) -> tuple[str | None, bool, str]:
    """Resolve a raw model string to a price-table key.

    Returns ``(resolved_key, is_approximate, approximate_reason)``.

    ``resolved_key`` is ``None`` when the model could not be resolved against
    the price table — callers (``compute_turn_cost``) MUST NOT substitute a
    default/guessed rate in that case; a ``None`` key means "cost genuinely
    unknown for this turn," not "approximately this many dollars." This
    mirrors adk-tracegauge's ``_pricing.resolve_model`` (fail-closed, no
    default) — until 0.10.2 this function instead silently returned
    ``prices["default_model"]``'s key, which is exactly the S1 audit finding:
    an unresolved claude-opus-5/claude-sonnet-5 call was silently priced at
    claude-sonnet-4-6's stale rate (50% overcharge for Sonnet-5, 40%
    undercharge for Opus-5 — see CHANGELOG.md 0.10.2). ``is_approximate`` is
    True in this same case (kept for backward-compatible field semantics —
    every existing caller of this flag already meant "the returned cost is
    not to be trusted," which now correctly means "cost is $0.00 and
    excluded," not "cost is guessed").
    """
    cleaned = _DATE_SUFFIX_RE.sub("", model_str.strip())

    models: dict[str, Any] = prices["models"]

    if not cleaned:
        return (
            None,
            True,
            "empty model string — cost unknown, not priced at a guessed/default rate",
        )

    if cleaned in models:
        return cleaned, False, ""

    for pattern in prices.get("model_patterns", []):
        if cleaned.startswith(pattern["prefix"]):
            return pattern["model_key"], False, ""

    known = ", ".join(sorted(models))
    reason = (
        f"unknown model '{model_str}' — cost unknown, not priced at a guessed/default rate "
        f"(known models: {known}). Set TES_PRICE_TABLE to a JSON file with the same schema "
        "as tes/data/prices.json containing an entry for this model, add one to "
        "~/.tes/prices.json, or open an issue at "
        "https://github.com/gaurav-gandhi-2411/token-efficiency-scorer/issues if it should "
        "ship built-in. This turn is excluded from the session's total_usd."
    )
    return None, True, reason


def _server_tool_warning(turn: TurnDigest) -> str:
    """Return a non-empty warning iff ``turn`` carries detected server-side
    tool usage (e.g. Claude's web_search server tool, billed at $10/1,000
    searches — see ``usage.server_tool_use`` in the raw Claude API response
    that ``tes.adapt`` parses) that this turn's total_usd does NOT reflect.

    Added 0.10.2 (S1 fix): before this, ``tes.adapt._parse_usage`` silently
    dropped any ``usage.server_tool_use`` counts (it read only 4 token-count
    fields), so a session with web-search calls was priced as if they never
    happened — no warning, no partial flag, nothing. tes has no verified
    per-search billing rate wired through compute_turn_cost yet (that would
    require confirming which of the 4 known token fields, if any, already
    reflect search-generated content — a larger change than this minimal
    fix), so the honest minimum is to warn loudly rather than silently omit.
    """
    counts = turn.server_tool_use
    if not counts:
        return ""
    detail = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()) if n)
    if not detail:
        return ""
    return (
        f"turn {turn.turn_index}: server-side tool usage detected ({detail}) but NOT "
        "priced — tes has no verified billing rate for these wired through cost "
        "computation yet (e.g. web search is $10/1,000 searches). total_usd for this "
        "turn excludes this cost; the true cost is higher than shown."
    )


def compute_turn_cost(
    turn: TurnDigest,
    prices: dict[str, Any],
    cache_duration: str = "5min",
) -> TurnCost:
    """Compute the dollar cost for a single AI turn.

    ``cache_duration`` controls which cache-creation multiplier is used:
    ``"5min"`` (default) or ``"1hr"``.

    When ``turn.model`` does not resolve against ``prices`` (see
    ``_resolve_model``), this returns a TurnCost with ``priced=False``,
    ``total_usd=0.0``, and ``approximate_reason`` naming the model and the
    remedy — NEVER a dollar figure computed at a guessed/default rate
    (0.10.2 S1 fix). Server-side tool usage detected on the turn (e.g. web
    search) is separately flagged via ``server_tool_warning`` regardless of
    whether the model itself resolved — the model resolving doesn't mean the
    turn's total is complete.
    """
    model_key, is_approximate, approximate_reason = _resolve_model(turn.model, prices)
    server_tool_warning = _server_tool_warning(turn)

    if model_key is None:
        # Cost genuinely unknown for this turn -- never substitute the
        # default model's rate. See _resolve_model's and this module's notes.
        return TurnCost(
            turn_index=turn.turn_index,
            model_key=turn.model or "(empty)",
            is_approximate=is_approximate,
            approximate_reason=approximate_reason,
            fresh_tokens=0,
            fresh_cost=0.0,
            cache_read_cost=0.0,
            cache_creation_cost=0.0,
            output_cost=0.0,
            total_usd=0.0,
            priced=False,
            server_tool_warning=server_tool_warning,
        )

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
        priced=True,
        server_tool_warning=server_tool_warning,
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
    server_tool_warnings = list(
        {tc.server_tool_warning for tc in turn_costs if tc.server_tool_warning}
    )

    # total_usd sums ONLY confidently-priced turns (priced=True) -- an
    # unresolved-model turn contributes total_usd=0.0 (see compute_turn_cost),
    # never a guessed/default-rate figure. When approximate_turn_count > 0,
    # this total is a floor, not the true total -- approximate/
    # approximate_reasons is the loud, always-checked signal of that (0.10.2
    # S1 fix; pre-0.10.2 this summed wrongly-guessed dollar amounts instead).
    total_usd = sum(tc.total_usd for tc in turn_costs)

    domain_of_validity = (
        f"Computed from measured tokens at per-turn, per-model rates (prices as of {price_table_date}; "
        "bundled — override with TES_PRICE_TABLE env var or ~/.tes/prices.json). "
        "Cache creation defaults to 5-min rate (1.25x input); cache read at 0.1x input. "
        "Output at full rate. A turn whose model string does not resolve against the "
        "price table is EXCLUDED from total_usd (never priced at a guessed/default "
        "rate, as of 0.10.2) — session flagged approximate when >"
        f"{approximate_threshold_pct}% of turns "
        "are unresolved this way; see approximate_reasons for the specific model(s) "
        "and remedy. Server-side tool usage (e.g. web search) detected but not priced "
        "is flagged separately in server_tool_warnings, regardless of approximate. "
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
        server_tool_warnings=server_tool_warnings,
    )


def check_price_table_staleness(
    prices: dict[str, Any] | None = None,
    today: date | None = None,
) -> list[tuple[str, str, int, str]]:
    """Return a list of ``(model_key, as_of, age_days, source_url)`` for every
    non-retired price-table entry older than ``STALE_THRESHOLD_DAYS``.

    Minimum-viable port of adk-tracegauge's staleness-guard CONCEPT
    (``_pricing.STALE_THRESHOLD_DAYS`` / ``ResolvedModel.is_stale`` /
    ``scripts/check_price_freshness.py``), adapted to tracegauge's own
    per-model ``as_of``/``source_url`` fields (adk-tracegauge's equivalent
    fields are named ``fetched_on``/``source_url``) and its ``retired``
    flag (adk-tracegauge has no equivalent — every entry there is expected
    to be live-verifiable). An entry with no per-model ``as_of`` inherits
    the table-level ``as_of``. An entry marked ``"retired": true`` is
    skipped entirely — a retired model's published rate does not change
    again, so "stale" does not apply (see prices.json's top-level note).
    An unparseable/missing ``as_of`` (on a non-retired entry, after falling
    back to the table-level date) is itself treated as stale — fail closed,
    never silently skipped. Deliberately does NOT port promo-expiry
    handling, tiering, multi-provider support, or the regression gate —
    Phase 7 (per-entry) full-engine-move scope, out of bounds here.

    Pure date arithmetic; no network calls, no paid API calls, zero cost.
    Empty list means every entry is fresh. See
    scripts/check_price_freshness.py for the CI entry point.
    """
    if prices is None:
        prices = load_price_table()
    if today is None:
        today = date.today()

    table_as_of = str(prices.get("as_of") or "")
    models: dict[str, Any] = prices.get("models", {})

    stale: list[tuple[str, str, int, str]] = []
    for model_key, entry in models.items():
        if entry.get("retired"):
            continue
        as_of = str(entry.get("as_of") or table_as_of)
        source_url = str(entry.get("source_url") or prices.get("source_url") or "<no source_url recorded>")
        try:
            as_of_date = date.fromisoformat(as_of)
        except ValueError:
            # An unparseable/missing date is itself a staleness signal --
            # fail closed rather than skip the entry silently.
            stale.append((model_key, as_of or "<missing>", -1, source_url))
            continue
        age_days = (today - as_of_date).days
        if age_days > STALE_THRESHOLD_DAYS:
            stale.append((model_key, as_of, age_days, source_url))
    return stale


__all__ = [
    "TurnCost",
    "SessionCost",
    "STALE_THRESHOLD_DAYS",
    "load_price_table",
    "compute_turn_cost",
    "compute_session_cost",
    "check_price_table_staleness",
]
