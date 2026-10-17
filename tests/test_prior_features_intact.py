# Copyright 2026 Gaurav Gandhi
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

"""tests/test_prior_features_intact.py — 0.10.0 is additive: everything prior still works.

This phase adds live monitor + alarm + coach + budget on TOP of the diagnostic
engine. It must never touch attribution/cost/self-baseline math, the frozen
waste detectors, or any existing route. This test is the regression backstop
spec.md's Build Order step 6 calls for before publish.
"""

import hashlib
import subprocess
from pathlib import Path

from tracegauge.baselines import BUNDLED_BASELINES_PATH, load_baselines
from tracegauge.store import open_db
from tracegauge.web.server import ServerConfig, create_app

_REPO_ROOT = Path(__file__).resolve().parents[1]

# sha256 of tracegauge/_waste_detectors.py with LF line endings.
_WASTE_DETECTORS_SHA256 = "3c3a738eff2fde7ea1af7fd48a800d04b035d04f01c0bde28a9b06d9012bcd22"


# ---------------------------------------------------------------------------
# Frozen engine untouched
# ---------------------------------------------------------------------------


def test_waste_detectors_byte_frozen() -> None:
    """The waste detectors are byte-frozen: no uncommitted change AND no committed change.

    Two checks, because each alone has a hole. ``git diff`` is empty once a change is committed, so
    it only catches uncommitted edits; the pinned sha256 of the working-tree file catches a
    committed edit too. CRLF is normalised to LF before hashing so a Windows ``autocrlf`` checkout
    hashes the same as the LF checkout in CI.

    A legitimate future change to the detectors (a deliberate, reviewed decision, not a side
    effect of another change) updates the pin in the same commit: run
    ``python -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path(
    'tracegauge/_waste_detectors.py').read_bytes().replace(b'\\r\\n', b'\\n')).hexdigest())"``
    and paste the output into ``_WASTE_DETECTORS_SHA256``, and say why in the commit message.
    """
    path = _REPO_ROOT / "tracegauge" / "_waste_detectors.py"
    digest = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    assert digest == _WASTE_DETECTORS_SHA256, (
        f"tracegauge/_waste_detectors.py changed (sha256 {digest}); it is byte-frozen. If the "
        "change is deliberate, update _WASTE_DETECTORS_SHA256 as the docstring describes."
    )
    result = subprocess.run(
        ["git", "diff", "--exit-code", "tracegauge/_waste_detectors.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"tracegauge/_waste_detectors.py has uncommitted changes:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Prior modules still import and run
# ---------------------------------------------------------------------------


def test_diagnostic_engine_modules_still_import() -> None:
    import tracegauge.adapt  # noqa: F401
    import tracegauge.attribution  # noqa: F401
    import tracegauge.baselines  # noqa: F401
    import tracegauge.classify  # noqa: F401
    import tracegauge.cost  # noqa: F401
    import tracegauge.report  # noqa: F401
    import tracegauge.score  # noqa: F401
    import tracegauge.self_baseline  # noqa: F401
    import tracegauge.waste  # noqa: F401


def test_intelligence_modules_still_import() -> None:
    import tracegauge.intelligence.anomaly  # noqa: F401
    import tracegauge.intelligence.cache  # noqa: F401
    import tracegauge.intelligence.chat  # noqa: F401
    import tracegauge.intelligence.cluster  # noqa: F401
    import tracegauge.intelligence.features  # noqa: F401


def test_dormant_corpus_modules_still_import() -> None:
    """0.9.0 corpus stays dormant-by-choice but must still be importable (untouched)."""
    import tracegauge.community_baseline  # noqa: F401
    import tracegauge.contribution  # noqa: F401
    import tracegauge.corpus_client  # noqa: F401


def test_new_010_modules_import_alongside_everything_else() -> None:
    import tracegauge.alarm  # noqa: F401
    import tracegauge.budget  # noqa: F401
    import tracegauge.coach  # noqa: F401
    import tracegauge.live_monitor  # noqa: F401


# ---------------------------------------------------------------------------
# Dashboard: prior routes still respond
# ---------------------------------------------------------------------------


def test_prior_dashboard_routes_still_registered(tmp_path: Path) -> None:
    cfg = ServerConfig(db_path=tmp_path / "regress.db")
    open_db(cfg.db_path).close()
    app = create_app(cfg)

    rules = {r.rule for r in app.url_map.iter_rules()}
    for expected in (
        "/",
        "/session/<session_id>",
        "/trends",
        "/baseline-status",
        "/patterns",
        "/ask",
    ):
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
