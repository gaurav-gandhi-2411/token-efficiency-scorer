"""test_web_patterns.py — Patterns page: honest framing, floor honored, validity shown.

Tests:
  1. /patterns returns 200 in both valid and below-floor states.
  2. Below-floor (<30 sessions): shows "not enough" message, no archetype cards.
  3. Above-floor: archetypes, validity stats, DOV caveat present.
  4. Descriptive-not-predictive framing enforced (no quality labels).
  5. Ask panel present on the page.
  6. Judge status chips rendered.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tes.score import (
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
    ThreeAxisResult,
)
from tes.store import open_db, upsert_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(session_id: str, real_tokens: int = 1000) -> ThreeAxisResult:
    return ThreeAxisResult(
        session_id=session_id,
        task_type="debug-fix",
        real_tokens=real_tokens,
        scope_status="in_scope",
        baseline_available=True,
        p25=800,
        p75=1200,
        median=1000,
        band_verdict="within_band",
        interpretation="",
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        baseline_source="self",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning=None,
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        waste_event_count=0,
        waste_events=[],
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
        session_cost_usd=0.01,
        cost_approximate=False,
        cost_domain_of_validity="",
    )


def _seed_sessions(db_path: Path, n: int) -> None:
    """Insert n content sessions (real_tokens > 0) into the DB."""
    conn = open_db(db_path)
    for i in range(n):
        sid = f"pat-test-{i:04d}-aaaa-bbbb-cccc-dddddddddddd"
        r = _make_result(sid, real_tokens=1000 + i * 10)
        upsert_session(conn, r, f"/fake/{sid}.jsonl", float(i), f"hash-{i}")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app_below_floor(tmp_path: Path):
    """App with fewer than 30 content sessions (below the clustering floor)."""
    from tes.web.server import ServerConfig, create_app
    db = tmp_path / "below.db"
    _seed_sessions(db, 5)
    cfg = ServerConfig(db_path=db)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def app_above_floor(tmp_path: Path):
    """App with 35 content sessions (above the clustering floor).

    Patterns computation is mocked — we don't need real sklearn for these tests.
    """
    from tes.web.server import ServerConfig, create_app
    db = tmp_path / "above.db"
    _seed_sessions(db, 35)
    cfg = ServerConfig(db_path=db)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


# Minimal valid cache dict (mimics what get_or_compute_intelligence returns above floor)
_MOCK_CACHE_VALID = {
    "valid": True,
    "k": 2,
    "silhouette": 0.45,
    "silhouette_stability_mean": 0.44,
    "silhouette_stability_cv": 0.02,
    "stable": True,
    "status": "Stable structure found.",
    "domain_of_validity": "Descriptive clustering over 35 sessions. Silhouette=0.45.",
    "n_sessions": 35,
    "session_count": 35,
    "tracegauge_version": "0.8.0",
    "computed_at": "2026-06-15T12:00:00+00:00",
    "anomaly_count": 2,
    "anomaly_pct": 5.7,
    "archetypes": [
        {
            "cluster_id": 0,
            "name": "Standard Context Session",
            "size": 20,
            "fraction": 0.571,
            "centroid": {
                "context_resend_pct": 0.94,
                "context_growth_pct": 0.03,
                "output_pct": 0.03,
                "has_waste": 0.1,
            },
            "task_type_counts": {"debug-fix": 20},
            "dominant_features": [
                {"name": "context_resend_pct", "label": "High re-send", "value_unscaled": 0.94, "z_from_global": 2.1},
            ],
        },
        {
            "cluster_id": 1,
            "name": "Growth Session",
            "size": 15,
            "fraction": 0.429,
            "centroid": {
                "context_resend_pct": 0.85,
                "context_growth_pct": 0.10,
                "output_pct": 0.05,
                "has_waste": 0.3,
            },
            "task_type_counts": {"debug-fix": 15},
            "dominant_features": [
                {"name": "context_growth_pct", "label": "High growth", "value_unscaled": 0.10, "z_from_global": 1.8},
            ],
        },
    ],
}

_MOCK_CACHE_BELOW_FLOOR = {
    "valid": False,
    "reason": "not_enough_sessions",
    "n_sessions": 5,
    "n_content_sessions_needed": 30,
    "status": "Not enough content sessions for pattern analysis yet (5 < 30 needed).",
    "domain_of_validity": "n/a — minimum corpus size not reached",
}


# ---------------------------------------------------------------------------
# Tests: /patterns route
# ---------------------------------------------------------------------------

class TestPatternsRouteStatus:
    def test_returns_200_below_floor(self, app_below_floor) -> None:
        with patch(
            "tes.web.server.patterns.__wrapped__" if hasattr(app_below_floor, "__wrapped__") else
            "tes.web.server.get_or_compute_intelligence",
            return_value=_MOCK_CACHE_BELOW_FLOOR,
        ):
            with app_below_floor.test_client() as c:
                resp = c.get("/patterns")
        assert resp.status_code == 200

    def test_returns_200_with_mocked_valid_cache(self, app_above_floor) -> None:
        with patch(
            "tes.web.server.get_or_compute_intelligence",
            return_value=_MOCK_CACHE_VALID,
        ):
            with app_above_floor.test_client() as c:
                resp = c.get("/patterns")
        assert resp.status_code == 200


class TestPatternsFloorHonored:
    def test_below_floor_shows_not_enough_message(self, app_below_floor) -> None:
        with patch(
            "tes.web.server.get_or_compute_intelligence",
            return_value=_MOCK_CACHE_BELOW_FLOOR,
        ):
            with app_below_floor.test_client() as c:
                html = c.get("/patterns").data.decode()
        assert "Not enough sessions" in html or "not enough" in html.lower()

    def test_below_floor_shows_n_sessions(self, app_below_floor) -> None:
        with patch(
            "tes.web.server.get_or_compute_intelligence",
            return_value=_MOCK_CACHE_BELOW_FLOOR,
        ):
            with app_below_floor.test_client() as c:
                html = c.get("/patterns").data.decode()
        assert "5" in html  # current n_sessions
        assert "30" in html  # threshold

    def test_below_floor_no_archetype_grid(self, app_below_floor) -> None:
        with patch(
            "tes.web.server.get_or_compute_intelligence",
            return_value=_MOCK_CACHE_BELOW_FLOOR,
        ):
            with app_below_floor.test_client() as c:
                html = c.get("/patterns").data.decode()
        # The archetype-grid div is only rendered when cache.valid=True
        assert '<div class="archetype-grid">' not in html


class TestPatternsAboveFloor:
    def _html(self, app_above_floor) -> str:
        with patch(
            "tes.web.server.get_or_compute_intelligence",
            return_value=_MOCK_CACHE_VALID,
        ):
            with app_above_floor.test_client() as c:
                return c.get("/patterns").data.decode()

    def test_validity_silhouette_shown(self, app_above_floor) -> None:
        html = self._html(app_above_floor)
        assert "silhouette" in html.lower()
        assert "0.450" in html or "0.45" in html

    def test_archetype_names_shown(self, app_above_floor) -> None:
        html = self._html(app_above_floor)
        assert "Standard Context Session" in html
        assert "Growth Session" in html

    def test_archetype_sizes_shown(self, app_above_floor) -> None:
        html = self._html(app_above_floor)
        assert "20" in html  # cluster 0 size
        assert "15" in html  # cluster 1 size

    def test_anomaly_count_shown(self, app_above_floor) -> None:
        html = self._html(app_above_floor)
        assert "2" in html  # anomaly_count

    def test_domain_of_validity_shown(self, app_above_floor) -> None:
        html = self._html(app_above_floor)
        assert "domain_of_validity" in _MOCK_CACHE_VALID
        assert "Descriptive clustering" in html or "descriptive" in html.lower()

    def test_descriptive_not_predictive_caveat(self, app_above_floor) -> None:
        html = self._html(app_above_floor)
        assert "Descriptive only" in html or "not predictive" in html.lower() or "descriptive" in html.lower()

    def test_no_quality_labels(self, app_above_floor) -> None:
        html = self._html(app_above_floor)
        # These quality labels must NOT appear as a framing for archetypes
        for bad_label in ("inefficient", "good session", "bad session"):
            assert bad_label not in html.lower(), f"Forbidden quality label found: {bad_label!r}"
        # The honesty framing MUST say archetypes are not quality labels
        assert "not quality labels" in html or "do not rate" in html


class TestPatternsJudgeStatus:
    def test_ollama_status_shown_when_available(self, app_above_floor) -> None:
        with patch("tes.web.server.get_or_compute_intelligence", return_value=_MOCK_CACHE_VALID), \
             patch("tes.web.server._check_ollama", return_value=True):
            with app_above_floor.test_client() as c:
                html = c.get("/patterns").data.decode()
        assert "Ollama running" in html or "local inference" in html

    def test_ollama_status_shown_when_unavailable(self, app_above_floor) -> None:
        with patch("tes.web.server.get_or_compute_intelligence", return_value=_MOCK_CACHE_VALID), \
             patch("tes.web.server._check_ollama", return_value=False):
            with app_above_floor.test_client() as c:
                html = c.get("/patterns").data.decode()
        assert "Ollama not detected" in html or "not detected" in html.lower()

    def test_api_key_shown_when_present(self, app_above_floor) -> None:
        with patch("tes.web.server.get_or_compute_intelligence", return_value=_MOCK_CACHE_VALID), \
             patch("tes.web.server._check_ollama", return_value=False), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            with app_above_floor.test_client() as c:
                html = c.get("/patterns").data.decode()
        assert "consent required" in html.lower() or "ANTHROPIC_API_KEY set" in html


class TestPatternsAskPanelPresent:
    def test_ask_panel_rendered(self, app_above_floor) -> None:
        with patch("tes.web.server.get_or_compute_intelligence", return_value=_MOCK_CACHE_VALID):
            with app_above_floor.test_client() as c:
                html = c.get("/patterns").data.decode()
        assert "ask-panel" in html
        assert "ask-input" in html
        assert "/ask" in html  # fetch target

    def test_ask_panel_rendered_below_floor_too(self, app_below_floor) -> None:
        """Ask panel must appear even below floor — just limits what context has."""
        with patch("tes.web.server.get_or_compute_intelligence", return_value=_MOCK_CACHE_BELOW_FLOOR):
            with app_below_floor.test_client() as c:
                html = c.get("/patterns").data.decode()
        assert "ask-panel" in html
