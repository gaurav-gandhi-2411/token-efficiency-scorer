from __future__ import annotations

"""tes/intelligence/ — Session Intelligence: ML clustering + conversational explainer.

0.7.0 addition. Reads already-computed metrics from the store; adds no new measurement.

Imports are lazy (PEP 562): `import tes.intelligence.cache` / `.chat` must not pull in numpy or
scikit-learn, because those live in the optional `tracegauge[patterns]` extra and the core CLI and
dashboard import these modules at startup. Callers that actually run clustering/features call
tes.patterns_extra.require_patterns_extra() first.

Public API:
    features.py   — session -> feature vector (from stored metrics + attribution)
    cluster.py    — validated KMeans clustering with silhouette/stability; archetype naming
    anomaly.py    — centroid-distance anomaly detection; deviating feature attribution
    chat.py       — constrained conversational explainer (metrics-only egress)
"""

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tes.intelligence.anomaly import AnomalyResult, detect_anomalies
    from tes.intelligence.cache import format_intelligence_summary, get_or_compute_intelligence
    from tes.intelligence.chat import (
        CHAT_EGRESS_NOTICE,
        ChatApiConfig,
        ChatConfig,
        ask_api,
        ask_local,
        build_chat_context,
    )
    from tes.intelligence.cluster import ArchetypeCluster, ClusteringResult, run_clustering
    from tes.intelligence.features import (
        FEATURE_NAMES,
        SessionFeatures,
        build_feature_matrix,
        extract_features,
    )

# public name -> submodule that defines it
_LAZY: dict[str, str] = {
    "AnomalyResult": "anomaly",
    "detect_anomalies": "anomaly",
    "format_intelligence_summary": "cache",
    "get_or_compute_intelligence": "cache",
    "CHAT_EGRESS_NOTICE": "chat",
    "ChatApiConfig": "chat",
    "ChatConfig": "chat",
    "ask_api": "chat",
    "ask_local": "chat",
    "build_chat_context": "chat",
    "ArchetypeCluster": "cluster",
    "ClusteringResult": "cluster",
    "run_clustering": "cluster",
    "FEATURE_NAMES": "features",
    "SessionFeatures": "features",
    "build_feature_matrix": "features",
    "extract_features": "features",
}


def __getattr__(name: str) -> Any:
    """Resolve a public name on first access (keeps `import tes.intelligence` dependency-free)."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"tes.intelligence.{module}"), name)
    globals()[name] = value
    return value


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
