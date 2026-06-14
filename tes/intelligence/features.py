from __future__ import annotations

"""tes/intelligence/features.py — Feature extraction for session ML.

Converts stored session rows + on-the-fly attribution into a 13-dimensional
feature vector per session. Only content sessions (real_tokens > 0, source
JSONL accessible) produce valid feature vectors.

Feature engineering decisions (documented for methodology/research-12):

Attribution bucket proportions (4 of 6 buckets):
  The 6 attribution buckets (B1–B6) sum to total_billed_tokens, so including
  all 6 as fractions introduces perfect multicollinearity (rank-1 deficiency).
  We drop fresh_input_pct (B5) — it is the residual after the other 5 buckets
  and typically < 1% for most sessions. The 4 included:
    context_resend_pct  — B3 / total_billed (dominant driver: cache re-reads)
    context_growth_pct  — B6 / total_billed (context written this session)
    output_pct          — B4 / total_billed (model output fraction)
    waste_pct           — (B1+B2) / total_billed (detected waste fraction)

Session shape (3 log-scale size features):
  log_real_tokens   — log1p(real_tokens); captures session work volume
  log_turn_count    — log1p(turn_count); captures session length
  log_cost          — log1p(session_cost_usd * 1000); cost shape (≈ size but
                      captures per-turn cost differences from model/cache mix)

Waste signature (1 binary feature):
  has_waste         — 1 if waste_event_count > 0, 0 otherwise. Separates the
                      ~13% of sessions with detected waste from the clean majority.

Task type — EXCLUDED from feature vector (documented decision):
  task_type was evaluated as a clustering feature. With 5 one-hot task_type
  columns, k=7 KMeans achieves silhouette=0.37 but produces 5/7 clusters that
  are simply pure task-type groups (e.g., "all debug-fix sessions") — this
  repeats the already-known label without revealing attribution-shape patterns.
  Without task_type, k=3 achieves silhouette=0.40 with three cross-type
  archetypes that describe genuine behavioral patterns: large-clean sessions,
  short context-building sessions, and waste-containing sessions.
  → task_type is stored in SessionFeatures.task_type and reported as a
    per-cluster breakdown (cluster characteristic, not clustering feature).
  → See research/12 for the methodological justification.

Total: 8 features, N≈235 content sessions.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Feature registry (order defines the feature matrix column order)
# ---------------------------------------------------------------------------

ATTRIBUTION_FEATURES = [
    "context_resend_pct",
    "context_growth_pct",
    "output_pct",
    "waste_pct",
]

SIZE_FEATURES = [
    "log_real_tokens",
    "log_turn_count",
    "log_cost",
]

WASTE_FLAG_FEATURE = ["has_waste"]

# task_type is metadata reported per cluster, not a clustering feature.
# See module docstring for the methodological justification.
TASK_TYPES = ["infra-deploy", "ml-eval", "debug-fix", "research-recon", "feature-build"]

FEATURE_NAMES: list[str] = ATTRIBUTION_FEATURES + SIZE_FEATURES + WASTE_FLAG_FEATURE

# Indices for use in anomaly naming
_FEAT_IDX = {name: i for i, name in enumerate(FEATURE_NAMES)}

# Human-readable display labels (for dashboard + chat context)
FEATURE_LABELS: dict[str, str] = {
    "context_resend_pct": "Context re-send % of billed tokens",
    "context_growth_pct": "Context growth % of billed tokens",
    "output_pct": "Output % of billed tokens",
    "waste_pct": "Detected-waste % of billed tokens",
    "log_real_tokens": "Session work volume (log real_tokens)",
    "log_turn_count": "Session length (log turns)",
    "log_cost": "Session cost shape (log USD×1000)",
    "has_waste": "Has detected waste events",
}

_N_FEATURES = len(FEATURE_NAMES)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SessionFeatures:
    """Feature vector + raw provenance for a single content session."""

    session_id: str
    task_type: str
    real_tokens: int
    turn_count: int
    session_cost_usd: float | None
    waste_event_count: int

    # Attribution proportions (stored for provenance/reporting)
    context_resend_pct: float
    context_growth_pct: float
    output_pct: float
    waste_pct: float
    fresh_input_pct: float  # not in feature vector but kept for provenance

    # The 13-element numpy feature vector (unscaled)
    vector: np.ndarray = field(default_factory=lambda: np.zeros(_N_FEATURES))


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_features(
    row: dict,
    prices: dict | None = None,
) -> SessionFeatures | None:
    """Extract a 13-feature vector from a session store row.

    Returns None if:
    - real_tokens == 0 (stub session — no work product to cluster)
    - source JSONL not accessible (can't compute attribution)
    - attribution computation fails for any reason

    This function is the only place in intelligence/ that touches source JSONL;
    everything downstream (cluster.py, anomaly.py, chat.py) works from the
    extracted SessionFeatures objects.
    """
    if row.get("real_tokens", 0) == 0:
        return None

    source_path = row.get("source_path")
    if not source_path or not Path(source_path).exists():
        return None

    # --- Compute attribution from source JSONL ---
    try:
        from tes.adapt import adapt_session
        from tes.attribution import compute_attribution
        from tes._digest import reconstruct_digest
        from tes.waste import build_waste_entry
        from tes.cost import load_price_table

        if prices is None:
            prices = load_price_table()

        record = adapt_session(Path(source_path))
        waste_entry = build_waste_entry(row["session_id"], record["digest"]["turns"])
        digest = reconstruct_digest(record["digest"])
        attr = compute_attribution(digest, waste_entry, prices)

        total_billed = (
            attr.rr_waste_tokens
            + attr.rfr_waste_tokens
            + attr.context_resend_tokens
            + attr.output_tokens
            + attr.fresh_input_tokens
            + attr.context_growth_tokens
        )
    except Exception:
        return None

    if total_billed == 0:
        return None

    context_resend_pct = attr.context_resend_tokens / total_billed
    context_growth_pct = attr.context_growth_tokens / total_billed
    output_pct = attr.output_tokens / total_billed
    waste_pct = (attr.rr_waste_tokens + attr.rfr_waste_tokens) / total_billed
    fresh_input_pct = attr.fresh_input_tokens / total_billed

    # --- Size features ---
    real_tokens = row.get("real_tokens", 0)
    stored_tc = row.get("turn_count") or 0
    if stored_tc == 0 and source_path:
        # turn_count=0 means it was scored before turn counting was wired.
        # Re-derive from the already-open source file so the log_turn_count
        # feature is accurate rather than defaulting to log1p(1)=0.693.
        try:
            from tes.store import _count_turns_from_jsonl
            computed_tc = _count_turns_from_jsonl(source_path)
            stored_tc = computed_tc or 0
        except Exception:
            pass
    turn_count = max(stored_tc, 1)  # guard against 0 for log
    cost_usd = row.get("session_cost_usd")

    log_real_tokens = math.log1p(real_tokens)
    log_turn_count = math.log1p(turn_count)
    log_cost = math.log1p((cost_usd or 0.0) * 1000.0)  # scale to millidollars before log

    # --- Waste flag ---
    has_waste = 1.0 if (row.get("waste_event_count") or 0) > 0 else 0.0

    # task_type is metadata, not a clustering feature (see module docstring).
    task_type = row.get("task_type", "feature-build")

    vec = np.array(
        [
            context_resend_pct,
            context_growth_pct,
            output_pct,
            waste_pct,
            log_real_tokens,
            log_turn_count,
            log_cost,
            has_waste,
        ],
        dtype=np.float64,
    )

    return SessionFeatures(
        session_id=row["session_id"],
        task_type=task_type,
        real_tokens=real_tokens,
        turn_count=int(turn_count),
        session_cost_usd=cost_usd,
        waste_event_count=int(row.get("waste_event_count") or 0),
        context_resend_pct=context_resend_pct,
        context_growth_pct=context_growth_pct,
        output_pct=output_pct,
        waste_pct=waste_pct,
        fresh_input_pct=fresh_input_pct,
        vector=vec,
    )


def build_feature_matrix(
    rows: list[dict],
    prices: dict | None = None,
    *,
    verbose: bool = False,
) -> tuple[list[SessionFeatures], np.ndarray]:
    """Extract features for all valid content sessions and stack into a matrix.

    Parameters
    ----------
    rows:
        Session rows from store.list_sessions().
    prices:
        Price table (loaded once and reused). Loaded from tes.cost if None.
    verbose:
        Print extraction progress summary.

    Returns
    -------
    features:
        List of SessionFeatures for sessions where extraction succeeded.
    X:
        (N, 13) float64 array, one row per SessionFeatures, same order.
    """
    from tes.cost import load_price_table as _lpt

    if prices is None:
        prices = _lpt()

    features: list[SessionFeatures] = []
    n_stub = 0
    n_no_source = 0
    n_failed = 0

    for row in rows:
        if row.get("real_tokens", 0) == 0:
            n_stub += 1
            continue
        sf = extract_features(row, prices=prices)
        if sf is None:
            p = row.get("source_path")
            if p and not Path(p).exists():
                n_no_source += 1
            else:
                n_failed += 1
            continue
        features.append(sf)

    if verbose:
        print(
            f"[features] extracted {len(features)} / {len(rows)} sessions "
            f"(stubs={n_stub}, no_source={n_no_source}, failed={n_failed})"
        )

    if not features:
        return [], np.empty((0, _N_FEATURES), dtype=np.float64)

    X = np.vstack([sf.vector for sf in features])
    return features, X


__all__ = [
    "FEATURE_NAMES",
    "FEATURE_LABELS",
    "TASK_TYPES",
    "SessionFeatures",
    "extract_features",
    "build_feature_matrix",
]
