# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""Legacy rows (scored before 0.15) on the dashboard: the session list and session page list
them marked (never compared with a baseline) and say how many there are; the patterns page says
how many the analysis left out. Flask test client on the synthetic mixed store."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask.testing import FlaskClient
from tracegauge.legacy import LEGACY_ROW_LABEL
from tracegauge.store import open_db
from tracegauge.web.server import ServerConfig, create_app

from tests.legacy_store import MixedStore, build_mixed_store, insert_row


@pytest.fixture
def mixed(tmp_path: Path) -> MixedStore:
    return build_mixed_store(tmp_path)


@pytest.fixture
def client(mixed: MixedStore) -> Iterator[FlaskClient]:
    app = create_app(ServerConfig(db_path=mixed.db))
    with app.test_client() as c:
        yield c


def test_dashboard_list_marks_every_legacy_row_and_only_those(
    client: FlaskClient, mixed: MixedStore
) -> None:
    html = client.get("/").get_data(as_text=True)
    assert html.count("legacy-badge") == 4
    assert html.count(LEGACY_ROW_LABEL) >= 4
    assert "4 legacy sessions" in html and 'id="legacy-banner"' in html
    # the badge sits on the legacy rows: a current row's block has none
    cur = html.split(f"/session/{mixed.current[0]}")[1].split("</tr>")[0]
    assert "legacy-badge" not in cur
    old = html.split(f"/session/{mixed.unrecoverable}")[1].split("</tr>")[0]
    assert "legacy-badge" in old


def test_dashboard_list_has_no_banner_without_legacy_rows(tmp_path: Path) -> None:
    db = tmp_path / "clean.db"
    conn = open_db(db)
    insert_row(conn, "cur-only")
    conn.close()
    app = create_app(ServerConfig(db_path=db))
    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
    assert "legacy-banner" not in html and "legacy-badge" not in html


def test_dashboard_detail_banner_on_legacy_only(client: FlaskClient, mixed: MixedStore) -> None:
    legacy = client.get(f"/session/{mixed.rescorable_pre_dedupe}")
    assert legacy.status_code == 200
    html = legacy.get_data(as_text=True)
    assert 'id="legacy-banner"' in html and LEGACY_ROW_LABEL in html
    assert "not compared" in html  # no cost-vs-baseline comparison for a legacy cost
    current = client.get(f"/session/{mixed.current[0]}").get_data(as_text=True)
    assert "legacy-banner" not in current


def test_dashboard_patterns_page_reports_the_count(client: FlaskClient) -> None:
    html = client.get("/patterns").get_data(as_text=True)
    assert "4 legacy sessions left out of this" in html


def test_dashboard_budget_page_says_how_many_legacy_sessions_it_left_out(tmp_path: Path) -> None:
    """/budget excludes legacy rows from the pace (as `tracegauge budget` does) and now says so."""
    import time

    db = tmp_path / "budget.db"
    conn = open_db(db)
    now = time.time()
    for i in range(3):
        insert_row(conn, f"cur-{i}", cost_usd=2.0, mtime=now - 3600 * (i + 1))
    for i in range(2):
        insert_row(
            conn, f"old-{i}", cost_usd=50.0, adapter_version=None, mtime=now - 3600 * (i + 1)
        )
    conn.close()
    app = create_app(ServerConfig(db_path=db))
    with app.test_client() as c:
        html = c.get("/budget").get_data(as_text=True)
        assert 'id="legacy-banner"' in html and "2 legacy sessions in this window" in html
        assert "3 session" in html  # the pace covers the current rows only

    clean = tmp_path / "clean.db"
    conn = open_db(clean)
    insert_row(conn, "cur", cost_usd=2.0, mtime=now - 3600)
    conn.close()
    with create_app(ServerConfig(db_path=clean)).test_client() as c:
        assert "legacy-banner" not in c.get("/budget").get_data(as_text=True)
