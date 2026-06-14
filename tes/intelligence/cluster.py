from __future__ import annotations

"""tes/intelligence/cluster.py — Validated KMeans clustering with archetype naming.

Method choice rationale (documented for research/12):
  KMeans was chosen over HDBSCAN for this corpus (N≈235) because:
  1. The feature space is well-bounded (attribution pcts in [0,1], log-scaled
     size features) with no extreme outliers expected → spherical clusters are
     plausible, which is KMeans' assumption.
  2. KMeans produces clean centroids that directly enable interpretable archetype
     naming (each archetype's description IS the centroid's dominant features).
  3. HDBSCAN at N=235 would likely mark many sessions as noise (especially in
     a 13-dimensional space), making archetype enumeration difficult.
  4. k selection by silhouette score is well-understood and interpretable for
     a portfolio audience.
  HDBSCAN is available in sklearn 1.3+ and is run as a secondary validation
  (reported but not the primary archetype source).

Silhouette threshold for "real clusters":
  Silhouette in (-1, 1). For text/session corpora at N<500:
  < 0.10 → the partition is essentially random (no structure)
  0.10–0.20 → weak but potentially real structure
  > 0.20 → meaningful clusters (we use this as the "stable" threshold)
  We report the score and be honest: below 0.20 = "weak structure, interpret
  with caution"; below 0.10 = "no stable clusters found at N sessions."

Stability check:
  10 KMeans runs with different random seeds at the chosen k.
  Compute mean and CV (std/mean) of silhouette scores.
  CV < 0.15 → stable; CV >= 0.15 → unstable (warn in DOV).

Archetype naming:
  For each cluster centroid, identify the 2-3 most discriminating features
  (highest absolute deviation from the global mean in scaled space).
  Build a human-readable name from those dominant features.
  The name IS the measured shape — not a quality label.
"""

