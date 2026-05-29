from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Domain resolve-rate priors (from research/report 03)
# These are hardcoded corpus priors; do not compute from runtime data.
# ---------------------------------------------------------------------------
DOMAIN_RESOLVE_RATE: dict[str, float] = {
    "lib_general": 0.59,
    "type_checker": 0.14,
    "unknown": 0.61,
    "data_ml": 0.42,
    "cloud_devops": 0.48,
    "graph_geo": 0.95,
    "db_orm": 0.43,
    "web_api": 1.00,
    "testing_ci": 0.50,
}

# Fallback for out-of-distribution domains not in the prior table.
CORPUS_MEAN_RESOLVE_RATE: float = 0.50

# Minimum number of sessions a domain must have for its own p25 to be used;
# domains below this threshold fall back to corpus-wide p25.
_DOMAIN_MIN_SESSIONS: int = 3

# Clamp bounds for p25_token_ratio to prevent extreme values corrupting scores.
_P25_RATIO_MIN: float = 0.1
_P25_RATIO_MAX: float = 100.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class LayerOneFeatures:
    """All Layer 1 features for a single session."""

    session_id: str
    domain_id: str
    test_outcome: bool  # True ↔ resolved=True in taxonomy
    total_tokens: int
    turn_count: int
    h2_duplicate_count: int  # turns flagged llm_h2_duplicate_message=True
    cache_hit_rate: float  # sum(cache_read) / max(1, sum(input)); in [0.0, 1.0]
    p25_token_ratio: float  # total_tokens / domain_p25_baseline; clamped to [0.1, 100.0]
    labeler_model: str  # annotation._model; "missing" if annotation absent
    scaffold: str  # source scaffold: swe_agent | openhands_nebius | openhands_swegym
    output_tokens_available: bool  # True when per-turn output tokens are recorded (openhands); False for swe_agent


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def compute_domain_p25_baselines(taxonomy: list[dict[str, Any]]) -> dict[str, float]:
    """Compute the 25th-percentile token baseline for each domain.

    Domains with fewer than ``_DOMAIN_MIN_SESSIONS`` sessions fall back to
    the corpus-wide 25th percentile so that a single outlier session cannot
    define an unstable baseline.

    Args:
        taxonomy: Full list of session dicts loaded from task_taxonomy.json.

    Returns:
        Mapping of domain → p25 tokens_total value.
    """
    # Collect tokens_total per domain and corpus-wide.
    domain_tokens: dict[str, list[int]] = {}
    all_tokens: list[int] = []

    for row in taxonomy:
        domain: str = row["domain"]
        tokens: int = int(row["tokens_total"])
        all_tokens.append(tokens)
        domain_tokens.setdefault(domain, []).append(tokens)

    corpus_p25: float = float(np.percentile(all_tokens, 25)) if all_tokens else 1.0

    baselines: dict[str, float] = {}
    for domain, tokens_list in domain_tokens.items():
        if len(tokens_list) < _DOMAIN_MIN_SESSIONS:
            baselines[domain] = corpus_p25
        else:
            baselines[domain] = float(np.percentile(tokens_list, 25))

    return baselines


