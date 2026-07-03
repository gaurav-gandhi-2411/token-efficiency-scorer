from __future__ import annotations

"""tests/test_prior_features_intact.py — 0.10.0 is additive: everything prior still works.

This phase adds live monitor + alarm + coach + budget on TOP of the diagnostic
engine. It must never touch attribution/cost/self-baseline math, the frozen
waste detectors, or any existing route. This test is the regression backstop
spec.md's Build Order step 6 calls for before publish.
"""

import subprocess
from pathlib import Path

from tes.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tes.store import open_db
from tes.web.server import ServerConfig, create_app

_REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Frozen engine untouched
# ---------------------------------------------------------------------------


def test_waste_detectors_byte_frozen() -> None:
    """git diff on tes/_waste_detectors.py must be empty — the detectors are byte-frozen."""
    result = subprocess.run(
        ["git", "diff", "--exit-code", "tes/_waste_detectors.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"tes/_waste_detectors.py has uncommitted changes:\n{result.stdout}"


# ---------------------------------------------------------------------------
# Prior modules still import and run
# ---------------------------------------------------------------------------


def test_diagnostic_engine_modules_still_import() -> None:
    import tes.adapt  # noqa: F401
    import tes.attribution  # noqa: F401
    import tes.baselines  # noqa: F401
    import tes.classify  # noqa: F401
    import tes.cost  # noqa: F401
    import tes.report  # noqa: F401
    import tes.score  # noqa: F401
    import tes.self_baseline  # noqa: F401
    import tes.waste  # noqa: F401


def test_intelligence_modules_still_import() -> None:
    import tes.intelligence.anomaly  # noqa: F401
    import tes.intelligence.cache  # noqa: F401
    import tes.intelligence.chat  # noqa: F401
    import tes.intelligence.cluster  # noqa: F401
    import tes.intelligence.features  # noqa: F401


def test_dormant_corpus_modules_still_import() -> None:
    """0.9.0 corpus stays dormant-by-choice but must still be importable (untouched)."""
    import tes.community_baseline  # noqa: F401
    import tes.contribution  # noqa: F401
    import tes.corpus_client  # noqa: F401


def test_new_010_modules_import_alongside_everything_else() -> None:
    import tes.alarm  # noqa: F401
    import tes.budget  # noqa: F401
    import tes.coach  # noqa: F401
    import tes.live_monitor  # noqa: F401


# ---------------------------------------------------------------------------
# Dashboard: prior routes still respond
# ---------------------------------------------------------------------------


def test_prior_dashboard_routes_still_registered(tmp_path: Path) -> None:
    cfg = ServerConfig(db_path=tmp_path / "regress.db")
    open_db(cfg.db_path).close()
    app = create_app(cfg)

    rules = {r.rule for r in app.url_map.iter_rules()}
    for expected in ("/", "/session/<session_id>", "/trends", "/baseline-status", "/patterns", "/ask"):
        assert expected in rules, f"Route {expected} missing from url_map — regression."


def test_prior_dashboard_session_list_responds(tmp_path: Path) -> None:
    cfg = ServerConfig(db_path=tmp_path / "regress.db")
    open_db(cfg.db_path).close()
    app = create_app(cfg)
    client = app.test_client()

    resp = client.get("/")
    assert resp.status_code == 200


def test_prior_dashboard_trends_and_baseline_status_respond(tmp_path: Path) -> None:
    cfg = ServerConfig(db_path=tmp_path / "regress.db")
    open_db(cfg.db_path).close()
    app = create_app(cfg)
    client = app.test_client()

    assert client.get("/trends").status_code == 200
    assert client.get("/baseline-status").status_code == 200


# ---------------------------------------------------------------------------
# Bundled baselines still load (unchanged data file)
# ---------------------------------------------------------------------------


def test_bundled_baselines_still_load() -> None:
    baselines = load_baselines(BUNDLED_BASELINES_PATH)
    assert "scope_gates" in baselines
    assert "types" in baselines
