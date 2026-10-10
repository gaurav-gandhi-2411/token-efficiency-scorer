from __future__ import annotations

"""tests/test_dep_closure.py — Verify ALL of tes/ imports only declared deps.

This test exists because 0.7.0 shipped with numpy and scikit-learn imported but
not declared in pyproject.toml. Clean installs got ModuleNotFoundError on
`tes patterns` and `tes ask` even though the gate passed (conda base had numpy).

The test walks ALL of tes/**/*.py recursively, extracts every absolute top-level
import via AST, and asserts every external import is either stdlib or declared in
DECLARED_IMPORT_NAMES.

Scope: the FULL tes/ package — cli.py, judge.py, web/, intelligence/, and every
future module. Not just intelligence/ — the next undeclared import could land anywhere.

Update DECLARED_IMPORT_NAMES whenever a new third-party package is added to
pyproject.toml [project.dependencies]. Package install name → import name mappings
that differ (e.g. scikit-learn → sklearn) are listed explicitly.
"""

import ast
import sys
from pathlib import Path

# Import names that correspond to declared pyproject.toml dependencies.
# Key: top-level importable name; update this when pyproject.toml deps change.
# Audit (2026-06-15): every third-party import in tes/ mapped here:
#   flask     → web/server.py
#   httpx     → judge.py, intelligence/chat.py
#   numpy     → intelligence/features.py, intelligence/anomaly.py, intelligence/chat.py
#   sklearn   → intelligence/cluster.py  (install name: scikit-learn)
DECLARED_IMPORT_NAMES: frozenset[str] = frozenset(
    {
        # flask>=3.0,<4 and its transitive deps
        "flask",
        "werkzeug",
        "jinja2",
        "markupsafe",
        "click",
        "itsdangerous",
        "blinker",
        # httpx>=0.27,<1 and its transitive deps
        "httpx",
        "httpcore",
        "h11",
        "anyio",
        "certifi",
        "idna",
        "sniffio",
        # colorama: transitive dep of click on Windows
        "colorama",
    }
)

# Optional `patterns` extra (numpy, scikit-learn; scipy arrives via scikit-learn). Only
# tes/intelligence may import these; everything else must run on the core dependencies.
PATTERNS_EXTRA_IMPORT_NAMES: frozenset[str] = frozenset({"numpy", "sklearn", "scipy"})

# Internal tes package and future-annotations guard — not third-party
_INTERNAL = frozenset({"tes", "__future__"})


def _stdlib_names() -> frozenset[str]:
    """Return frozenset of stdlib top-level module names (Python 3.10+)."""
    return frozenset(sys.stdlib_module_names)  # type: ignore[attr-defined]


def _top_level_imports(source: str) -> set[str]:
    """Extract the top-level package name for every absolute import in source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:  # absolute import only
                names.add(node.module.split(".")[0])
    return names


def test_all_tes_imports_are_declared() -> None:
    """Every external import in ALL of tes/ must be in pyproject.toml deps.

    Covers: cli.py, judge.py, web/, intelligence/, and every future module.
    The 0.7.0 regression was caught only in intelligence/ — the invariant must
    hold package-wide so a new import in any module triggers immediate failure.

    If this test fails:
      1. Identify the undeclared package (reported in the assertion message).
      2. Add it to pyproject.toml [project.dependencies] with version bounds.
      3. Add its top-level import name to DECLARED_IMPORT_NAMES above.
      4. Run the clean-gate (conda create --no-default-packages) to confirm
         the declared dep installs cleanly before publishing.
    """
    tes_dir = Path(__file__).parent.parent / "tes"
    stdlib = _stdlib_names()

    all_imports: set[str] = set()
    scanned: list[str] = []
    for py_file in sorted(tes_dir.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        found = _top_level_imports(source)
        if "intelligence" in py_file.relative_to(tes_dir).parts:
            found -= PATTERNS_EXTRA_IMPORT_NAMES  # checked separately below
        all_imports |= found
        scanned.append(str(py_file.relative_to(tes_dir.parent)))

    external = all_imports - stdlib - _INTERNAL
    undeclared = external - DECLARED_IMPORT_NAMES

    assert not undeclared, (
        f"tes/ imports packages not declared in pyproject.toml: {undeclared}.\n"
        f"Scanned {len(scanned)} files across tes/.\n"
        "Add undeclared packages to [project.dependencies] and to DECLARED_IMPORT_NAMES.\n"
        "Then run the clean-gate: conda create --no-default-packages + pip install wheel.\n"
        "This guards against clean-install ModuleNotFoundError (the 0.7.0 regression)."
    )


def test_extra_only_packages_are_imported_only_by_tes_intelligence() -> None:
    """numpy/scikit-learn/scipy are the optional `patterns` extra; core must not import them."""
    tes_dir = Path(__file__).parent.parent / "tes"
    offenders: dict[str, set[str]] = {}
    for py_file in sorted(tes_dir.rglob("*.py")):
        if "intelligence" in py_file.relative_to(tes_dir).parts:
            continue
        hit = _top_level_imports(py_file.read_text(encoding="utf-8")) & PATTERNS_EXTRA_IMPORT_NAMES
        if hit:
            offenders[str(py_file.relative_to(tes_dir.parent))] = hit
    assert not offenders, f"core modules import the optional patterns extra: {offenders}"
