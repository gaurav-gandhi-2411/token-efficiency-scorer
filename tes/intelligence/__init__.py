from __future__ import annotations

"""tes/intelligence/ — Session Intelligence: ML clustering + conversational explainer.

0.7.0 addition. Reads already-computed metrics from the store; adds no new measurement.

Public API:
    features.py   — session -> feature vector (from stored metrics + attribution)
    cluster.py    — validated KMeans clustering with silhouette/stability; archetype naming
    anomaly.py    — centroid-distance anomaly detection; deviating feature attribution
    chat.py       — constrained conversational explainer (metrics-only egress)
"""

from tes.intelligence.features import (
    FEATURE_NAMES,
    SessionFeatures,
    build_feature_matrix,
    extract_features,
)
from tes.intelligence.cluster import (
    ArchetypeCluster,
    ClusteringResult,
    run_clustering,
)
from tes.intelligence.anomaly import (
    AnomalyResult,
    detect_anomalies,
)

__all__ = [
    "FEATURE_NAMES",
    "SessionFeatures",
    "build_feature_matrix",
    "extract_features",
    "ArchetypeCluster",
    "ClusteringResult",
    "run_clustering",
    "AnomalyResult",
    "detect_anomalies",
]
