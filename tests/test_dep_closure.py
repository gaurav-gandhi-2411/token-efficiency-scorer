from __future__ import annotations

"""tests/test_dep_closure.py — Verify tes/intelligence/ imports only declared deps.

This test exists because 0.7.0 shipped with numpy and scikit-learn imported but
not declared in pyproject.toml. Clean installs got ModuleNotFoundError on
`tes patterns` and `tes ask` even though the gate passed (conda base had numpy).

The test walks tes/intelligence/*.py, extracts all absolute top-level imports via
ast, and asserts every external import is either stdlib or declared in DECLARED_PKGS.

Update DECLARED_PKGS whenever a new third-party package is added to
pyproject.toml [project.dependencies]. Package install name → import name mappings
that differ (e.g. scikit-learn → sklearn) are listed explicitly.
"""

import ast
import sys
from pathlib import Path

# Import names that correspond to declared pyproject.toml dependencies.
# Key: top-level importable name; update this when pyproject.toml deps change.
DECLARED_IMPORT_NAMES: frozenset[str] = frozenset({
    # flask and its transitive deps
    "flask", "werkzeug", "jinja2", "markupsafe", "click", "itsdangerous", "blinker",
    # httpx and its transitive deps
    "httpx", "httpcore", "h11", "anyio", "certifi", "idna", "sniffio",
    # numpy (declared 0.7.1+)
    "numpy",
    # scikit-learn (declared 0.7.1+) — import name is 'sklearn'
    "sklearn",
    # colorama is a transitive dep of click on Windows
    "colorama",
})

# Internal tes package — not a third-party import
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
            if node.level == 0 and node.module:   # absolute import only
                names.add(node.module.split(".")[0])
    return names


def test_intelligence_imports_are_declared() -> None:
    """Every external import in tes/intelligence/ must be in pyproject.toml deps.

    If this test fails:
      1. Identify the undeclared package.
      2. Add it to pyproject.toml [project.dependencies].
      3. Add its top-level import name to DECLARED_IMPORT_NAMES above.
    """
    intel_dir = Path(__file__).parent.parent / "tes" / "intelligence"
    stdlib = _stdlib_names()

    all_imports: set[str] = set()
    for py_file in intel_dir.glob("*.py"):
        all_imports |= _top_level_imports(py_file.read_text(encoding="utf-8"))

    external = all_imports - stdlib - _INTERNAL
    undeclared = external - DECLARED_IMPORT_NAMES

    assert not undeclared, (
        f"tes/intelligence/ imports packages not declared in pyproject.toml: {undeclared}.\n"
        "Add them to [project.dependencies] and to DECLARED_IMPORT_NAMES in this file.\n"
        "This guards against clean-install ModuleNotFoundError (the 0.7.0 regression)."
    )
