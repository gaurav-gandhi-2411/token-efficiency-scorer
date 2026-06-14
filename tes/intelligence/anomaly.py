from __future__ import annotations

"""tes/intelligence/anomaly.py — Principled anomaly detection for session clusters.

Anomaly definition:
  A session is anomalous when its distance to its assigned cluster centroid
  (in scaled feature space) exceeds the Tukey outer fence for that cluster's
  distance distribution: Q3 + 1.5 * IQR.

Threshold rationale (documented for research/12):
  Tukey's fence is the standard non-parametric outlier criterion:
  - Outer fence (Q3 + 1.5*IQR) catches ~1% of a normal distribution.
  - It is data-driven (computed per-cluster from real distances, not an
    arbitrary z-score picked in advance).
  - Per-cluster thresholds are correct: a small cluster may have tight
    distances while a large diffuse cluster has wide distances; a fixed
    global threshold would mislabel members of diffuse clusters.
  - Honest about false-positive rate: for N sessions, approximately N * 0.01
    anomalies are expected from chance alone. We report count and percentage.

Feature attribution for anomalies:
  For each anomalous session, we compute the per-feature signed deviation from
  its cluster centroid (in scaled space), then report the top-3 by absolute
  deviation. This tells the user WHICH measured features make the session unusual
  within its cluster — grounded, falsifiable, and specific.

What anomaly does NOT mean:
  "Anomalous" = "statistically unusual for its cluster" — descriptive, not evaluative.
  It does NOT mean "bad session", "wasteful", or "something went wrong." A session
  can be anomalous because it was unusually productive (very large with no waste).
  The anomaly report explicitly carries this framing.
"""

import dataclasses

import numpy as np

from tes.intelligence.cluster import ClusteringResult
from tes.intelligence.features import FEATURE_LABELS, FEATURE_NAMES, SessionFeatures

# Number of top deviating features to report per anomaly
_N_TOP_FEATURES: int = 3


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class AnomalyResult:
    """Anomaly descriptor for a single session."""

    session_id: str
    task_type: str
    cluster_id: int
    cluster_name: str
    distance_to_centroid: float        # in scaled feature space
    cluster_threshold: float           # Tukey fence for this cluster
    top_deviating_features: list[dict] # top-3 features by |deviation|; see _build_top_features


def _build_top_features(
    session_vec_scaled: np.ndarray,
    centroid_scaled: np.ndarray,
) -> list[dict]:
    """Return top-N features by absolute scaled deviation from centroid."""
    diff = session_vec_scaled - centroid_scaled
    top_idxs = np.argsort(np.abs(diff))[::-1][:_N_TOP_FEATURES]
    return [
        {
            "name": FEATURE_NAMES[i],
            "label": FEATURE_LABELS.get(FEATURE_NAMES[i], FEATURE_NAMES[i]),
            "deviation": float(diff[i]),   # positive = above centroid, negative = below
            "abs_deviation": float(abs(diff[i])),
        }
        for i in top_idxs
    ]


def detect_anomalies(
    features: list[SessionFeatures],
    X: np.ndarray,
    result: ClusteringResult,
) -> list[AnomalyResult]:
    """Detect anomalous sessions using per-cluster Tukey outer fences.

    Parameters
    ----------
    features:
        SessionFeatures list (same order as X and result.labels).
    X:
        Raw (unscaled) feature matrix, shape (N, 13).
    result:
        ClusteringResult from run_clustering().

    Returns
    -------
    List of AnomalyResult, one per anomalous session, sorted by distance descending.
    Returns empty list if result.valid is False or result has no labels.
    """
    if not result.labels or not result.valid:
        return []

    X_scaled = result.scaler.transform(X)
    labels = np.array(result.labels)
    distances = np.array(result.distances_to_centroid)

    # Build per-cluster Tukey fence thresholds
    cluster_thresholds: dict[int, float] = {}
    for k_idx in range(result.k):
        mask = labels == k_idx
        cluster_dists = distances[mask]
        if len(cluster_dists) < 4:
            # Too few samples for Tukey; fall back to 2× max distance
            cluster_thresholds[k_idx] = float(cluster_dists.max() * 2.0 if len(cluster_dists) > 0 else 1e9)
            continue
        q1 = float(np.percentile(cluster_dists, 25))
        q3 = float(np.percentile(cluster_dists, 75))
        iqr = q3 - q1
        cluster_thresholds[k_idx] = q3 + 1.5 * iqr

    # Identify anomalies and build results
    anomalies: list[AnomalyResult] = []
    archetype_by_id = {a.cluster_id: a for a in result.archetypes}

    for i, sf in enumerate(features):
        k_idx = labels[i]
        dist = distances[i]
        threshold = cluster_thresholds.get(k_idx, float("inf"))

        if dist <= threshold:
            continue

        archetype = archetype_by_id.get(k_idx)
        cluster_name = archetype.name if archetype else f"cluster_{k_idx}"
        centroid_scaled = result.scaler.transform(
            archetype.centroid_unscaled.reshape(1, -1)
        )[0] if archetype is not None else np.zeros(X_scaled.shape[1])

        top_features = _build_top_features(X_scaled[i], centroid_scaled)

        anomalies.append(
            AnomalyResult(
                session_id=sf.session_id,
                task_type=sf.task_type,
                cluster_id=int(k_idx),
                cluster_name=cluster_name,
                distance_to_centroid=float(dist),
                cluster_threshold=float(threshold),
                top_deviating_features=top_features,
            )
        )

    anomalies.sort(key=lambda a: a.distance_to_centroid, reverse=True)
    return anomalies


def summarize_anomalies(
    anomalies: list[AnomalyResult],
    total_n: int,
) -> str:
    """Return a plain-language summary of the anomaly results."""
    if not anomalies:
        return f"No anomalous sessions detected across {total_n} content sessions."
    pct = 100.0 * len(anomalies) / total_n
    top = anomalies[0]
    return (
        f"{len(anomalies)} of {total_n} content sessions ({pct:.1f}%) are statistical outliers "
        f"for their cluster (Tukey outer fence on centroid distance). "
        f"Most extreme: session {top.session_id[:12]}... (cluster: {top.cluster_name}) — "
        f"top deviating feature: {top.top_deviating_features[0]['label'] if top.top_deviating_features else 'unknown'}."
    )


__all__ = [
    "AnomalyResult",
    "detect_anomalies",
    "summarize_anomalies",
]
