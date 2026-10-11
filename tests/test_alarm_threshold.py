from __future__ import annotations

"""The alarm gate over the user's store: threshold from the pool, fire/no-fire, e2e.
"""

import dataclasses
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tes.adapt import ADAPTER_VERSION, COST_VERSION
from tes.alarm import AlarmConfig, check_alarm, threshold_for_live
from tes.alarm_baseline import (
    DEFAULT_PERCENTILE,
    TIER_RECENT_ERA_TYPE,
    PoolRow,
    compute_alarm_threshold,
    load_pool_rows,
    resolve_threshold,
)
from tes.live_monitor import LiveSessionState, score_live_session
from tes.self_baseline import SelfBaselineState
from tes.store import open_db

DAY = 86400.0
NOW = 1_800_000_000.0
S5, S55 = "claude-sonnet-5", "claude-sonnet-5-5"


def _rows(
    n: int,
    *,
    task_type: str = "infra-deploy",
    era: str = S55,
    base: int = 100_000,
    age_days: float = 1.0,
    prefix: str = "s",
) -> list[PoolRow]:
    """n sessions with tokens base, base+1000, ... all `age_days` old (so spread is explicit)."""
    return [
        PoolRow(
            f"{prefix}-{era}-{task_type}-{i}", task_type, era, base + 1000 * i, NOW - age_days * DAY
        )
        for i in range(n)
    ]


def _resolve(rows: list[PoolRow], **kw: Any) -> Any:
    args: dict[str, Any] = {"task_type": "infra-deploy", "era": S55, "now": NOW}
    args.update(kw)
    return resolve_threshold(rows, **args)


def _insert(
    conn: sqlite3.Connection, sid: str, tokens: int, version: int | None, **kw: Any
) -> None:
    conn.execute(
        "INSERT INTO sessions (session_id, task_type, source_path, source_mtime, source_hash, "
        "scored_at, axes_scored, real_tokens, scope_status, baseline_available, band_verdict, "
        "interpretation, token_domain_of_validity, trajectory_domain_of_validity, "
        "waste_event_count, waste_events, waste_domain_of_validity, adapter_version, "
        "cost_version, dominant_model) VALUES (?, ?, 'x', ?, 'h', 't', '[]', ?, "
        "'in_scope', 1, 'within_band', 'i', 'd', 'd', 0, '[]', 'd', ?, ?, ?)",
        (
            sid,
            kw.get("task_type", "infra-deploy"),
            kw.get("mtime", NOW - DAY),
            tokens,
            version,
            COST_VERSION if version is not None else None,
            kw.get("model"),
        ),
    )


