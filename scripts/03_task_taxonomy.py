"""
03_task_taxonomy.py

Assigns each SWE-bench instance a task-type class using two signals:
  1. Repository domain (extracted from instance_id), mapped to one of 8 coarse categories
  2. Patch structure: single-file vs multi-file, test-only vs code change

Produces:
  data/validation-corpus/taxonomy/task_taxonomy.json
  data/validation-corpus/taxonomy/taxonomy_summary.json

Usage:
    python scripts/03_task_taxonomy.py
"""
from __future__ import annotations

import json
import pathlib
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TRACES_DIR = REPO_ROOT / "data" / "validation-corpus" / "traces_normalized"
OUT_DIR = REPO_ROOT / "data" / "validation-corpus" / "taxonomy"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Domain mapping ────────────────────────────────────────────────────────────
# Maps substrings of the repo-name portion of instance_id (case-insensitive)
# to one of 8 coarse task-type categories.
# Ordering matters: first match wins.
DOMAIN_MAP: list[tuple[str, str]] = [
    # Static analysis / type checking
    ("mypy",        "type_checker"),
    ("pyflakes",    "type_checker"),
    ("pyupgrade",   "type_checker"),
    ("autopep8",    "type_checker"),
    ("flake8",      "type_checker"),
    ("pylint",      "type_checker"),
    ("ruff",        "type_checker"),
    # Data / ML / scientific
    ("numpy",       "data_ml"),
    ("pandas",      "data_ml"),
    ("scipy",       "data_ml"),
    ("sklearn",     "data_ml"),
    ("scikit",      "data_ml"),
    ("monai",       "data_ml"),
    ("pytorch",     "data_ml"),
    ("transformers","data_ml"),
    ("matplotlib",  "data_ml"),
    ("seaborn",     "data_ml"),
    ("xarray",      "data_ml"),
    ("statsmodels", "data_ml"),
    ("sympy",       "data_ml"),
    # Web / API
    ("django",      "web_api"),
    ("flask",       "web_api"),
    ("fastapi",     "web_api"),
    ("aiohttp",     "web_api"),
    ("requests",    "web_api"),
    ("httpx",       "web_api"),
    ("starlette",   "web_api"),
    ("tornado",     "web_api"),
    # Cloud / DevOps / infra
    ("moto",        "cloud_devops"),
    ("boto",        "cloud_devops"),
    ("docker",      "cloud_devops"),
    ("kubernetes",  "cloud_devops"),
    ("airflow",     "cloud_devops"),
    ("celery",      "cloud_devops"),
    ("prefect",     "cloud_devops"),
    ("hydra",       "cloud_devops"),
    # DB / ORM / data-layer
    ("sqlalchemy",  "db_orm"),
    ("sqlglot",     "db_orm"),
    ("pymongo",     "db_orm"),
    ("redis",       "db_orm"),
    ("peewee",      "db_orm"),
    ("tortoise",    "db_orm"),
    ("intervals",   "db_orm"),
    # Graph / geospatial / visualization
    ("networkx",    "graph_geo"),
    ("geopandas",   "graph_geo"),
    ("folium",      "graph_geo"),
    ("shapely",     "graph_geo"),
    ("pygments",    "graph_geo"),
    # Testing / CI tools
    ("pytest",      "testing_ci"),
    ("tox",         "testing_ci"),
    ("coverage",    "testing_ci"),
    # Data / ML / scientific (extended)
    ("dask",        "data_ml"),
    ("modin",       "data_ml"),
    ("pennylane",   "data_ml"),
    ("starfish",    "data_ml"),
    ("parlai",      "data_ml"),
    ("igseq",       "data_ml"),
    # Static analysis / linters (extended)
    ("cognitive_complexity", "type_checker"),
    ("wemake",      "type_checker"),
    ("black",       "type_checker"),
    # Cloud / DevOps (extended)
    ("dvc",         "cloud_devops"),
    ("xonsh",       "cloud_devops"),
    ("synthtool",   "cloud_devops"),
    ("python-api-core", "cloud_devops"),
    # Testing (extended)
    ("faker",       "testing_ci"),
    ("responses",   "testing_ci"),
    # General libraries
    ("pydantic",    "lib_general"),
    ("attrs",       "lib_general"),
    ("click",       "lib_general"),
    ("rich",        "lib_general"),
    ("humanize",    "lib_general"),
    ("more_itertools","lib_general"),
    ("pymdown",     "lib_general"),
    ("asv",         "lib_general"),
    ("astarte",     "lib_general"),
    ("pyproject",   "lib_general"),
    ("pygame",      "lib_general"),
    ("marshmallow", "lib_general"),
    ("pint",        "lib_general"),
    ("ansi",        "lib_general"),
    ("ulid",        "lib_general"),
    ("jsonargparse","lib_general"),
    ("pypistats",   "lib_general"),
    ("agavepy",     "lib_general"),
    ("fffw",        "lib_general"),
    ("beets",       "lib_general"),
    ("mailmerge",   "lib_general"),
    ("exceptiongroup", "lib_general"),
    ("lexicon",     "lib_general"),
    ("structlog",   "lib_general"),
    ("reportseff",  "lib_general"),
    ("biomedsheets","lib_general"),
    ("modin",       "lib_general"),
]

