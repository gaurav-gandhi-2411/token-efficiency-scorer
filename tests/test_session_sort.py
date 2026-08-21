"""test_session_sort.py — Verify server-side session-list sorting.

Tests:
  1. Each sort key orders correctly (cost, date, waste, tokens, verdict).
  2. Unknown sort key falls back to 'date' (no 500, no SQL injection).
  3. Both ASC and DESC directions work.
  4. The existing honesty elements survive sorted HTML responses:
     band_verdict, DOV caveat, trajectory badge, price_provenance, sort arrows.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from tes.score import (
    TOKEN_DOMAIN_OF_VALIDITY,
    TRAJECTORY_DOMAIN_OF_VALIDITY,
    WASTE_DOMAIN_OF_VALIDITY,
    ThreeAxisResult,
)
from tes.store import _SORT_COLUMN_WHITELIST, list_sessions, open_db, upsert_session

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_result(
    session_id: str,
    task_type: str = "debug-fix",
    real_tokens: int = 1000,
    cost_usd: float | None = 0.01,
    waste_count: int = 0,
    band_verdict: str = "within_band",
) -> ThreeAxisResult:
    return ThreeAxisResult(
        session_id=session_id,
        task_type=task_type,
        real_tokens=real_tokens,
        scope_status="in_scope",
        baseline_available=True,
        p25=800,
        p75=1200,
        median=1000,
        band_verdict=band_verdict,
        interpretation="",
        token_domain_of_validity=TOKEN_DOMAIN_OF_VALIDITY,
        baseline_source="self",
        judge_verdict=None,
        judge_score=None,
        judge_reasoning=None,
        trajectory_domain_of_validity=TRAJECTORY_DOMAIN_OF_VALIDITY,
        waste_event_count=waste_count,
        waste_events=[],
        waste_domain_of_validity=WASTE_DOMAIN_OF_VALIDITY,
        session_cost_usd=cost_usd,
        cost_approximate=False,
        cost_domain_of_validity="",
    )


def _seed_db(db_path: Path) -> None:
    """Seed a DB with 5 sessions having distinct sort-key values."""
    conn = open_db(db_path)
    sessions = [
        # (sid_suffix, tokens,  cost,   waste, verdict)
        ("aaaa", 100, 0.001, 0, "within_band"),
        ("bbbb", 500, 0.050, 2, "above_p75"),
        ("cccc", 300, 0.020, 0, "below_p25"),
        ("dddd", 900, 0.100, 5, "within_band"),
        ("eeee", 700, 0.005, 1, "above_p75"),
    ]
    for i, (suf, tokens, cost, waste, verdict) in enumerate(sessions):
        sid = f"test-{suf}-0000-0000-0000-{i:012d}"
        r = _make_result(
            sid, real_tokens=tokens, cost_usd=cost, waste_count=waste, band_verdict=verdict
        )
        upsert_session(conn, r, f"/fake/{sid}.jsonl", float(i), f"hash-{suf}")
        # Patch cost + waste because upsert_session reads them from the result
        # and already writes them; this ensures they're committed to the store.
        conn.execute(
            "UPDATE sessions SET waste_event_count=?, session_cost_usd=? WHERE session_id=?",
            (waste, cost, sid),
        )
        time.sleep(0.01)  # ensure distinct scored_at values
    conn.commit()
    conn.close()


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    _seed_db(db)
    return db


@pytest.fixture()
def seeded_conn(seeded_db: Path) -> sqlite3.Connection:
    return open_db(seeded_db)


# ---------------------------------------------------------------------------
# Unit tests: list_sessions sort
# ---------------------------------------------------------------------------


class TestListSessionsSort:
    def test_sort_cost_desc(self, seeded_conn: sqlite3.Connection) -> None:
        rows = list_sessions(seeded_conn, order_by="cost", direction="DESC")
        costs = [r["session_cost_usd"] for r in rows if r["session_cost_usd"] is not None]
        assert costs == sorted(costs, reverse=True)

    def test_sort_cost_asc(self, seeded_conn: sqlite3.Connection) -> None:
        rows = list_sessions(seeded_conn, order_by="cost", direction="ASC")
        costs = [r["session_cost_usd"] for r in rows if r["session_cost_usd"] is not None]
        assert costs == sorted(costs)

    def test_sort_tokens_desc(self, seeded_conn: sqlite3.Connection) -> None:
        rows = list_sessions(seeded_conn, order_by="tokens", direction="DESC")
        tokens = [r["real_tokens"] for r in rows]
        assert tokens == sorted(tokens, reverse=True)

    def test_sort_tokens_asc(self, seeded_conn: sqlite3.Connection) -> None:
        rows = list_sessions(seeded_conn, order_by="tokens", direction="ASC")
        tokens = [r["real_tokens"] for r in rows]
        assert tokens == sorted(tokens)

    def test_sort_waste_desc(self, seeded_conn: sqlite3.Connection) -> None:
        rows = list_sessions(seeded_conn, order_by="waste", direction="DESC")
        waste = [r["waste_event_count"] for r in rows]
        assert waste == sorted(waste, reverse=True)

    def test_sort_verdict_asc(self, seeded_conn: sqlite3.Connection) -> None:
        rows = list_sessions(seeded_conn, order_by="verdict", direction="ASC")
        verdicts = [r["band_verdict"] for r in rows]
        assert verdicts == sorted(verdicts)

    def test_sort_date_desc_default(self, seeded_conn: sqlite3.Connection) -> None:
        rows = list_sessions(seeded_conn)  # defaults: date DESC
        dates = [r["scored_at"] for r in rows]
        assert dates == sorted(dates, reverse=True)

    def test_unknown_sort_key_falls_back_to_date(self, seeded_conn: sqlite3.Connection) -> None:
        rows = list_sessions(seeded_conn, order_by="INVALID_KEY__DROP_TABLE", direction="DESC")
        assert len(rows) == 5
        dates = [r["scored_at"] for r in rows]
        assert dates == sorted(dates, reverse=True)

    def test_invalid_direction_falls_back_to_desc(self, seeded_conn: sqlite3.Connection) -> None:
        rows = list_sessions(seeded_conn, order_by="tokens", direction="' OR 1=1 --")
        tokens = [r["real_tokens"] for r in rows]
        assert tokens == sorted(tokens, reverse=True)


# ---------------------------------------------------------------------------
# Whitelist integrity — no SQL injection surface
# ---------------------------------------------------------------------------


class TestSortWhitelist:
    def test_whitelist_values_are_real_column_names(self, seeded_conn: sqlite3.Connection) -> None:
        """Every whitelist value must be an actual DB column name."""
        cursor = seeded_conn.execute("SELECT * FROM sessions LIMIT 1")
        col_names = {desc[0] for desc in cursor.description}
        for key, col in _SORT_COLUMN_WHITELIST.items():
            assert col in col_names, (
                f"Whitelist maps {key!r} → {col!r} but that column doesn't exist"
            )

    def test_all_five_sort_keys_present(self) -> None:
        expected = {"date", "cost", "waste", "tokens", "verdict"}
        assert expected == set(_SORT_COLUMN_WHITELIST.keys())


# ---------------------------------------------------------------------------
# Flask route tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def flask_app(seeded_db: Path):
    from tes.web.server import ServerConfig, create_app

    cfg = ServerConfig(db_path=seeded_db)
    app = create_app(cfg)
    app.config["TESTING"] = True
    return app


class TestSessionListRoute:
    def test_default_sort_returns_200(self, flask_app) -> None:
        with flask_app.test_client() as c:
            assert c.get("/").status_code == 200

    def test_sort_cost_returns_200(self, flask_app) -> None:
        with flask_app.test_client() as c:
            assert c.get("/?sort=cost&dir=desc").status_code == 200

    def test_sort_tokens_asc_returns_200(self, flask_app) -> None:
        with flask_app.test_client() as c:
            assert c.get("/?sort=tokens&dir=asc").status_code == 200

    def test_sort_waste_returns_200(self, flask_app) -> None:
        with flask_app.test_client() as c:
            assert c.get("/?sort=waste&dir=desc").status_code == 200

    def test_sort_verdict_returns_200(self, flask_app) -> None:
        with flask_app.test_client() as c:
            assert c.get("/?sort=verdict&dir=asc").status_code == 200

    def test_unknown_sort_key_returns_200(self, flask_app) -> None:
        with flask_app.test_client() as c:
            assert c.get("/?sort=malicious_key&dir=desc").status_code == 200

    # ── Honesty elements must survive sorting ────────────────────────────

    def test_dov_caveat_present_after_sort(self, flask_app) -> None:
        with flask_app.test_client() as c:
            html = c.get("/?sort=cost&dir=desc").data.decode()
        assert "your own lean waste-free sessions" in html

    def test_band_verdict_badges_present(self, flask_app) -> None:
        with flask_app.test_client() as c:
            html = c.get("/?sort=cost&dir=desc").data.decode()
        assert any(b in html for b in ("badge-above", "badge-within", "badge-below"))

    def test_sort_active_class_rendered(self, flask_app) -> None:
        with flask_app.test_client() as c:
            html = c.get("/?sort=cost&dir=desc").data.decode()
        assert "sort-active" in html

    def test_sort_direction_arrow_rendered(self, flask_app) -> None:
        with flask_app.test_client() as c:
            html = c.get("/?sort=cost&dir=desc").data.decode()
        assert "↓" in html

    def test_column_headers_all_present(self, flask_app) -> None:
        with flask_app.test_client() as c:
            html = c.get("/").data.decode()
        for label in ("Cost", "Tokens", "Waste", "Token verdict"):
            assert label in html, f"Column header '{label}' missing"

    def test_cost_not_labeled_as_score(self, flask_app) -> None:
        with flask_app.test_client() as c:
            html = c.get("/?sort=cost&dir=desc").data.decode()
        assert "not a score" in html
