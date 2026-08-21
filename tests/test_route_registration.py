"""test_route_registration.py — Assert /patterns and /ask are on the production app.

This test specifically guards against the "tests-pass-but-real-server-fails" gap:
  - pytest runs from the repo dir (repo is first on sys.path, repo code is imported)
  - tes.exe / tes serve uses site-packages (PyPI-installed code)

A test that merely GETs a route via a test-fixture app passes in BOTH cases, because
the fixture creates a fresh app from whatever `tes.web.server` is imported — and in
pytest that is always the repo. So the fixture test cannot catch a missing route on
the installed version.

What this test does instead:
  1. Imports `create_app` and `start_server` from tes.web.server (same import path
     that `tes serve` uses).
  2. Calls create_app() — exactly what start_server does.
  3. Inspects url_map — the Flask route table that Werkzeug uses to dispatch requests.
     If a route is not in url_map, it 404s on a real server regardless of what any
     other test does.
  4. Asserts every new 0.8.0 route is present, plus an HTTP-level GET via test_client
     to confirm the route is reachable (not just registered-but-broken at import).

If this test passes, the code in the repo registers /patterns and /ask. If it fails,
the routes are missing from create_app and the real server would 404 — same as the
regression the user hit.

Note: this test does NOT protect against repo-vs-installed divergence (that is an
infrastructure problem solved by `pip install -e .` or publishing). It protects
against code bugs where routes are accidentally omitted from create_app.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tes.store import open_db
from tes.web.server import ServerConfig, create_app, start_server  # same imports as `tes serve`

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def minimal_app(tmp_path: Path):
    """App created with the EXACT same call as start_server makes internally."""
    cfg = ServerConfig(db_path=tmp_path / "routes.db")
    open_db(cfg.db_path).close()  # create the DB file (start_server assumes it exists)
    return create_app(cfg)  # ← identical call to what start_server makes


# ---------------------------------------------------------------------------
# Route-table assertions (url_map checks)
# ---------------------------------------------------------------------------


class TestProductionRouteTable:
    """Assert routes are in the Flask url_map that Werkzeug uses to dispatch.

    A 404 from the real server means the path is missing from url_map.
    These assertions catch that before deploy.
    """

    _EXPECTED_ROUTES = {
        "/",
        "/session/<session_id>",
        "/trends",
        "/baseline-status",
        "/patterns",  # 0.8.0 — new
        "/ask",  # 0.8.0 — new
    }

    def test_all_expected_routes_registered(self, minimal_app) -> None:
        registered = {
            str(rule)
            for rule in minimal_app.url_map.iter_rules()
            if not str(rule).startswith("/static")
        }
        for route in self._EXPECTED_ROUTES:
            assert route in registered, (
                f"Route {route!r} is missing from create_app url_map.\n"
                f"Registered routes: {sorted(registered)}\n"
                "If this test fails, `tes serve` will 404 on that path."
            )

    def test_patterns_route_present(self, minimal_app) -> None:
        rules = {str(r) for r in minimal_app.url_map.iter_rules()}
        assert "/patterns" in rules

    def test_ask_route_present(self, minimal_app) -> None:
        rules = {str(r) for r in minimal_app.url_map.iter_rules()}
        assert "/ask" in rules

    def test_ask_route_accepts_post(self, minimal_app) -> None:
        """The /ask route must only accept POST, not GET."""
        for rule in minimal_app.url_map.iter_rules():
            if str(rule) == "/ask":
                assert "POST" in rule.methods, "/ask must accept POST"
                assert "GET" not in rule.methods, "/ask must not accept GET"
                break
        else:
            pytest.fail("/ask not found in url_map")

    def test_no_extra_routes_added_accidentally(self, minimal_app) -> None:
        """Guard against accidentally registering admin/debug routes."""
        registered = {
            str(r) for r in minimal_app.url_map.iter_rules() if not str(r).startswith("/static")
        }
        forbidden_prefixes = ("/admin", "/debug", "/internal", "/api/v")
        for route in registered:
            for prefix in forbidden_prefixes:
                assert not route.startswith(prefix), (
                    f"Unexpected route {route!r} found — may expose admin/debug surface"
                )

    def test_start_server_import_succeeds(self) -> None:
        """start_server must be importable — if its module has a syntax/import error,
        `tes serve` would crash before registering any routes."""
        # This is already proven by the import at the top of this file,
        # but make it an explicit named test so failure is obvious.
        assert callable(start_server), "start_server not callable after import"
        assert callable(create_app), "create_app not callable after import"


# ---------------------------------------------------------------------------
# HTTP-level reachability (test client GET /patterns, POST /ask)
# ---------------------------------------------------------------------------


class TestRoutesReachableViaTestClient:
    """These tests are what the existing test_web_patterns.py etc. already do.

    The difference from url_map tests: these confirm the route handler doesn't
    immediately crash (import error, missing template, etc.). Both layers needed:
    url_map confirms registration; HTTP confirms reachability.
    """

    def test_patterns_get_returns_200(self, minimal_app) -> None:
        from unittest.mock import patch

        with (
            patch(
                "tes.web.server.get_or_compute_intelligence",
                return_value={
                    "valid": False,
                    "reason": "not_enough_sessions",
                    "n_sessions": 0,
                    "n_content_sessions_needed": 30,
                    "status": "Not enough sessions.",
                    "domain_of_validity": "n/a",
                },
            ),
            patch("tes.web.server._check_ollama", return_value=False),
        ):
            with minimal_app.test_client() as c:
                resp = c.get("/patterns")
        assert resp.status_code == 200, (
            f"/patterns returned {resp.status_code} — route is registered but handler failed.\n"
            f"Body: {resp.data[:500]}"
        )

    def test_ask_post_empty_returns_400(self, minimal_app) -> None:
        """Empty POST to /ask must return 400, not 404 or 500."""
        import json

        with minimal_app.test_client() as c:
            resp = c.post(
                "/ask", data=json.dumps({"question": ""}), content_type="application/json"
            )
        assert resp.status_code == 400, (
            f"/ask returned {resp.status_code} for empty question — expected 400"
        )

    def test_ask_get_returns_405_not_404(self, minimal_app) -> None:
        """GET /ask must return 405 (method not allowed), not 404 (route missing)."""
        with minimal_app.test_client() as c:
            resp = c.get("/ask")
        assert resp.status_code == 405, (
            f"GET /ask returned {resp.status_code} — expected 405 (route registered, wrong method).\n"
            "A 404 here means /ask is not in the url_map at all."
        )
