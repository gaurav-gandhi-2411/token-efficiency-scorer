from __future__ import annotations

"""tests/test_intelligence_cache_scoping.py — RR2: intelligence_cache.json
must be scoped to the DB it was computed from, never a single fixed global
path regardless of TES_DB_PATH.

Real incident, not hypothetical: before this fix, tes.intelligence.cache's
_cache_path() was hardcoded to ~/.tes/intelligence_cache.json unconditionally
-- computing patterns against an isolated test/scratch database (via
TES_DB_PATH, exactly the pattern this project's own test suite and manual
verification use) silently overwrote the REAL cache file, which a real
`tes ask`/`tes patterns` call on the actual corpus would then read back as
if it were genuine. Found live during RR1 verification when a scratch-DB
`tes patterns --recompute` run wrote 38-session clustering results from an
unrelated test corpus into the real ~/.tes/intelligence_cache.json.
"""

from pathlib import Path

import pytest
from tes.intelligence.cache import (
    _cache_path,
    get_or_compute_intelligence,
    load_cache,
    save_cache,
)

# ---------------------------------------------------------------------------
# UU2: db_path is a required parameter, not a defaultable one, on every
# function in this module that can write. This is what actually closes the
# hole RR2 found and UU2 found again: a caller that omits db_path entirely
# now gets a loud TypeError at the call site, not a silent write to
# ~/.tes/intelligence_cache.json. Verified structurally (the call itself
# fails) rather than by convention (a rule the caller has to remember).
# ---------------------------------------------------------------------------


class TestDbPathIsRequiredNotDefaulted:
    def test_cache_path_requires_db_path(self):
        with pytest.raises(TypeError):
            _cache_path()  # type: ignore[call-arg]

    def test_load_cache_requires_db_path(self):
        with pytest.raises(TypeError):
            load_cache()  # type: ignore[call-arg]

    def test_save_cache_requires_db_path(self):
        with pytest.raises(TypeError):
            save_cache({"valid": False}, session_count=0)  # type: ignore[call-arg]

    def test_get_or_compute_intelligence_requires_db_path(self):
        with pytest.raises(TypeError):
            get_or_compute_intelligence()  # type: ignore[call-arg]


def test_different_db_paths_resolve_to_different_cache_files(tmp_path: Path):
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "sub" / "b.db"

    assert _cache_path(db_a) != _cache_path(db_b)


def test_cache_path_is_named_after_and_colocated_with_its_db(tmp_path: Path):
    db = tmp_path / "some_scratch.db"
    cache_path = _cache_path(db)

    assert cache_path.parent == db.parent
    assert cache_path.name == "some_scratch.intelligence_cache.json"


def test_default_cache_path_mirrors_resolve_db_path(monkeypatch):
    """No explicit db_path and no TES_DB_PATH -- _cache_path's default must
    track tes.store.resolve_db_path's own default exactly (RR2's fix
    mirrors it, does not replace it). Compared against a live call rather
    than a reconstructed ~/.tes path: tes.store._DEFAULT_DIR is a
    module-level constant baked in at import time from Path.home(), so it
    cannot be monkeypatched after import -- this comparison is robust to
    that regardless of which real home directory the test process has.
    """
    from tes.store import resolve_db_path

    monkeypatch.delenv("TES_DB_PATH", raising=False)

    expected_db = resolve_db_path(None)
    cache_path = _cache_path(None)

    assert cache_path == expected_db.parent / f"{expected_db.stem}.intelligence_cache.json"


def test_save_and_load_for_one_db_never_touches_a_different_dbs_cache(tmp_path: Path):
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"

    save_cache({"valid": True, "n_sessions": 1}, session_count=1, db_path=db_a)

    assert load_cache(db_a) is not None
    assert load_cache(db_a)["n_sessions"] == 1
    # A completely different DB path must see no cache at all -- not the
    # other DB's data, not a merged/ambiguous result.
    assert load_cache(db_b) is None


def test_env_var_and_explicit_arg_agree_on_the_same_cache_file(monkeypatch, tmp_path: Path):
    """TES_DB_PATH and an explicit db_path pointing at the identical file
    must resolve to the identical cache path -- otherwise a caller reading
    via one route and writing via the other would silently diverge."""
    db = tmp_path / "shared.db"

    monkeypatch.setenv("TES_DB_PATH", str(db))
    via_env = _cache_path(None)

    monkeypatch.delenv("TES_DB_PATH", raising=False)
    via_explicit = _cache_path(db)

    assert via_env == via_explicit


def test_get_or_compute_intelligence_isolates_scratch_db_from_real_cache(
    monkeypatch, tmp_path: Path
):
    """End-to-end (mocked store layer, real cache-path resolution): running
    get_or_compute_intelligence with an EXPLICIT scratch db_path must write
    ONLY to that scratch DB's own cache file. An explicit (non-None)
    db_path short-circuits tes.store.resolve_db_path before it ever
    consults Path.home()/TES_DB_PATH at all (confirmed by reading
    resolve_db_path's own resolution order) -- this is what actually keeps
    this test safe regardless of the real machine's home directory, not a
    Path.home() patch (tes.store._DEFAULT_DIR is a module-level constant
    baked in from Path.home() at import time, so patching it post-import
    has no effect -- see test_default_cache_path_mirrors_resolve_db_path's
    docstring for the same finding).
    """
    from unittest.mock import patch

    monkeypatch.delenv("TES_DB_PATH", raising=False)

    # Capture the REAL default cache file's state before this test runs --
    # never assert it "must not exist" (it may genuinely exist from real
    # prior use on this machine, and asserting non-existence of a real file
    # would itself violate this project's own "never touch real ~/.tes"
    # rule); instead prove this test's scratch-db run left it byte-for-byte
    # unchanged, which is the actually correct claim either way.
    from tes.store import resolve_db_path

    real_default_db = resolve_db_path(None)
    real_default_cache = real_default_db.parent / f"{real_default_db.stem}.intelligence_cache.json"
    before = real_default_cache.read_bytes() if real_default_cache.exists() else None

    scratch_db = tmp_path / "scratch.db"
    n = 5  # below MIN_CONTENT_FOR_CACHE -- exercises the not-enough-sessions path

    fake_rows = [
        {
            "session_id": f"s{i}",
            "task_type": "feature-build",
            "real_tokens": 1000,
            "turn_count": 5,
            "session_cost_usd": 1.0,
            "waste_event_count": 0,
            "waste_events": [],
            "context_resend_pct": 0.9,
            "context_growth_pct": 0.05,
            "output_pct": 0.05,
            "waste_pct": 0.0,
        }
        for i in range(n)
    ]

    from tes.intelligence.cache import get_or_compute_intelligence

    with (
        patch("tes.store.open_db"),
        patch("tes.store.list_sessions", return_value=fake_rows),
    ):
        result = get_or_compute_intelligence(db_path=scratch_db, force_recompute=True)

    assert result["valid"] is False

    expected_cache = scratch_db.parent / "scratch.intelligence_cache.json"
    assert expected_cache.exists()

    # The REAL default cache file must be exactly as it was before this
    # scratch-db run -- this is the actual RR2 regression this test exists
    # to catch: before the fix, this exact call sequence overwrote it.
    after = real_default_cache.read_bytes() if real_default_cache.exists() else None
    assert after == before, (
        "get_or_compute_intelligence against a scratch db_path modified the "
        "REAL default intelligence cache -- this is the exact RR2 regression."
    )