DOMAIN_LABELS = {
    "type_checker":  "Type-checker / linter",
    "data_ml":       "Data / ML / scientific",
    "web_api":       "Web / REST API",
    "cloud_devops":  "Cloud / DevOps / infra",
    "db_orm":        "DB / ORM / data-layer",
    "graph_geo":     "Graph / geo / visualization",
    "testing_ci":    "Testing / CI tools",
    "lib_general":   "General-purpose library",
    "unknown":       "Unknown / uncategorised",
}


def classify_domain(instance_id: str) -> str:
    repo_part = instance_id.split("__")[-1] if "__" in instance_id else instance_id
    repo_lower = repo_part.lower()
    for fragment, category in DOMAIN_MAP:
        if fragment in repo_lower:
            return category
    return "unknown"


# ── Patch analysis ────────────────────────────────────────────────────────────

def analyze_patch(patch_diff: str | None) -> dict[str, int | str]:
    if not patch_diff:
        return {"files_changed": 0, "lines_added": 0, "lines_removed": 0,
                "net_lines": 0, "patch_type": "no_patch", "has_test_changes": False,
                "has_code_changes": False}

    files_changed = len(re.findall(r"^diff --git", patch_diff, re.MULTILINE))
    lines_added = patch_diff.count("\n+") - patch_diff.count("\n+++")
    lines_removed = patch_diff.count("\n-") - patch_diff.count("\n---")

    # Categorise changed files
    changed_paths = re.findall(r"^(?:\+\+\+|---) (?:a|b)/(.+)$", patch_diff, re.MULTILINE)
    has_test = any(
        p.startswith("test") or "/test" in p or p.startswith("tests/")
        for p in changed_paths
    )
    has_code = any(
        not (p.startswith("test") or "/test" in p)
        for p in changed_paths
        if p.endswith(".py")
    )

    patch_type: str
    if files_changed == 0:
        patch_type = "no_patch"
    elif files_changed == 1:
        patch_type = "single_file"
    else:
        patch_type = "multi_file"

    return {
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "net_lines": lines_added - lines_removed,
        "patch_type": patch_type,
        "has_test_changes": has_test,
        "has_code_changes": has_code,
    }


# ── Composite task class ──────────────────────────────────────────────────────

def assign_task_class(domain: str, patch_info: dict) -> str:
    """Combine domain and patch signals into a fine-grained task class."""
    patch_type = patch_info["patch_type"]
    has_test = patch_info["has_test_changes"]
    net = patch_info["net_lines"]

    suffix = ""
    if patch_type == "no_patch":
        suffix = "_nopatch"
    elif patch_type == "single_file" and not has_test:
        suffix = "_single_code"
    elif patch_type == "single_file" and has_test:
        suffix = "_single_with_test"
    elif patch_type == "multi_file":
        suffix = "_multi_file"

    return f"{domain}{suffix}"


def main() -> None:
    sessions = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(TRACES_DIR.glob("*.json"))]
    print(f"Classifying {len(sessions)} sessions…")

    records: list[dict] = []
    domain_counts: Counter[str] = Counter()
    task_class_counts: Counter[str] = Counter()

    for s in sessions:
        domain = classify_domain(s["instance_id"])
        patch_info = analyze_patch(s["outcome"].get("patch_diff"))
        task_class = assign_task_class(domain, patch_info)
        resolved = s["outcome"]["result"] in ("pass", "resolved", True, "true", "1")

        rec = {
            "session_id": s["session_id"],
            "instance_id": s["instance_id"],
            "scaffold": s["scaffold"],
            "model": s["model"],
            "domain": domain,
            "domain_label": DOMAIN_LABELS[domain],
            "task_class": task_class,
            "resolved": resolved,
            "turn_count": s["turn_count"],
            **patch_info,
        }
        # Token totals if present
        tok = s.get("session_token_totals") or {}
        rec["tokens_input"] = tok.get("input", 0)
        rec["tokens_output"] = tok.get("output", 0)
        rec["tokens_total"] = tok.get("total", 0)

        records.append(rec)
        domain_counts[domain] += 1
        task_class_counts[task_class] += 1

    # Write per-session taxonomy
    taxonomy_path = OUT_DIR / "task_taxonomy.json"
    taxonomy_path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    # Write summary
    domain_resolve: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "resolved": 0})
    for r in records:
        domain_resolve[r["domain"]]["total"] += 1
        domain_resolve[r["domain"]]["resolved"] += int(r["resolved"])

    summary = {
        "n_sessions": len(records),
        "domain_distribution": {
            d: {
                "count": domain_counts[d],
                "label": DOMAIN_LABELS[d],
                "resolved": domain_resolve[d]["resolved"],
                "resolve_rate": round(domain_resolve[d]["resolved"] / domain_counts[d], 3)
                    if domain_counts[d] else 0.0,
            }
            for d in sorted(domain_counts, key=lambda x: -domain_counts[x])
        },
        "task_class_distribution": dict(task_class_counts.most_common()),
        "patch_type_distribution": dict(
            Counter(r["patch_type"] for r in records)
        ),
    }

    summary_path = OUT_DIR / "taxonomy_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nDomain distribution:")
    for d, info in summary["domain_distribution"].items():
        print(f"  {info['label']:<35} {info['count']:>3} sessions  "
              f"resolve={info['resolve_rate']:.0%}")

    print(f"\nPatch type distribution: {summary['patch_type_distribution']}")
    print(f"\nTask class count: {len(task_class_counts)} unique classes")
    print(f"\nTaxonomy written to {taxonomy_path}")
    print(f"Summary written to {summary_path}")


if __name__ == "__main__":
    main()