import dataclasses
import warnings
from typing import Any

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from tes.intelligence.features import (
    FEATURE_LABELS,
    FEATURE_NAMES,
    SessionFeatures,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_SESSIONS_FOR_CLUSTERING: int = 30  # below this we don't attempt clustering
K_RANGE: tuple[int, int] = (2, 8)      # min/max k to evaluate
N_INIT: int = 30                        # KMeans random restarts per k evaluation
STABILITY_SEEDS: list[int] = list(range(10))  # seeds for stability check
SILHOUETTE_STABLE_THRESHOLD: float = 0.20     # above this: meaningful clusters
SILHOUETTE_WEAK_THRESHOLD: float = 0.10       # above this: weak but present
STABILITY_CV_THRESHOLD: float = 0.15         # CV below this: stable solution


# ---------------------------------------------------------------------------
# Archetype naming logic
# ---------------------------------------------------------------------------
# Feature indices (fixed by FEATURE_NAMES order in features.py):
#   0=context_resend_pct, 1=context_growth_pct, 2=output_pct, 3=waste_pct
#   4=log_real_tokens, 5=log_turn_count, 6=log_cost, 7=has_waste


def _attribution_shape(centroid_unscaled: np.ndarray) -> str:
    """Describe the dominant attribution pattern from centroid feature values."""
    ctx_resend = centroid_unscaled[0]
    ctx_growth = centroid_unscaled[1]
    output = centroid_unscaled[2]
    waste = centroid_unscaled[3]
    has_waste = centroid_unscaled[7]

    parts: list[str] = []

    if has_waste > 0.5:
        parts.append("with detected waste")
    elif ctx_growth > 0.08:
        parts.append("active context-building")
    elif ctx_resend > 0.93:
        parts.append("high context re-send")
    elif ctx_resend > 0.85:
        parts.append("moderate context re-send")
    else:
        parts.append("lower context re-send")

    if output > 0.025:
        parts.append("output-intensive")

    return ", ".join(parts) if parts else "balanced"


def _size_label(centroid_unscaled: np.ndarray, global_medians: np.ndarray) -> str:
    """Describe session size relative to the corpus median."""
    log_rt = centroid_unscaled[4]
    global_log_rt = global_medians[4]

    if log_rt > global_log_rt + 0.7:
        return "large"
    elif log_rt < global_log_rt - 0.7:
        return "small"
    else:
        return "medium"


def _build_archetype_name(
    centroid_unscaled: np.ndarray,
    global_medians: np.ndarray,
    cluster_idx: int,
) -> str:
    """Build a descriptive archetype name from a cluster centroid.

    Format: "[size] [attribution-shape] sessions"
    All terms derived from measured centroid feature values, not quality labels.
    """
    attr_shape = _attribution_shape(centroid_unscaled)
    size = _size_label(centroid_unscaled, global_medians)
    return f"{size} {attr_shape} sessions"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ArchetypeCluster:
    """Descriptor for one cluster archetype."""

    cluster_id: int
    name: str               # derived from dominant measured features
    size: int               # number of sessions in this cluster
    fraction: float         # size / total N
    centroid_unscaled: np.ndarray   # centroid in original feature space
    centroid_scaled: np.ndarray     # centroid in scaled feature space (for distance)

    # Dominant features (top features by absolute deviation from global mean in scaled space)
    dominant_features: list[dict]   # [{"name": str, "label": str, "value_unscaled": float, "z_from_global": float}]

    # Per-task-type breakdown in this cluster
    task_type_counts: dict[str, int]


@dataclasses.dataclass
class ClusteringResult:
    """Full result of the clustering analysis."""

    # Metadata
    n_sessions: int
    k: int                      # chosen k
    valid: bool                 # True if silhouette >= SILHOUETTE_WEAK_THRESHOLD
    status: str                 # human-readable status message
    domain_of_validity: str

    # Validity metrics
    silhouette: float
    silhouette_stability_mean: float
    silhouette_stability_cv: float
    stable: bool                # CV < threshold

    # Clusters
    archetypes: list[ArchetypeCluster]

    # Per-session assignment
    session_ids: list[str]
    labels: list[int]           # cluster label per session (same order as session_ids)
    distances_to_centroid: list[float]  # scaled-space distance from assigned centroid

    # Fitted scaler (needed by anomaly.py)
    scaler: StandardScaler


# ---------------------------------------------------------------------------
# Core clustering function
# ---------------------------------------------------------------------------


def run_clustering(
    features: list[SessionFeatures],
    X: np.ndarray,
    *,
    random_state: int = 42,
    verbose: bool = False,
) -> ClusteringResult:
    """Run validated KMeans clustering on the session feature matrix.

    Steps:
    1. Scale features (StandardScaler — zero mean, unit variance per feature).
    2. Evaluate k=2..8 by silhouette score (n_init=30 restarts each).
    3. Choose k with highest silhouette.
    4. Run stability check (10 seeds) at chosen k.
    5. Build archetype descriptors from cluster centroids.
    6. Assign sessions and compute centroid distances.

    Returns a ClusteringResult with validity reported honestly.
    """
    n = len(features)
    dov = (
        f"Clustering over {n} content sessions from your store. "
        "DESCRIPTIVE ONLY — describes measured patterns in this corpus; "
        "does not predict future sessions or judge session quality. "
        "Single-developer corpus: patterns reflect YOUR workflow, not population norms."
    )

    if n < MIN_SESSIONS_FOR_CLUSTERING:
        return ClusteringResult(
            n_sessions=n, k=0, valid=False,
            status=f"Too few content sessions ({n} < {MIN_SESSIONS_FOR_CLUSTERING}) for clustering.",
            domain_of_validity=dov,
            silhouette=0.0, silhouette_stability_mean=0.0, silhouette_stability_cv=0.0,
            stable=False, archetypes=[], session_ids=[sf.session_id for sf in features],
            labels=[], distances_to_centroid=[], scaler=StandardScaler(),
        )

    # --- Scale ---
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # --- k selection by silhouette ---
    k_min, k_max = K_RANGE
    k_max = min(k_max, n // 10)  # don't allow k > n/10 (tiny clusters)
    k_max = max(k_max, k_min)

    best_k = k_min
    best_sil = -1.0
    sil_by_k: dict[int, float] = {}

    for k in range(k_min, k_max + 1):
        km = KMeans(n_clusters=k, n_init=N_INIT, random_state=random_state, max_iter=500)
        labels = km.fit_predict(X_scaled)
        # silhouette requires at least 2 labels and at least 2 unique labels
        if len(set(labels)) < 2:
            sil_by_k[k] = -1.0
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sil = float(silhouette_score(X_scaled, labels, random_state=random_state))
        sil_by_k[k] = sil
        if sil > best_sil:
            best_sil = sil
            best_k = k

    if verbose:
        print(f"[cluster] silhouette by k: {sil_by_k}")
        print(f"[cluster] chosen k={best_k}, silhouette={best_sil:.4f}")

    # --- Stability check at best_k ---
    sil_stability: list[float] = []
    for seed in STABILITY_SEEDS:
        km_s = KMeans(n_clusters=best_k, n_init=N_INIT, random_state=seed, max_iter=500)
        lbl_s = km_s.fit_predict(X_scaled)
        if len(set(lbl_s)) < 2:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sil_s = float(silhouette_score(X_scaled, lbl_s, random_state=seed))
        sil_stability.append(sil_s)

    stab_mean = float(np.mean(sil_stability)) if sil_stability else best_sil
    stab_cv = float(np.std(sil_stability) / (stab_mean + 1e-9)) if len(sil_stability) > 1 else 0.0
    stable = stab_cv < STABILITY_CV_THRESHOLD

    if verbose:
        print(f"[cluster] stability: mean={stab_mean:.4f}, CV={stab_cv:.4f}, stable={stable}")

    # --- Determine validity status ---
    valid = best_sil >= SILHOUETTE_WEAK_THRESHOLD
    if best_sil >= SILHOUETTE_STABLE_THRESHOLD:
        if stable:
            status = (
                f"k={best_k} clusters found, silhouette={best_sil:.3f} (meaningful), "
                f"stability CV={stab_cv:.3f} (stable). Archetypes reflect real patterns."
            )
        else:
            status = (
                f"k={best_k} clusters found, silhouette={best_sil:.3f} (meaningful) but "
                f"stability CV={stab_cv:.3f} ≥ {STABILITY_CV_THRESHOLD} (variable across seeds). "
                "Archetypes are suggestive but may shift with different seeds."
            )
    elif best_sil >= SILHOUETTE_WEAK_THRESHOLD:
        status = (
            f"k={best_k} clusters found, silhouette={best_sil:.3f} (weak structure). "
            "Sessions show some tendency to group but clusters overlap. "
            "Archetypes represent central tendencies, not tight groups."
        )
    else:
        status = (
            f"No stable clusters found (best silhouette={best_sil:.3f} < {SILHOUETTE_WEAK_THRESHOLD}). "
            f"The {n} content sessions do not form clearly distinct groups in the feature space. "
            "Archetype descriptions are statistical summaries of the full corpus, not distinct clusters."
        )

    # --- Final fit at best_k ---
    km_final = KMeans(n_clusters=best_k, n_init=N_INIT, random_state=random_state, max_iter=500)
    labels_final = km_final.fit_predict(X_scaled)

    # Centroid distances
    centers_scaled = km_final.cluster_centers_
    distances = [
        float(np.linalg.norm(X_scaled[i] - centers_scaled[labels_final[i]]))
        for i in range(n)
    ]

    # --- Build archetype descriptors ---
    global_mean_scaled = np.mean(X_scaled, axis=0)
    global_medians_unscaled = np.median(X, axis=0)

    archetypes: list[ArchetypeCluster] = []
    for k_idx in range(best_k):
        mask = labels_final == k_idx
        cluster_size = int(mask.sum())
        centroid_scaled = centers_scaled[k_idx]
        # Unscale the centroid for human-readable feature values
        centroid_unscaled = scaler.inverse_transform(centroid_scaled.reshape(1, -1))[0]

        # Dominant features: largest absolute z-score deviation from global mean
        z_from_global = centroid_scaled - global_mean_scaled
        top_feat_idxs = np.argsort(np.abs(z_from_global))[::-1][:5]
        dominant_features = [
            {
                "name": FEATURE_NAMES[i],
                "label": FEATURE_LABELS.get(FEATURE_NAMES[i], FEATURE_NAMES[i]),
                "value_unscaled": float(centroid_unscaled[i]),
                "z_from_global": float(z_from_global[i]),
            }
            for i in top_feat_idxs
        ]

        # Task type breakdown
        task_type_counts: dict[str, int] = {}
        for i, sf in enumerate(features):
            if labels_final[i] == k_idx:
                task_type_counts[sf.task_type] = task_type_counts.get(sf.task_type, 0) + 1

        name = _build_archetype_name(centroid_unscaled, global_medians_unscaled, k_idx)

        archetypes.append(
            ArchetypeCluster(
                cluster_id=k_idx,
                name=name,
                size=cluster_size,
                fraction=cluster_size / n,
                centroid_unscaled=centroid_unscaled,
                centroid_scaled=centroid_scaled,
                dominant_features=dominant_features,
                task_type_counts=task_type_counts,
            )
        )

    # Sort archetypes by size descending
    archetypes.sort(key=lambda a: a.size, reverse=True)

    return ClusteringResult(
        n_sessions=n,
        k=best_k,
        valid=valid,
        status=status,
        domain_of_validity=dov,
        silhouette=best_sil,
        silhouette_stability_mean=stab_mean,
        silhouette_stability_cv=stab_cv,
        stable=stable,
        archetypes=archetypes,
        session_ids=[sf.session_id for sf in features],
        labels=labels_final.tolist(),
        distances_to_centroid=distances,
        scaler=scaler,
    )


__all__ = [
    "ArchetypeCluster",
    "ClusteringResult",
    "MIN_SESSIONS_FOR_CLUSTERING",
    "SILHOUETTE_STABLE_THRESHOLD",
    "SILHOUETTE_WEAK_THRESHOLD",
    "run_clustering",
]
