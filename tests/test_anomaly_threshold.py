from __future__ import annotations

"""tests/test_anomaly_threshold.py — Principled anomaly threshold tests.

Verifies that:
- Anomaly threshold is Tukey outer fence (Q3 + 1.5*IQR), not arbitrary
- Anomaly sessions receive deviating feature attribution
- No anomalies returned when clustering is invalid
- Real corpus anomaly count is in a plausible range
"""

import numpy as np
import pytest

from tes.intelligence.anomaly import AnomalyResult, detect_anomalies, summarize_anomalies
from tes.intelligence.cluster import run_clustering
from tes.intelligence.features import FEATURE_NAMES, SessionFeatures, build_feature_matrix
from tes.store import list_sessions, open_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_corpus() -> tuple[list[SessionFeatures], np.ndarray]:
    conn = open_db()
    rows = list_sessions(conn, limit=5000, offset=0)
    conn.close()
    features, X, _diagnostics = build_feature_matrix(rows, verbose=False)
    return features, X


@pytest.fixture(scope="module")
def corpus_and_result():
    features, X = _load_corpus()
    result = run_clustering(features, X, random_state=42)
    return features, X, result


@pytest.fixture(scope="module")
def anomalies(corpus_and_result):
    features, X, result = corpus_and_result
    return detect_anomalies(features, X, result)


# ---------------------------------------------------------------------------
# Threshold tests
# ---------------------------------------------------------------------------


class TestAnomalyThreshold:
    def test_tukey_threshold_is_per_cluster(self, corpus_and_result, anomalies):
        """Each anomaly's threshold should be >= its cluster's Q3 (i.e., not a fixed value)."""
        features, X, result = corpus_and_result
        if not anomalies or not result.valid:
            pytest.skip("No anomalies to test or invalid clustering")

        # Compute Q3 per cluster from the distances
        labels = np.array(result.labels)
        distances = np.array(result.distances_to_centroid)
        q3_per_cluster: dict[int, float] = {}
        for k in range(result.k):
            mask = labels == k
            if mask.sum() >= 4:
                q3_per_cluster[k] = float(np.percentile(distances[mask], 75))

        for a in anomalies:
            if a.cluster_id in q3_per_cluster:
                assert a.cluster_threshold >= q3_per_cluster[a.cluster_id], (
                    f"Threshold {a.cluster_threshold:.3f} < Q3 {q3_per_cluster[a.cluster_id]:.3f} "
                    f"for cluster {a.cluster_id}. Tukey fence must be >= Q3."
                )

    def test_every_anomaly_exceeds_its_threshold(self, corpus_and_result, anomalies):
        """All flagged sessions must have distance > their cluster threshold."""
        if not anomalies:
            pytest.skip("No anomalies in this corpus")
        for a in anomalies:
            assert a.distance_to_centroid > a.cluster_threshold, (
                f"Session {a.session_id[:12]}: distance {a.distance_to_centroid:.3f} "
                f"<= threshold {a.cluster_threshold:.3f}"
            )

    def test_anomaly_count_in_plausible_range(self, corpus_and_result, anomalies):
        """Anomalies should be < 20% of corpus (Tukey fence should not mass-flag sessions)."""
        features, _, result = corpus_and_result
        if not result.valid:
            pytest.skip("Invalid clustering")
        pct = len(anomalies) / len(features)
        assert pct < 0.20, (
            f"{pct*100:.1f}% of sessions flagged as anomalies. "
            "Tukey fence should catch tails, not the majority."
        )

    def test_non_anomalies_below_threshold(self, corpus_and_result, anomalies):
        """Sessions NOT flagged should all have distance <= their cluster threshold."""
        features, X, result = corpus_and_result
        if not result.valid:
            pytest.skip("Invalid clustering")

        anomaly_ids = {a.session_id for a in anomalies}
        labels = np.array(result.labels)
        distances = np.array(result.distances_to_centroid)

        # Compute thresholds
        cluster_thresholds: dict[int, float] = {}
        for k in range(result.k):
            mask = labels == k
            cluster_dists = distances[mask]
            if len(cluster_dists) < 4:
                cluster_thresholds[k] = float(cluster_dists.max() * 2.0 if len(cluster_dists) > 0 else 1e9)
                continue
            q3 = float(np.percentile(cluster_dists, 75))
            iqr = q3 - float(np.percentile(cluster_dists, 25))
            cluster_thresholds[k] = q3 + 1.5 * iqr

        for i, sf in enumerate(features):
            if sf.session_id in anomaly_ids:
                continue
            k_idx = labels[i]
            threshold = cluster_thresholds.get(k_idx, float("inf"))
            assert distances[i] <= threshold + 1e-9, (
                f"Non-anomaly session {sf.session_id[:12]} has distance {distances[i]:.3f} "
                f"> threshold {threshold:.3f} but was not flagged."
            )