def test_pool_ignores_stale_and_empty_rows_and_never_writes(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = open_db(db)
    for i in range(12):
        _insert(conn, f"cur{i}", 100_000 + i, ADAPTER_VERSION, model=f"{S55}-20260901")
        _insert(conn, f"old{i}", 9_000_000 + i, None, model=S55)  # pre-dedupe, inflated
    _insert(conn, "zero", 0, ADAPTER_VERSION, model=S55)
    conn.commit()
    conn.close()
    before = db.read_bytes()

    rows = load_pool_rows(db)
    assert len(rows) == 12 and {r.era for r in rows} == {S55}
    t = compute_alarm_threshold(
        db, task_type="infra-deploy", era=S55, session_id="live", baselines=None, now=NOW
    )
    assert t.status == "active" and t.threshold_tokens is not None and t.threshold_tokens < 200_000
    assert db.read_bytes() == before  # read-only


def test_pool_of_a_missing_or_old_store_is_empty_not_an_error(tmp_path: Path) -> None:
    assert load_pool_rows(tmp_path / "absent.db") == []
    assert not (tmp_path / "absent.db").exists()  # a lookup must not create a store
    db = tmp_path / "old.db"
    conn = open_db(db)
    _insert(conn, "a", 5, ADAPTER_VERSION)
    conn.commit()
    conn.execute("ALTER TABLE sessions DROP COLUMN dominant_model")
    conn.commit()
    assert [r.era for r in load_pool_rows(db)] == [""]  # pre-column rows: unknown era
    conn.execute("ALTER TABLE sessions DROP COLUMN adapter_version")
    conn.commit()
    conn.close()
    assert load_pool_rows(db) == []  # no adapter column: nothing is trusted


def _live(tokens: int, *, dominant: bool = True, era: str = S55) -> LiveSessionState:
    return LiveSessionState(
        session_id="live-1",
        task_type="infra-deploy",
        source_path="/x",
        live_cost_usd=1.0,
        live_context_tokens=tokens,
        live_resend_tokens=int(tokens * 0.9),
        live_resend_ratio=0.9,
        context_resend_dominant=dominant,
        ai_turn_count=10,
        domain_of_validity="d",
        dominant_model=era,
    )


def test_fires_strictly_above_the_threshold_only() -> None:
    thr = _resolve(_rows(12))
    assert thr.threshold_tokens is not None
    cfg = AlarmConfig(enabled=True)
    empty = SelfBaselineState()
    assert check_alarm(_live(thr.threshold_tokens), empty, cfg, thr) is None  # equal: silent
    hit = check_alarm(_live(thr.threshold_tokens + 1), empty, cfg, thr)
    assert hit is not None
    assert hit.baseline_p75_tokens == thr.threshold_tokens
    assert (hit.baseline_tier, hit.baseline_n, hit.baseline_percentile) == (
        TIER_RECENT_ERA_TYPE,
        12,
        DEFAULT_PERCENTILE,
    )
    assert "p85" in hit.message and "Consider `/compact`" in hit.message


def test_cause_gate_and_disabled_threshold_keep_it_silent() -> None:
    thr = _resolve(_rows(12))
    cfg = AlarmConfig(enabled=True)
    big = (thr.threshold_tokens or 0) * 10
    assert check_alarm(_live(big, dominant=False), SelfBaselineState(), cfg, thr) is None
    off = _resolve(_rows(2))
    assert off.status == "disabled"
    assert check_alarm(_live(big), SelfBaselineState(), cfg, off) is None
    assert check_alarm(_live(big), SelfBaselineState(), AlarmConfig(enabled=False), thr) is None


def test_alarm_config_stays_backward_compatible() -> None:
    cfg = AlarmConfig(True, "max")  # positional use from before the new knobs
    assert (cfg.enabled, cfg.plan_type) == (True, "max")
    assert (cfg.percentile, cfg.window_days, cfg.min_n) == (0.85, 30, 10)


def test_threshold_for_live_uses_the_config_knobs(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = open_db(db)
    import time

    for i in range(6):
        _insert(conn, f"s{i}", 100_000 + i, ADAPTER_VERSION, model=S55, mtime=time.time() - 60)
    conn.commit()
    conn.close()
    live = _live(1)
    assert threshold_for_live(live, db, None, AlarmConfig(enabled=True)).status == "disabled"
    assert threshold_for_live(live, db, None, AlarmConfig(enabled=True, min_n=5)).status == "active"


def _transcript(path: Path, model: str, turns: int, resend: int) -> None:
    """A synthetic main-chain session: `turns` responses, each re-sending `resend` cached tokens."""
    lines = [
        json.dumps({"type": "user", "uuid": "u0", "message": {"role": "user", "content": "go"}})
    ]
    for i in range(turns):
        usage = {
            "input_tokens": 10,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": resend,
            "output_tokens": 200,
        }
        lines.append(
            json.dumps(
                {
                    "isSidechain": False,
                    "requestId": f"r{i}",
                    "type": "assistant",
                    "uuid": f"a{i}",
                    "message": {
                        "model": model,
                        "id": f"m{i}",
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"step {i}"}],
                        "usage": usage,
                    },
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_runaway_resend_session_fires_and_a_normal_one_does_not(tmp_path: Path) -> None:
    normal = tmp_path / "normal.jsonl"
    runaway = tmp_path / "runaway.jsonl"
    _transcript(normal, S55, turns=10, resend=20_000)
    _transcript(runaway, S55, turns=80, resend=150_000)
    live_normal = score_live_session(normal)
    live_runaway = score_live_session(runaway)
    assert live_normal is not None and live_runaway is not None
    assert live_normal.dominant_model == S55 and live_runaway.context_resend_dominant

    typical = [
        PoolRow(
            f"p{i}",
            live_normal.task_type,
            S55,
            live_normal.live_context_tokens + 500 * i,
            NOW - DAY,
        )
        for i in range(12)
    ]
    thr = resolve_threshold(
        typical, task_type=live_normal.task_type, era=live_normal.dominant_model, now=NOW
    )
    cfg = AlarmConfig(enabled=True)
    assert check_alarm(live_normal, SelfBaselineState(), cfg, thr) is None
    fired = check_alarm(live_runaway, SelfBaselineState(), cfg, thr)
    assert fired is not None and fired.resend_pct >= 90


def test_monitor_cli_reads_the_store_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No seam on the threshold: the store's recent same-era rows decide exit 3 vs 0."""
    import time

    import tes.live_monitor as lm
    from tes import cli

    db = tmp_path / "tes.db"
    conn = open_db(db)
    for i in range(12):
        _insert(conn, f"s{i}", 100_000 + i, ADAPTER_VERSION, model=S55, mtime=time.time() - 3600)
    conn.commit()
    conn.close()
    monkeypatch.setenv("TES_DB_PATH", str(db))
    monkeypatch.setattr(lm, "find_active_session", lambda *a, **k: Path("/fake/active.jsonl"))

    def run(tokens: int) -> tuple[int, dict[str, Any]]:
        monkeypatch.setattr(lm, "score_live_session", lambda *a, **k: _live(tokens))
        monkeypatch.setattr("sys.argv", ["tes", "monitor", "--json"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        return int(exc.value.code or 0), json.loads(capsys.readouterr().out)

    code, doc = run(150_000)
    assert code == 3 and doc["alarm"] is not None
    assert (
        doc["alarm_baseline"]["tier"] == TIER_RECENT_ERA_TYPE and doc["alarm_baseline"]["n"] == 12
    )
    code, doc = run(100_000)
    assert code == 0 and doc["alarm"] is None and doc["alarm_baseline"]["status"] == "active"


def test_dashboard_monitor_page_shows_the_threshold_or_the_disabled_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    import tes.live_monitor as lm
    from tes.web.server import ServerConfig, create_app

    db = tmp_path / "tes.db"
    conn = open_db(db)
    for i in range(3):
        _insert(conn, f"s{i}", 100_000 + i, ADAPTER_VERSION, model=S55, mtime=time.time() - 3600)
    conn.commit()
    conn.close()
    live = dataclasses.replace(_live(1_000_000), task_type="feature-build")  # no shipped band
    monkeypatch.setattr(lm, "find_active_session", lambda *a, **k: Path("/fake/active.jsonl"))
    monkeypatch.setattr(lm, "score_live_session", lambda *a, **k: live)
    client = create_app(ServerConfig(db_path=db, cc_path=tmp_path)).test_client()

    html = client.get("/monitor").get_data(as_text=True)
    assert "alarm disabled: needs at least 10 of your sessions" in html  # 3 sessions: no tier
    assert "[ALARM]" not in html

    conn = open_db(db)
    for i in range(10):
        _insert(conn, f"t{i}", 100_000 + i, ADAPTER_VERSION, model=S55, mtime=time.time() - 3600)
    conn.commit()
    conn.close()
    html = client.get("/monitor").get_data(as_text=True)
    assert "[ALARM]" in html and "p85 of your last 30 days of claude-sonnet-5-5 sessions" in html


def test_shipped_tier_message_says_it_is_not_the_users_own_history() -> None:
    thr = resolve_threshold(
        [],
        task_type="infra-deploy",
        era=S55,
        now=NOW,
        shipped_types={"infra-deploy": {"available": True, "n": 15, "p75": 3_000_000}},
    )
    hit = check_alarm(_live(5_000_000), SelfBaselineState(), AlarmConfig(enabled=True), thr)
    assert hit is not None and hit.baseline_tier == "shipped"
    assert "bundled infra-deploy reference band (3,000,000 tokens" in hit.message
    assert "not your own history" in hit.message
