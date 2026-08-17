from __future__ import annotations

"""tests/test_cluster_validity.py — ML clustering validation tests.

Verifies that the clustering produces validated output (real silhouette, stable
assignment) or honestly reports no-stable-clusters when the data lacks structure.
"""

import numpy as np
import pytest

from tes.intelligence.cluster import (
    SILHOUETTE_STABLE_THRESHOLD,
    SILHOUETTE_WEAK_THRESHOLD,
    ClusteringResult,
    run_clustering,
)
from tes.intelligence.features import (
    FEATURE_NAMES,
    SessionFeatures,
    build_feature_matrix,
)
from tes.store import list_sessions, open_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MIN_REAL_CORPUS_FOR_VALIDITY_TESTS = 100  # matches test_real_corpus_yields_content_sessions


def _load_real_corpus() -> tuple[list[SessionFeatures], np.ndarray, dict[str, int]]:
    """Load the real session corpus from the live store.

    UU2: db_path is resolved explicitly here (this is a legitimate top-level
    entry point -- these tests intentionally exercise this machine's own real
    corpus) rather than relying on a downstream function's own default.
    """
    from tes.store import resolve_db_path

    conn = open_db(resolve_db_path(None))
    rows = list_sessions(conn, limit=5000, offset=0)
    conn.close()
    features, X, diagnostics = build_feature_matrix(rows, verbose=False)
    return features, X, diagnostics


def _fake_features(n: int, n_feats: int = len(FEATURE_NAMES)) -> tuple[list[SessionFeatures], np.ndarray]:
    """Build minimal fake SessionFeatures + random X for structural tests."""
    rng = np.random.default_rng(42)
    X = rng.standard_normal((n, n_feats))
    feats = [
        SessionFeatures(
            session_id=f"fake-{i}",
            task_type="debug-fix",
            real_tokens=100_000,
            turn_count=50,
            session_cost_usd=1.0,
            waste_event_count=0,
            context_resend_pct=0.9,
            context_growth_pct=0.05,
            output_pct=0.04,
            waste_pct=0.0,
            fresh_input_pct=0.01,
            vector=X[i],
        )
        for i in range(n)
    ]
    return feats, X


# ---------------------------------------------------------------------------
# Feature extraction tests
# ---------------------------------------------------------------------------


def _skip_reason_for_thin_corpus(n_features: int, diagnostics: dict[str, int]) -> str:
    """UU1: name the real cause (legacy rows vs. a genuinely thin corpus) in
    the skip reason, same distinction tes.intelligence.cache's user-facing
    message makes -- see its SS1 note."""
    n_no_source = diagnostics.get("n_no_source", 0)
    if n_no_source > 0:
        return (
            f"Real corpus has only {n_features} content sessions with reachable "
            f"attribution (< {MIN_REAL_CORPUS_FOR_VALIDITY_TESTS} needed) -- "
            f"{n_no_source} previously-scored session(s) can't count because their "
            "source transcript no longer exists on disk. Not a clustering-quality "
            "regression; re-run once 100+ sessions are scored under a version that "
            "persists attribution at score time."
        )
    return (
        f"Real corpus has only {n_features} content sessions "
        f"(< {MIN_REAL_CORPUS_FOR_VALIDITY_TESTS} needed) for a meaningful clustering-"
        "quality check on this machine."
    )


class TestFeatureExtraction:
    def test_real_corpus_yields_content_sessions(self):
        features, X, diagnostics = _load_real_corpus()
        if len(features) < MIN_REAL_CORPUS_FOR_VALIDITY_TESTS:
            pytest.skip(_skip_reason_for_thin_corpus(len(features), diagnostics))
        assert len(features) >= MIN_REAL_CORPUS_FOR_VALIDITY_TESTS

    def test_feature_matrix_shape(self):
        features, X, _diagnostics = _load_real_corpus()
        assert X.shape == (len(features), len(FEATURE_NAMES))

    def test_feature_names_count(self):
        assert len(FEATURE_NAMES) == 8

    def test_all_sessions_have_real_tokens(self):
        features, X, _diagnostics = _load_real_corpus()
        assert all(sf.real_tokens > 0 for sf in features), "Stub sessions slipped through"

    def test_attribution_pcts_bounded(self):
        features, X, _diagnostics = _load_real_corpus()
        for sf in features:
            total = (
                sf.context_resend_pct
                + sf.context_growth_pct
                + sf.output_pct
                + sf.waste_pct
                + sf.fresh_input_pct
            )
            assert total <= 1.0 + 1e-9, f"Attribution pcts sum > 1: {total}"
            assert total >= 0.0, f"Attribution pcts sum < 0: {total}"

    def test_feature_vector_no_nan(self):
        features, X, _diagnostics = _load_real_corpus()
        assert not np.any(np.isnan(X)), "NaN values in feature matrix"

    def test_task_type_not_in_feature_vector(self):
        """task_type is metadata, not a clustering feature — confirmed absent from vector."""
        for name in FEATURE_NAMES:
            assert not name.startswith("task_type_"), (
                f"task_type found in feature vector: {name}. "
                "task_type should be metadata, not a clustering feature."
            )