# ---------------------------------------------------------------------------
# Feature attribution tests
# ---------------------------------------------------------------------------


class TestAnomalyFeatureAttribution:
    def test_deviating_features_are_valid_names(self, anomalies):
        if not anomalies:
            pytest.skip("No anomalies to test")
        for a in anomalies:
            for df in a.top_deviating_features:
                assert df["name"] in FEATURE_NAMES, (
                    f"Unknown deviating feature: {df['name']}"
                )

    def test_deviating_features_count_is_bounded(self, anomalies):
        if not anomalies:
            pytest.skip("No anomalies to test")
        for a in anomalies:
            assert 1 <= len(a.top_deviating_features) <= 3, (
                f"Expected 1-3 deviating features, got {len(a.top_deviating_features)}"
            )

    def test_deviating_features_sorted_by_abs_deviation(self, anomalies):
        """Deviating features should be ordered by |deviation| descending."""
        if not anomalies:
            pytest.skip("No anomalies to test")
        for a in anomalies:
            devs = [abs(df["deviation"]) for df in a.top_deviating_features]
            assert devs == sorted(devs, reverse=True), (
                f"Deviating features not sorted by |deviation|: {devs}"
            )

    def test_anomaly_result_has_cluster_name(self, anomalies):
        if not anomalies:
            pytest.skip("No anomalies to test")
        for a in anomalies:
            assert a.cluster_name, f"Anomaly {a.session_id[:12]} has no cluster name"

    def test_anomaly_result_has_task_type(self, anomalies):
        if not anomalies:
            pytest.skip("No anomalies to test")
        for a in anomalies:
            assert a.task_type, f"Anomaly {a.session_id[:12]} has no task_type"


# ---------------------------------------------------------------------------
# Guard: no anomalies when clustering is invalid
# ---------------------------------------------------------------------------


class TestAnomalyGuards:
    def test_no_anomalies_on_invalid_clustering(self):
        """detect_anomalies should return [] when result.valid is False."""
        from tes.intelligence.cluster import ClusteringResult, MIN_SESSIONS_FOR_CLUSTERING
        from sklearn.preprocessing import StandardScaler

        # Build a result that has valid=False
        fake_result = ClusteringResult(
            n_sessions=5,
            k=0,
            valid=False,
            status="Too few sessions",
            domain_of_validity="n/a",
            silhouette=0.0,
            silhouette_stability_mean=0.0,
            silhouette_stability_cv=0.0,
            stable=False,
            archetypes=[],
            session_ids=["a", "b", "c", "d", "e"],
            labels=[],
            distances_to_centroid=[],
            scaler=StandardScaler(),
        )

        # Need minimal features + X
        feats, X = [], np.zeros((0, len(FEATURE_NAMES)))
        anomalies = detect_anomalies(feats, X, fake_result)
        assert anomalies == [], f"Expected [] from invalid result, got {anomalies}"

    def test_no_anomalies_on_empty_labels(self):
        """detect_anomalies with empty labels returns []."""
        from tes.intelligence.cluster import ClusteringResult
        from sklearn.preprocessing import StandardScaler

        result = ClusteringResult(
            n_sessions=0, k=2, valid=True,
            status="ok", domain_of_validity="n/a",
            silhouette=0.3, silhouette_stability_mean=0.3, silhouette_stability_cv=0.0,
            stable=True, archetypes=[],
            session_ids=[], labels=[],
            distances_to_centroid=[], scaler=StandardScaler(),
        )
        anomalies = detect_anomalies([], np.zeros((0, 8)), result)
        assert anomalies == []

    def test_summarize_on_empty_anomalies(self):
        summary = summarize_anomalies([], 100)
        assert "no anomalous sessions" in summary.lower()
