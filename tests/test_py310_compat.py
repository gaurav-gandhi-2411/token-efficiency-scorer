"""Static guard for Python 3.10 compatibility (the package claims 3.10-3.14).

`datetime.UTC` is 3.11+. The 3.10 CI leg found `scripts/rebuild_baselines.py` importing it, which
broke collection of two test files there; this keeps the whole class out of the shipped package and
out of the scripts the tests import, without needing a 3.10 interpreter to notice.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# tes/ is what ships; rebuild_baselines.py is the one script the test suite imports.
_GUARDED = [*sorted((_ROOT / "tes").rglob("*.py")), _ROOT / "scripts" / "rebuild_baselines.py"]


def _imports_datetime_utc(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "datetime"
        and any(alias.name == "UTC" for alias in node.names)
        for node in ast.walk(tree)
    )


def test_guarded_files_exist() -> None:
    assert len(_GUARDED) > 10
    assert all(p.exists() for p in _GUARDED)


def test_no_datetime_utc_import_in_shipped_or_test_imported_code() -> None:
    offenders = [str(p.relative_to(_ROOT)) for p in _GUARDED if _imports_datetime_utc(p)]
    assert offenders == [], (
        f"`from datetime import UTC` is Python 3.11+ but tracegauge supports 3.10; "
        f"use `UTC = timezone.utc` instead: {offenders}"
    )


def test_the_detector_still_detects(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("from datetime import UTC, datetime\n", encoding="utf-8")
    assert _imports_datetime_utc(bad)
