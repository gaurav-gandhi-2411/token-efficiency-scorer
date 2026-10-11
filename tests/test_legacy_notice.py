from __future__ import annotations

"""The one-time notice about legacy rows: once per store per version, again only when new legacy
rows appear, on stderr only, silenced by --quiet / TES_NO_NOTICE, and never fatal or slow."""

import io
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
import tes
from tes import cli, legacy
from tes.legacy import NO_NOTICE_ENV, maybe_notify
from tes.store import backfill_waste, open_db

from tests.legacy_store import MixedStore, build_mixed_store, insert_row


@pytest.fixture(autouse=True)
def _notice_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NO_NOTICE_ENV, raising=False)


@pytest.fixture
def mixed(tmp_path: Path) -> MixedStore:
    return build_mixed_store(tmp_path)


def _notify(s: MixedStore, **kw: object) -> tuple[bool, str]:
    buf = io.StringIO()
    shown = maybe_notify(s.db, stream=buf, **kw)  # type: ignore[arg-type]
    return shown, buf.getvalue()


def _marker(db: Path) -> dict[str, object]:
    conn = sqlite3.connect(db)
    value = conn.execute("SELECT value FROM meta WHERE key = 'legacy_notice'").fetchone()[0]
    conn.close()
    return json.loads(value)


def test_first_run_names_counts_and_the_fix_then_stays_quiet(mixed: MixedStore) -> None:
    shown, text = _notify(mixed)
    assert shown
    assert "4 of 6 stored session(s)" in text
    assert "rescorable (transcript still on disk): 3" in text
    assert "unrecoverable (transcript gone):       1" in text
    assert "tes rescore" in text
    assert _marker(mixed.db) == {"version": tes.__version__, "legacy": 4}
    assert _notify(mixed) == (False, "")  # once per store per version


def test_it_reappears_only_when_new_legacy_rows_appear(mixed: MixedStore) -> None:
    assert _notify(mixed)[0]
    conn = open_db(mixed.db)
    insert_row(conn, "late-legacy", adapter_version=None, cost_version=None)
    conn.close()
    shown, text = _notify(mixed)
    assert shown and "5 of 7" in text
    assert not _notify(mixed)[0]


def test_a_rescore_lowers_the_marker_so_later_legacy_rows_notify_again(mixed: MixedStore) -> None:
    assert _notify(mixed)[0]
    backfill_waste(mixed.db, only_legacy=True)  # 4 -> 2 legacy
    assert _notify(mixed) == (False, "")  # fewer legacy rows is not news
    assert _marker(mixed.db)["legacy"] == 2
    conn = open_db(mixed.db)
    insert_row(conn, "late-legacy", adapter_version=None, cost_version=None)
    conn.close()
    assert _notify(mixed)[0]  # 3 > 2


def test_a_new_tes_version_notifies_again(
    mixed: MixedStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _notify(mixed)[0]
    monkeypatch.setattr(tes, "__version__", "9.9.9")
    assert _notify(mixed)[0]
    assert _marker(mixed.db)["version"] == "9.9.9"


def test_nothing_to_rescore_says_so_instead_of_offering_the_command(tmp_path: Path) -> None:
    s = build_mixed_store(tmp_path)
    conn = open_db(s.db)
    conn.execute("UPDATE sessions SET source_path = 'gone.jsonl'")
    conn.commit()
    conn.close()
    _, text = _notify(s)
    assert "rescorable (transcript still on disk): 0" in text
    assert "has nothing to recover" in text and "Fix the rescorable ones" not in text


def test_quiet_and_env_var_suppress_without_writing_a_marker(
    mixed: MixedStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _notify(mixed, quiet=True) == (False, "")
    monkeypatch.setenv(NO_NOTICE_ENV, "1")
    assert _notify(mixed) == (False, "")
    conn = sqlite3.connect(mixed.db)
    assert conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0
    conn.close()
    monkeypatch.setenv(NO_NOTICE_ENV, "0")  # explicit zero means "do not suppress"
    assert _notify(mixed)[0]


def test_no_store_and_no_legacy_rows_print_nothing_and_create_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "none.db"
    assert not maybe_notify(missing, stream=io.StringIO())
    assert not missing.exists()
    clean = tmp_path / "clean.db"
    conn = open_db(clean)
    insert_row(conn, "ok")
    conn.close()
    assert not maybe_notify(clean, stream=io.StringIO())


def test_a_store_that_predates_the_columns_and_the_meta_table_is_handled(tmp_path: Path) -> None:
    db = tmp_path / "v014.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sessions (session_id TEXT, source_path TEXT, source_mtime REAL)")
    conn.execute("INSERT INTO sessions VALUES ('a', 'x', 1.0)")
    conn.commit()
    conn.close()
    buf = io.StringIO()
    assert maybe_notify(db, stream=buf)
    assert "1 of 1 stored session(s)" in buf.getvalue()
    assert not maybe_notify(db, stream=io.StringIO())


def test_a_marker_that_cannot_be_saved_still_shows_the_notice(
    mixed: MixedStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(legacy, "_write_marker", refuse)
    assert _notify(mixed)[0]
    assert _notify(mixed)[0]  # due again next time, but never a crash


def test_a_locked_store_neither_raises_nor_hangs(mixed: MixedStore) -> None:
    holder = sqlite3.connect(mixed.db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")  # another writer (e.g. the watcher) mid-transaction
    start = time.monotonic()
    shown, _ = _notify(mixed)
    elapsed = time.monotonic() - start
    holder.execute("ROLLBACK")
    holder.close()
    assert shown  # reading is not blocked; only the marker write was
    assert elapsed < 3.0


def test_concurrent_callers_do_not_crash(mixed: MixedStore) -> None:
    errors: list[BaseException] = []
    barrier = threading.Barrier(5)

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            maybe_notify(mixed.db, stream=io.StringIO())
        except BaseException as exc:  # noqa: BLE001 -- the test asserts none escaped
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    assert _marker(mixed.db)["legacy"] == 4


# ------------------------------------------------------------------------ through the real CLI


def _run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.argv", ["tes", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_notice_goes_to_stderr_once_and_keeps_json_stdout_clean(
    mixed: MixedStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TES_DB_PATH", str(mixed.db))
    code, out, err = _run(monkeypatch, capsys, "cost", "--week", "--json")
    assert code == 0
    json.loads(out)  # stdout is exactly one JSON document
    assert "tes rescore" in err and "4 of 6" in err
    assert "tracegauge:" not in out
    _, out, err = _run(monkeypatch, capsys, "budget", "--json")
    assert "tracegauge:" not in err  # second command: already told


def test_cli_quiet_flag_and_other_commands(
    mixed: MixedStore, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TES_DB_PATH", str(mixed.db))
    _, _, err = _run(monkeypatch, capsys, "--quiet", "budget", "--json")
    assert "tracegauge:" not in err
    _, _, err = _run(monkeypatch, capsys, "rescore", "--dry-run", "--json")
    assert "tracegauge:" not in err  # rescore is the fix, not a place to nag
    _, _, err = _run(monkeypatch, capsys, "impact", "--json")
    assert "tracegauge:" in err  # first store-opening command that is not silenced
