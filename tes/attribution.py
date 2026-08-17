from __future__ import annotations

"""tes/attribution.py — Six-bucket token attribution for CC sessions.

Breaks total_billed_tokens (= Σ_ai_turns(input + output), including cached re-reads)
into six reconciling buckets:

  B1  rr_waste_tokens       — Redundant-read waste turns (RR proof_turns[2:])
  B2  rfr_waste_tokens      — Retry-loop waste turns (RFR proof_turns[2:])
  B3  context_resend_tokens — Cache reads on clean turns (context re-send cost)
  B4  output_tokens         — Output tokens on clean turns
  B5  fresh_input_tokens    — Fresh (non-cached) input on clean turns
  B6  context_growth_tokens — Cache creation on clean turns (new context written)

Invariant: B1+B2+B3+B4+B5+B6 == total_billed_tokens (algebraic proof in module body).

Algebraic proof:
  B1+B2 = Σ_waste_ai(input+output)
  B3+B4+B5+B6 = Σ_clean_ai(cache_read + output + (input-cache_read-cache_creation) +
                cache_creation)
              = Σ_clean_ai(input+output)
  Total = Σ_all_ai(input+output) = total_billed_tokens ✓

Public API:
    AttributionResult   — dataclass carrying the 6 token + 6 USD buckets
    compute_attribution — produce an AttributionResult from a SessionDigest
"""

from dataclasses import dataclass
from typing import Any

from tes._digest import SessionDigest
from tes.cost import TurnCost, compute_turn_cost, load_price_table

# ---------------------------------------------------------------------------
# Hard-locked display labels (never rename these — spec constraint)
# ---------------------------------------------------------------------------

_LABEL_B3: str = "Context re-send (cache reads)"
_LABEL_B5: str = "Fresh input (not attributable to detected waste)"
_LABEL_B6: str = "Context growth (cache writes)"

