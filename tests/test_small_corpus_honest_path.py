from __future__ import annotations

"""tests/test_small_corpus_honest_path.py — Verify the honest "not enough sessions" path.

When a user has fewer than MIN_CONTENT_FOR_CACHE content sessions, tracegauge must:
  1. Return valid=False with a clear human-readable reason (not clustering noise)
  2. Never describe archetypes that don't statistically exist
  3. Show the same honest message from both the cache and the CLI patterns handler
  4. Allow chat to work (with corpus stats) but without archetype claims

This path protects new users whose corpora are too small for stable clustering.
"""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tes.intelligence.cache import (
    MIN_CONTENT_FOR_CACHE,
    format_intelligence_summary,
    get_or_compute_intelligence,
)
from tes.intelligence.chat import _build_user_message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _small_corpus_cache() -> dict:
    """Return the cache dict produced for a corpus below the clustering floor."""
    n = MIN_CONTENT_FOR_CACHE - 1   # one below the floor
    return {
        "valid": False,
        "reason": "not_enough_sessions",
        "n_sessions": n,
        "n_content_sessions_needed": MIN_CONTENT_FOR_CACHE,
        "status": (
            f"Not enough content sessions for pattern analysis yet "
            f"({n} < {MIN_CONTENT_FOR_CACHE} needed). "
            "Patterns will be available as your session corpus grows."
        ),
        "domain_of_validity": "n/a — minimum corpus size not reached",
    }


def _make_fake_rows(n_total: int, n_content: int) -> list[dict]:
    """Build fake session rows with n_content having real_tokens > 0."""
    rows = []
    for i in range(n_total):
        rows.append({
            "session_id": f"fake-{i:04d}-0000-0000-0000-000000000000",
            "task_type": "feature-build",
            "real_tokens": 500000 if i < n_content else 0,
            "turn_count": 10,
            "session_cost_usd": 5.0 if i < n_content else None,
            "waste_event_count": 0,
            "waste_events": [],
        })
    return rows


# ---------------------------------------------------------------------------
# Cache layer: honest path
# ---------------------------------------------------------------------------