def extract_features(
    session_id: str,
    taxonomy_row: dict[str, Any],
    annotation: dict[str, Any] | None,
    trace: dict[str, Any] | None,
    domain_p25: dict[str, float],
) -> LayerOneFeatures:
    """Assemble all 7 Layer 1 features for one session.

    Args:
        session_id:    The canonical session identifier.
        taxonomy_row:  Single row from task_taxonomy.json for this session.
        annotation:    Parsed annotation JSON, or None if unavailable.
        trace:         Parsed trace JSON, or None if unavailable.
        domain_p25:    Pre-computed domain → p25 mapping from
                       ``compute_domain_p25_baselines``.

    Returns:
        A fully-populated ``LayerOneFeatures`` instance.
    """
    domain: str = taxonomy_row["domain"]
    total_tokens: int = int(taxonomy_row["tokens_total"])
    turn_count: int = int(taxonomy_row["turn_count"])
    test_outcome: bool = bool(taxonomy_row.get("resolved", False))

    # --- h2_duplicate_count ---------------------------------------------------
    h2_duplicate_count: int = 0
    labeler_model: str = "missing"
    if annotation is not None:
        labeler_model = str(annotation.get("_model", "missing"))
        for turn_label in annotation.get("per_turn_labels", []):
            if turn_label.get("llm_h2_duplicate_message", False):
                h2_duplicate_count += 1

    # --- cache_hit_rate -------------------------------------------------------
    cache_hit_rate: float = 0.0
    if trace is not None:
        total_cache_read: int = 0
        total_input: int = 0
        for turn in trace.get("turns", []):
            tc = turn.get("token_counts", {})
            total_cache_read += int(tc.get("cache_read", 0))
            total_input += int(tc.get("input", 0))
        cache_hit_rate = total_cache_read / max(1, total_input)

    # --- p25_token_ratio ------------------------------------------------------
    # Use corpus-wide p25 as fallback when the domain is not in the map.
    p25_baseline: float = domain_p25.get(domain, 1.0)
    # Protect against a zero baseline (shouldn't happen but guard defensively).
    if p25_baseline <= 0.0:
        p25_baseline = 1.0
    raw_ratio: float = total_tokens / p25_baseline
    p25_token_ratio: float = max(_P25_RATIO_MIN, min(_P25_RATIO_MAX, raw_ratio))

    scaffold: str = str(taxonomy_row.get("scaffold", "unknown"))
    output_tokens_available: bool = int(taxonomy_row.get("tokens_output", 0)) > 0

    return LayerOneFeatures(
        session_id=session_id,
        domain_id=domain,
        test_outcome=test_outcome,
        total_tokens=total_tokens,
        turn_count=turn_count,
        h2_duplicate_count=h2_duplicate_count,
        cache_hit_rate=cache_hit_rate,
        p25_token_ratio=p25_token_ratio,
        labeler_model=labeler_model,
        scaffold=scaffold,
        output_tokens_available=output_tokens_available,
    )


def load_corpus(
    taxonomy_path: Path,
    annotations_dir: Path,
    traces_dir: Path,
) -> list[LayerOneFeatures]:
    """Load the full corpus and return one ``LayerOneFeatures`` per session.

    Missing annotation or trace files are tolerated; the function logs a
    warning to stderr and continues with degraded feature values.

    Args:
        taxonomy_path:   Path to task_taxonomy.json.
        annotations_dir: Directory containing per-session annotation JSONs.
        traces_dir:      Directory containing per-session trace JSONs.

    Returns:
        List of ``LayerOneFeatures``, one per taxonomy entry.
    """
    taxonomy: list[dict[str, Any]] = json.loads(taxonomy_path.read_text(encoding="utf-8"))

    # Compute domain p25 baselines from the full 200-session corpus up-front
    # so that individual extract_features calls see consistent baselines.
    domain_p25: dict[str, float] = compute_domain_p25_baselines(taxonomy)

    results: list[LayerOneFeatures] = []

    for row in taxonomy:
        sid: str = row["session_id"]

        # Attempt to load annotation.
        annotation_path: Path = annotations_dir / f"{sid}.json"
        annotation: dict[str, Any] | None = None
        if annotation_path.exists():
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
        else:
            print(f"[layer1] WARNING: annotation missing for session {sid}", file=sys.stderr)

        # Attempt to load trace.
        trace_path: Path = traces_dir / f"{sid}.json"
        trace: dict[str, Any] | None = None
        if trace_path.exists():
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
        else:
            print(f"[layer1] WARNING: trace missing for session {sid}", file=sys.stderr)

        features: LayerOneFeatures = extract_features(
            session_id=sid,
            taxonomy_row=row,
            annotation=annotation,
            trace=trace,
            domain_p25=domain_p25,
        )
        results.append(features)

    return results
