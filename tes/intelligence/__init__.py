from __future__ import annotations

"""tes/intelligence/ — Session Intelligence: ML clustering + conversational explainer.

0.7.0 addition. Reads already-computed metrics from the store; adds no new measurement.

Public API:
    features.py   — session -> feature vector (from stored metrics + attribution)
    cluster.py    — validated KMeans clustering with silhouette/stability; archetype naming
    anomaly.py    — centroid-distance anomaly detection; deviating feature attribution
    chat.py       — constrained conversational explainer (metrics-only egress)
"""

from tes.intelligence.anomaly import (
    AnomalyResult,
    detect_anomalies,
)
from tes.intelligence.cache import (
    format_intelligence_summary,
    get_or_compute_intelligence,
)
from tes.intelligence.chat import (
    CHAT_EGRESS_NOTICE,
    ChatApiConfig,
    ChatConfig,
    ask_api,
    ask_local,
    build_chat_context,
)
from tes.intelligence.cluster import (
    ArchetypeCluster,
    ClusteringResult,
    run_clustering,
)
from tes.intelligence.features import (
    FEATURE_NAMES,
    SessionFeatures,
    build_feature_matrix,
    extract_features,
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
    "get_or_compute_intelligence",
    "format_intelligence_summary",
    "ChatConfig",
    "ChatApiConfig",
    "CHAT_EGRESS_NOTICE",
    "build_chat_context",
    "ask_local",
    "ask_api",
]
