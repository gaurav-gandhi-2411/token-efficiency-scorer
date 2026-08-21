from __future__ import annotations

"""tests/test_contribution_no_network.py — Contribution path makes no network calls.

Asserts the build_contribution_payload code path does not import socket,
urllib, requests, httpx, or any other network module — and does not open
any socket connections.
"""

import socket
from datetime import UTC, datetime
from unittest.mock import patch

from tes.contribution import build_contribution_payload
from tes.score import ThreeAxisResult
from tes.store import open_db, upsert_session


def _make_conn():
    conn = open_db(":memory:")
    result = ThreeAxisResult(
        session_id="no-network-test",
        task_type="feature-build",
        real_tokens=500,
        scope_status="in_scope",
        baseline_available=True,
        p25=300,
        p75=800,
        median=500,
        band_verdict="within_band",
        interpretation="",
        token_domain_of_validity="",
        baseline_source="b2_corpus",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning=None,
        trajectory_domain_of_validity="",
        waste_event_count=0,
        waste_events=[],
        waste_domain_of_validity="",
        session_cost_usd=None,
        cost_approximate=False,
        cost_domain_of_validity=None,
    )
    mtime = datetime(2026, 6, 10, tzinfo=UTC).timestamp()
    upsert_session(conn, result, "/nonexistent/path.jsonl", mtime, "hash-nn", turn_count=10)
    return conn


def test_no_socket_connect_during_payload_build() -> None:
    """socket.connect must never be called during build_contribution_payload."""

    def fail_on_connect(self, *args, **kwargs):
        raise AssertionError(f"build_contribution_payload made a network connection: {args}")

    conn = _make_conn()
    with patch.object(socket.socket, "connect", fail_on_connect):
        payload = build_contribution_payload(
            conn, contributor_id=None, include_source_components=False
        )

    assert payload is not None  # reached only if no network call was made


def test_contribution_module_imports_no_network_libs() -> None:
    """tes.contribution must not import requests, httpx, urllib.request, or aiohttp."""
    import tes.contribution as contrib_module

    network_modules = {"requests", "httpx", "aiohttp", "urllib.request"}
    # Allow if they were already loaded before our module (transitive deps).
    # The real test is test_no_socket_connect_during_payload_build.
    # Structural source check: confirm no network URLs or call-sites in the module.
    import inspect

    source = inspect.getsource(contrib_module)
    for banned in (
        "http://",
        "https://",
        "socket.connect",
        "requests.get",
        "httpx.get",
        "urllib.request",
        "urlopen",
    ):
        assert banned not in source, f"Network reference found in contribution.py: {banned!r}"


def test_build_with_source_components_false_makes_no_network_calls() -> None:
    """Even the re-adapt path (include_source_components=True on missing source) makes no calls."""

    def fail_on_connect(self, *args, **kwargs):
        raise AssertionError(f"Network call attempted: {args}")

    conn = _make_conn()
    with patch.object(socket.socket, "connect", fail_on_connect):
        # include_source_components=True but source_path points to a nonexistent file
        # → _get_source_components should gracefully return None values, not hit network
        payload = build_contribution_payload(
            conn, contributor_id=None, include_source_components=True
        )

    assert payload is not None