# ---------------------------------------------------------------------------
# Clustering validity tests
# ---------------------------------------------------------------------------


class TestClusteringValidity:
    @pytest.fixture(scope="class")
    def real_result(self):
        """UU1: skip (not fail) when the real corpus can't support a
        clustering-quality check right now -- this fixture feeds every test
        in this class, so the skip happens once here rather than repeated in
        each. Distinguishes 'can't evaluate' (too few content sessions) from
        'evaluated and it's bad' (a real quality regression, which must
        still fail loudly, not skip)."""
        features, X, diagnostics = _load_real_corpus()
        if len(features) < MIN_REAL_CORPUS_FOR_VALIDITY_TESTS:
            pytest.skip(_skip_reason_for_thin_corpus(len(features), diagnostics))
        return run_clustering(features, X, random_state=42)

    def test_clustering_produces_valid_result(self, real_result: ClusteringResult):
        """The real corpus should produce meaningful clusters (silhouette >= 0.20)."""
        assert real_result.valid, (
            f"Clustering is not valid. Status: {real_result.status}. "
            f"Silhouette: {real_result.silhouette:.4f} (threshold: {SILHOUETTE_WEAK_THRESHOLD})"
        )

    def test_silhouette_is_meaningful(self, real_result: ClusteringResult):
        """Silhouette should be >= SILHOUETTE_STABLE_THRESHOLD for portfolio-grade results."""
        assert real_result.silhouette >= SILHOUETTE_STABLE_THRESHOLD, (
            f"Silhouette {real_result.silhouette:.4f} < {SILHOUETTE_STABLE_THRESHOLD}. "
            "Clusters may be noise rather than real patterns."
        )

    def test_clustering_is_stable(self, real_result: ClusteringResult):
        """Different random seeds should converge to similar silhouette scores."""
        assert real_result.stable, (
            f"Clustering is unstable: CV={real_result.silhouette_stability_cv:.4f} "
            f">= {0.15}. Archetypes may not be reproducible."
        )

    def test_cluster_count_is_bounded(self, real_result: ClusteringResult):
        """k should be in the range [2, 8]."""
        assert 2 <= real_result.k <= 8

    def test_session_count_matches_features(self, real_result: ClusteringResult):
        features, _X, _diagnostics = _load_real_corpus()
        assert real_result.n_sessions == len(features)
        assert len(real_result.labels) == len(features)
        assert len(real_result.session_ids) == len(features)

    def test_archetype_names_are_non_empty(self, real_result: ClusteringResult):
        for archetype in real_result.archetypes:
            assert archetype.name, f"Cluster {archetype.cluster_id} has empty name"
            assert len(archetype.name) > 5, f"Archetype name too short: {archetype.name!r}"

    def test_archetype_sizes_sum_to_n(self, real_result: ClusteringResult):
        total = sum(a.size for a in real_result.archetypes)
        assert total == real_result.n_sessions, (
            f"Archetype sizes sum to {total}, expected {real_result.n_sessions}"
        )

    def test_dominant_features_are_valid_feature_names(self, real_result: ClusteringResult):
        for archetype in real_result.archetypes:
            for df in archetype.dominant_features:
                assert df["name"] in FEATURE_NAMES, (
                    f"Unknown dominant feature: {df['name']}"
                )

    def test_archetype_names_not_quality_labels(self, real_result: ClusteringResult):
        """Names must be descriptive, not evaluative quality labels."""
        forbidden = ["good", "bad", "wasteful", "productive", "inefficient", "efficient"]
        for a in real_result.archetypes:
            name_lower = a.name.lower()
            for word in forbidden:
                assert word not in name_lower, (
                    f"Archetype name contains evaluative label '{word}': {a.name!r}"
                )

    def test_domain_of_validity_present(self, real_result: ClusteringResult):
        assert real_result.domain_of_validity
        assert "descriptive" in real_result.domain_of_validity.lower()

    def test_no_stable_clusters_status_on_random_data(self):
        """When fed truly random data, clustering should report weak or no structure."""
        feats, X_rand = _fake_features(50)
        result = run_clustering(feats, X_rand, random_state=42)
        # Random data typically gets silhouette well below 0.20
        # We can't guarantee it fails (depends on random seed), but we check
        # the status message is honest when silhouette is low
        if result.silhouette < SILHOUETTE_WEAK_THRESHOLD:
            assert not result.valid
            assert "no stable clusters" in result.status.lower() or "not valid" in result.status.lower() or result.k == 0

    def test_too_few_sessions_returns_invalid(self):
        """Fewer than MIN_SESSIONS should return an invalid result with clear status."""
        from tes.intelligence.cluster import MIN_SESSIONS_FOR_CLUSTERING
        feats, X_small = _fake_features(MIN_SESSIONS_FOR_CLUSTERING - 1)
        result = run_clustering(feats, X_small, random_state=42)
        assert not result.valid
        assert result.k == 0
        assert "too few" in result.status.lower()
