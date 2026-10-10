# CI

Workflow: `.github/workflows/ci.yml`. Runs on pushes to `master`, pull requests to `master`, and manual dispatch;
a newer run on the same ref cancels an older one (`concurrency: cancel-in-progress: true`).

## Jobs

| Job (check name) | What | Legs |
|---|---|---|
| `lint-and-test` | `ruff check`, `ruff format --check`, mypy (informational), `check_release_version.py` (offline). One job: none of this varies by platform. | 1 (ubuntu, py3.11) |
| `tests (<os>, py<ver>)` | The full suite with the dev group, which includes the `patterns` extra's pins (numpy, scikit-learn). | 12: ubuntu, macos, windows x 3.10, 3.11, 3.12, 3.13 |
| `core-only (<os>, py<ver>)` | A real **no-extras** install, see below. | 4: ubuntu, windows x 3.10, 3.13 |
| `python-compat (<ver>)` | The pre-existing ubuntu leg for 3.14, kept so no claimed Python version loses coverage. | 1 (3.14) |
| `python-compat (every leg)` | Aggregate: red unless `python-compat`, `tests` and `core-only` all succeeded (a skipped or cancelled leg is not success). | 1 |
| `manifest-provenance`, `readme-pypi` | Unchanged. | 1 each |

All matrices use `fail-fast: false`, so one failing platform does not hide the others.

## Required status checks

Branch protection on `master` requires exactly: `lint-and-test`, `manifest-provenance`,
`python-compat (every leg)`, `readme-pypi`. This workflow keeps all four names, so no repository setting needs to
change. The matrix legs have new names (`tests (...)`, `core-only (...)`) and are deliberately **not** required
individually: they are covered by the `python-compat (every leg)` aggregate, which also covers legs added later.
Do not add a per-leg name to branch protection; it goes stale the day the matrix changes.

## Core-only leg

`pip install tracegauge` with no extras has no numpy or scikit-learn. `tests/test_core_without_extras.py`
simulates that inside the full dev environment by blocking the imports in a subprocess; the `core-only` job is the
real thing. In an empty venv it: builds and installs the wheel, asserts numpy/sklearn are absent, runs
`tes quickstart` (must show the `REPEATED-FAILED-RETRY` finding), runs `tes score --json` on the bundled sample
session, then installs the checkout editable (still without extras, which also satisfies
`tests/_checkout_guard.py`) and runs `tests/test_core_without_extras.py`.

## Coverage policy

`[tool.coverage.report] fail_under` in `pyproject.toml` is a ratchet, set to the measured total floored to an
integer. It is enforced on **one** leg, ubuntu py3.12 (the only leg run with `--cov=tes`); the other legs run without
coverage so a platform-dependent branch cannot flip the total across the line. Raise the number when coverage rises;
do not lower it to make a PR pass. `omit` keeps `tests/`, `scripts/` and `notebooks/` out of the measure.

Reproduce locally: `uv sync --frozen && uv run pytest --cov=tes --cov-report=term-missing`.

## Platform-sensitive spots reviewed

Windows runs the suite locally; macOS is only covered by CI and static review. Reviewed and found portable:
user paths come from `Path.home()` (tests redirect `HOME` and `USERPROFILE` together), every text read/write in
`tes/` passes an explicit `encoding`, the only binary open is in `store.py`, and no `tes/` code uses `fcntl`,
`signal`, `chmod`, symlinks or `os.sep`. One real portability bug was found by running Python 3.10 (not by reading):
`datetime.UTC` is 3.11+ (`tests/test_py310_compat.py` guards it).