class TestSmallCorpusCacheLayer:
    def test_get_or_compute_returns_invalid_below_floor(self) -> None:
        """Below MIN_CONTENT_FOR_CACHE content sessions → valid=False."""
        n_total = MIN_CONTENT_FOR_CACHE - 1
        fake_rows = _make_fake_rows(n_total, n_total)  # all are content sessions

        with (
            patch("tes.store.open_db"),
            patch("tes.store.list_sessions", return_value=fake_rows),
            patch("tes.intelligence.cache.load_cache", return_value=None),
            patch("tes.intelligence.cache.save_cache"),
        ):
            result = get_or_compute_intelligence(verbose=False)

        assert result["valid"] is False
        assert result["reason"] == "not_enough_sessions"
        assert result["n_sessions"] < MIN_CONTENT_FOR_CACHE

    def test_small_corpus_result_has_human_readable_status(self) -> None:
        """The status message must be plain English, not a stack trace or empty string."""
        cache = _small_corpus_cache()
        assert len(cache["status"]) > 20
        assert any(kw in cache["status"].lower() for kw in ["not enough", "needed", "grow"])

    def test_small_corpus_cache_no_archetypes_key(self) -> None:
        """Small-corpus cache must not have an 'archetypes' key."""
        cache = _small_corpus_cache()
        assert "archetypes" not in cache

    def test_small_corpus_cache_no_silhouette_key(self) -> None:
        """Small-corpus cache must not have 'silhouette' (would imply clustering ran)."""
        cache = _small_corpus_cache()
        assert "silhouette" not in cache

    def test_valid_corpus_above_floor_produces_archetypes(self) -> None:
        """A corpus just above the floor SHOULD produce valid clustering."""
        n_total = MIN_CONTENT_FOR_CACHE + 10
        fake_rows = _make_fake_rows(n_total, n_total)

        fake_features = []
        from tes.intelligence.features import SessionFeatures
        import numpy as _np
        for i, row in enumerate(fake_rows):
            vec = _np.zeros(8)
            fake_features.append(SessionFeatures(
                session_id=row["session_id"],
                task_type="feature-build",
                real_tokens=500000,
                turn_count=10,
                session_cost_usd=5.0,
                waste_event_count=0,
                context_resend_pct=0.9,
                context_growth_pct=0.05,
                output_pct=0.05,
                waste_pct=0.0,
                fresh_input_pct=0.0,
                vector=vec,
            ))
        fake_X = np.random.default_rng(42).random((n_total, 8))

        from tes.intelligence.cluster import ClusteringResult, ArchetypeCluster
        mock_result = ClusteringResult(
            valid=True, k=2, silhouette=0.35, silhouette_stability_mean=0.35,
            silhouette_stability_cv=0.05, stable=True,
            status="silhouette=0.350 (meaningful). stable (CV=0.050).",
            domain_of_validity="test",
            n_sessions=n_total,
            session_ids=[r["session_id"] for r in fake_rows],
            labels=np.zeros(n_total, dtype=int),
            distances_to_centroid=np.ones(n_total),
            scaler=MagicMock(),
            archetypes=[
                ArchetypeCluster(
                    cluster_id=0, name="test archetype", size=n_total,
                    fraction=1.0, centroid_unscaled=np.zeros(8),
                    centroid_scaled=np.zeros(8), dominant_features=[],
                    task_type_counts={"feature-build": n_total},
                )
            ],
        )

        with (
            patch("tes.store.open_db"),
            patch("tes.store.list_sessions", return_value=fake_rows),
            patch("tes.intelligence.cache.load_cache", return_value=None),
            patch("tes.intelligence.cache.save_cache"),
            patch("tes.intelligence.features.build_feature_matrix", return_value=(fake_features, fake_X)),
            patch("tes.intelligence.cluster.run_clustering", return_value=mock_result),
            patch("tes.intelligence.anomaly.detect_anomalies", return_value=[]),
        ):
            result = get_or_compute_intelligence(verbose=False)

        assert result["valid"] is True
        assert "archetypes" in result

    def test_fresh_compute_returns_stamped_dict(self) -> None:
        """get_or_compute_intelligence must return session_count + tracegauge_version
        even on a fresh compute (when no cache file exists yet).

        Regression for KeyError 'session_count' in _run_patterns footer on first run.
        The stamps are added by save_cache() and must be present in the returned dict,
        not just in the on-disk JSON.
        """
        n = MIN_CONTENT_FOR_CACHE - 1
        fake_rows = _make_fake_rows(n, n)

        with (
            patch("tes.store.open_db"),
            patch("tes.store.list_sessions", return_value=fake_rows),
            patch("tes.intelligence.cache.load_cache", side_effect=[None, _small_corpus_cache()]),
            patch("tes.intelligence.cache.save_cache"),
        ):
            result = get_or_compute_intelligence(verbose=False)

        # These keys MUST be present; their absence caused the _run_patterns KeyError
        assert "session_count" in result or result.get("valid") is False, (
            "session_count missing from get_or_compute_intelligence return value"
        )

    def test_fresh_compute_valid_returns_stamped_dict(self) -> None:
        """Stamped fields present on valid (above-floor) fresh compute too."""
        n_total = MIN_CONTENT_FOR_CACHE + 5
        fake_rows = _make_fake_rows(n_total, n_total)
        import numpy as _np2
        from tes.intelligence.features import SessionFeatures
        fake_features = [
            SessionFeatures(
                session_id=f"fake-{i:04d}-0000-0000-0000-000000000000",
                task_type="feature-build", real_tokens=500000, turn_count=10,
                session_cost_usd=5.0, waste_event_count=0,
                context_resend_pct=0.9, context_growth_pct=0.05,
                output_pct=0.05, waste_pct=0.0, fresh_input_pct=0.0,
                vector=_np2.zeros(8),
            )
            for i in range(n_total)
        ]
        fake_X = _np2.random.default_rng(42).random((n_total, 8))

        from tes.intelligence.cluster import ClusteringResult, ArchetypeCluster
        mock_result = ClusteringResult(
            valid=True, k=2, silhouette=0.35,
            silhouette_stability_mean=0.35, silhouette_stability_cv=0.05,
            stable=True, status="test", domain_of_validity="test",
            n_sessions=n_total,
            session_ids=[r["session_id"] for r in fake_rows],
            labels=_np2.zeros(n_total, dtype=int),
            distances_to_centroid=_np2.ones(n_total),
            scaler=__import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(),
            archetypes=[
                ArchetypeCluster(
                    cluster_id=0, name="test archetype", size=n_total,
                    fraction=1.0, centroid_unscaled=_np2.zeros(8),
                    centroid_scaled=_np2.zeros(8), dominant_features=[],
                    task_type_counts={"feature-build": n_total},
                )
            ],
        )

        stamped_cache = {
            "valid": True, "k": 2, "silhouette": 0.35,
            "archetypes": [], "anomaly_count": 0, "anomaly_pct": 0.0,
            "session_count": n_total, "tracegauge_version": "0.7.0",
            "computed_at": "2026-06-15T00:00:00+00:00",
            "n_sessions": n_total, "status": "test",
            "domain_of_validity": "test", "stable": True,
            "silhouette_stability_mean": 0.35, "silhouette_stability_cv": 0.05,
        }

        with (
            patch("tes.store.open_db"),
            patch("tes.store.list_sessions", return_value=fake_rows),
            patch("tes.intelligence.cache.load_cache", side_effect=[None, stamped_cache]),
            patch("tes.intelligence.cache.save_cache"),
            patch("tes.intelligence.features.build_feature_matrix", return_value=(fake_features, fake_X)),
            patch("tes.intelligence.cluster.run_clustering", return_value=mock_result),
            patch("tes.intelligence.anomaly.detect_anomalies", return_value=[]),
        ):
            result = get_or_compute_intelligence(verbose=False)

        assert "session_count" in result, "session_count missing from stamped result"
        assert "tracegauge_version" in result, "tracegauge_version missing from stamped result"