_DOMAIN_OF_VALIDITY: str = (
    "Shows WHERE total billed tokens went (measured, over ALL billed tokens including "
    "cached re-reads). Whether non-waste tokens were used WELL is the judge's "
    "question, not attribution's. Attribution basis (total_billed_tokens) differs "
    "from the real_tokens verdict — do not compare these numbers directly."
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class AttributionResult:
    """Six-bucket token and dollar attribution for a single session.

    token buckets sum exactly to total_billed_tokens.
    usd buckets sum exactly to total_usd.
    """

    session_id: str

    # --- 6 token buckets ---
    rr_waste_tokens: int
    rfr_waste_tokens: int
    context_resend_tokens: int    # B3 — _LABEL_B3
    output_tokens: int            # B4
    fresh_input_tokens: int       # B5 — _LABEL_B5
    context_growth_tokens: int    # B6 — _LABEL_B6

    # --- 6 USD buckets (float, always set; 0.0 when turn has no cost data) ---
    rr_waste_usd: float
    rfr_waste_usd: float
    context_resend_usd: float
    output_usd: float
    fresh_input_usd: float
    context_growth_usd: float

    # --- totals ---
    total_billed_tokens: int   # INVARIANT: equals sum of 6 token buckets
    total_usd: float           # equals sum of 6 USD buckets

    # --- real_tokens for comparison with the verdict axis (excludes cache_read) ---
    real_tokens: int

    # --- domain of validity — always populated ---
    domain_of_validity: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_waste_ai_turns(
    waste_entry: dict[str, Any],
    ai_turn_indices: frozenset[int],
) -> tuple[set[int], set[int]]:
    """Extract waste AI-turn sets from a waste_entry dict.

    Returns
    -------
    rfr_waste_ai_turns:
        AI turn indices that appear in proof_turns[2:] of any RFR event.
    rr_exclusive_ai_turns:
        AI turn indices that appear in proof_turns[2:] of any RR event
        and are NOT already in rfr_waste_ai_turns (RFR takes priority).
    """
    rfr_waste_ai_turns: set[int] = set()
    rr_candidate_ai_turns: set[int] = set()

    for event in waste_entry.get("waste_events", []):
        proof_turns: list[int] = event.get("turns", [])
        redundant_turns: list[int] = proof_turns[2:]  # turns[0:2] are legitimate
        # Filter to AI turns only
        redundant_ai: set[int] = {t for t in redundant_turns if t in ai_turn_indices}

        detector: str = event.get("detector", "")
        if detector == "REPEATED-FAILED-RETRY":
            rfr_waste_ai_turns |= redundant_ai
        elif detector == "REDUNDANT-READ":
            rr_candidate_ai_turns |= redundant_ai

    # RFR takes priority over RR for any overlap
    rr_exclusive_ai_turns: set[int] = rr_candidate_ai_turns - rfr_waste_ai_turns
    return rfr_waste_ai_turns, rr_exclusive_ai_turns


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def compute_attribution(
    digest: SessionDigest,
    waste_entry: dict[str, Any] | None = None,
    prices: dict[str, Any] | None = None,
) -> AttributionResult:
    """Compute the 6-bucket token attribution for a session.

    Parameters
    ----------
    digest:
        SessionDigest from tes.adapt.adapt_session or tes._digest.reconstruct_digest.
    waste_entry:
        Optional dict from tes.waste.build_waste_entry (keys: session_id, waste_events).
        When None, waste buckets are 0 and all billed tokens are attributed to clean
        buckets.
    prices:
        Optional price table from tes.cost.load_price_table().
        When None, loads the bundled table.

    Returns
    -------
    AttributionResult
        All 6 token buckets sum exactly to total_billed_tokens.
        All 6 USD buckets sum exactly to total_usd.
    """
    if prices is None:
        prices = load_price_table()

    # -----------------------------------------------------------------------
    # Step 1: Identify all AI turns and pre-compute per-turn costs
    # -----------------------------------------------------------------------
    ai_turns = [t for t in digest.turns if t.role == "ai"]
    ai_turn_index_set: frozenset[int] = frozenset(t.turn_index for t in ai_turns)

    # Map turn_index → TurnCost (computed once, reused for all buckets)
    turn_cost_map: dict[int, TurnCost] = {}
    for turn in ai_turns:
        turn_cost_map[turn.turn_index] = compute_turn_cost(turn, prices)

    # -----------------------------------------------------------------------
    # Step 2: Classify turns into waste vs clean sets
    # -----------------------------------------------------------------------
    rfr_waste_ai_turns: set[int] = set()
    rr_exclusive_ai_turns: set[int] = set()

    if waste_entry is not None:
        rfr_waste_ai_turns, rr_exclusive_ai_turns = _extract_waste_ai_turns(
            waste_entry, ai_turn_index_set
        )

    waste_ai_turns: set[int] = rfr_waste_ai_turns | rr_exclusive_ai_turns
    clean_ai_turn_indices: set[int] = {
        t.turn_index for t in ai_turns if t.turn_index not in waste_ai_turns
    }

    # -----------------------------------------------------------------------
    # Step 3: Accumulate token buckets
    # -----------------------------------------------------------------------
    rr_waste_tokens: int = 0
    rfr_waste_tokens: int = 0
    context_resend_tokens: int = 0
    output_tokens: int = 0
    fresh_input_tokens: int = 0
    context_growth_tokens: int = 0

    rr_waste_usd: float = 0.0
    rfr_waste_usd: float = 0.0
    context_resend_usd: float = 0.0
    output_usd: float = 0.0
    fresh_input_usd: float = 0.0
    context_growth_usd: float = 0.0

    total_billed_tokens: int = 0
    real_tokens: int = 0

    for turn in ai_turns:
        idx = turn.turn_index
        tc = turn_cost_map[idx]

        billed = turn.token_count_input + turn.token_count_output
        total_billed_tokens += billed
        real_tokens += (turn.token_count_input - turn.cache_read) + turn.token_count_output

        if idx in rfr_waste_ai_turns:
            # B2: retry-loop waste
            rfr_waste_tokens += billed
            rfr_waste_usd += tc.total_usd

        elif idx in rr_exclusive_ai_turns:
            # B1: redundant-read waste
            rr_waste_tokens += billed
            rr_waste_usd += tc.total_usd

        else:
            # Clean turn — split into B3/B4/B5/B6
            context_resend_tokens += turn.cache_read
            output_tokens += turn.token_count_output
            # fresh_input = input - cache_read - cache_creation (use max(0) per-turn defensively)
            fresh = max(0, turn.token_count_input - turn.cache_read - turn.cache_creation)
            fresh_input_tokens += fresh
            context_growth_tokens += turn.cache_creation

            context_resend_usd += tc.cache_read_cost
            output_usd += tc.output_cost
            fresh_input_usd += tc.fresh_cost
            context_growth_usd += tc.cache_creation_cost

    total_usd: float = (
        rr_waste_usd
        + rfr_waste_usd
        + context_resend_usd
        + output_usd
        + fresh_input_usd
        + context_growth_usd
    )

    return AttributionResult(
        session_id=digest.session_id,
        rr_waste_tokens=rr_waste_tokens,
        rfr_waste_tokens=rfr_waste_tokens,
        context_resend_tokens=context_resend_tokens,
        output_tokens=output_tokens,
        fresh_input_tokens=fresh_input_tokens,
        context_growth_tokens=context_growth_tokens,
        rr_waste_usd=rr_waste_usd,
        rfr_waste_usd=rfr_waste_usd,
        context_resend_usd=context_resend_usd,
        output_usd=output_usd,
        fresh_input_usd=fresh_input_usd,
        context_growth_usd=context_growth_usd,
        total_billed_tokens=total_billed_tokens,
        total_usd=total_usd,
        real_tokens=real_tokens,
        domain_of_validity=_DOMAIN_OF_VALIDITY,
    )


def attribution_fractions(attr: AttributionResult) -> tuple[float, float, float, float]:
    """Returns (context_resend_pct, context_growth_pct, output_pct, waste_pct).

    The single source of truth for the 4 attribution fractions
    tes.intelligence.features derives for ML clustering (RR1) -- factored
    out here so both score-time persistence (tes.store.upsert_session, via
    tes.cli/tes.watcher) and any legacy on-demand extraction from a source
    JSONL (tes.intelligence.features.extract_features, for rows scored
    before this existed) compute these identically, never two slightly
    different formulas that could silently disagree.

    Returns all-zero for a session with total_billed_tokens == 0 (no
    billed tokens at all) rather than dividing by zero.
    """
    total = attr.total_billed_tokens
    if total == 0:
        return 0.0, 0.0, 0.0, 0.0
    context_resend_pct = attr.context_resend_tokens / total
    context_growth_pct = attr.context_growth_tokens / total
    output_pct = attr.output_tokens / total
    waste_pct = (attr.rr_waste_tokens + attr.rfr_waste_tokens) / total
    return context_resend_pct, context_growth_pct, output_pct, waste_pct


__all__ = ["AttributionResult", "attribution_fractions", "compute_attribution"]