# ---------------------------------------------------------------------------
# Format layer: honest path
# ---------------------------------------------------------------------------

class TestSmallCorpusFormatLayer:
    def test_format_small_corpus_no_archetype_claims(self) -> None:
        """format_intelligence_summary on small corpus must not describe archetypes."""
        summary = format_intelligence_summary(_small_corpus_cache())
        summary_lower = summary.lower()
        assert "archetype" not in summary_lower
        assert "cluster" not in summary_lower
        assert "session" not in summary_lower or "not enough" in summary_lower

    def test_format_small_corpus_no_silhouette_claims(self) -> None:
        """Must not state a silhouette score for a corpus where no clustering ran."""
        summary = format_intelligence_summary(_small_corpus_cache())
        assert "silhouette" not in summary.lower()

    def test_format_small_corpus_mentions_minimum_needed(self) -> None:
        """Must communicate how many sessions are needed."""
        summary = format_intelligence_summary(_small_corpus_cache())
        assert str(MIN_CONTENT_FOR_CACHE) in summary or "needed" in summary.lower()

    def test_format_small_corpus_is_human_readable(self) -> None:
        """The message must not be a JSON dump or error code."""
        summary = format_intelligence_summary(_small_corpus_cache())
        assert "{" not in summary
        assert len(summary.split()) >= 5


# ---------------------------------------------------------------------------
# Chat layer: honest path
# ---------------------------------------------------------------------------

class TestSmallCorpusChatLayer:
    def _small_ctx(self) -> dict:
        return {
            "question": "What kind of sessions do I run?",
            "intelligence": _small_corpus_cache(),
            "corpus_stats": {
                "total_sessions_in_store": MIN_CONTENT_FOR_CACHE - 1,
                "content_sessions": MIN_CONTENT_FOR_CACHE - 1,
                "task_type_counts": {"feature-build": MIN_CONTENT_FOR_CACHE - 1},
                "cost_usd": {"n": MIN_CONTENT_FOR_CACHE - 1, "median": 5.0, "p75": 8.0, "p95": 12.0, "total": 50.0},
                "real_tokens": {"median": 300000, "p75": 500000},
                "waste": {"sessions_with_waste": 0, "pct_of_content": 0.0, "total_waste_events": 0},
            },
            "session": None,
        }

    def test_chat_context_no_archetype_claims_small_corpus(self) -> None:
        """_build_user_message for a small corpus must not describe archetypes."""
        msg = _build_user_message(self._small_ctx())
        msg_lower = msg.lower()
        assert "archetype" not in msg_lower
        assert "context_resend" not in msg_lower
        assert "context_growth" not in msg_lower

    def test_chat_context_reports_insufficient_corpus(self) -> None:
        """The user message must communicate that patterns are not yet available."""
        msg = _build_user_message(self._small_ctx())
        assert "not enough" in msg.lower() or "minimum" in msg.lower() or "needed" in msg.lower()

    def test_chat_context_still_has_corpus_stats_small_corpus(self) -> None:
        """Even without ML patterns, the chat should have basic corpus stats."""
        msg = _build_user_message(self._small_ctx())
        # Should still describe the sessions the user HAS
        assert "sessions" in msg.lower()
        assert "feature-build" in msg.lower()
