# Project Spec: tracegauge — Public PyPI Packaging & Release (Iteration P3)

## Goal

Publish the product to the public Python Package Index (PyPI) under the name **tracegauge** (AGPL-3.0) so that anyone, anywhere, on a fresh machine can run:

```
pip install tracegauge
tes serve
```

— with no repo clone, no `-e .`, no setup. The bundled baselines, the CLI (`tes`), the watcher, and the dashboard all work out of the box. This is the full, polished public release — NOT a half-baked "it technically uploads" version.

## The one irreversible-action discipline (read first)

Publishing to public PyPI is a ONE-WAY DOOR in two ways:
1. **The name `tracegauge` is claimed permanently** once the first version uploads.
2. **A version number is burned forever** — PyPI does not allow re-uploading the same version, even after a `yank`. A broken `1.0.0` cannot be replaced by a fixed `1.0.0`; you'd have to ship `1.0.1` with `1.0.0` permanently visible as broken.

Therefore: **everything is validated in a FRESH, CLEAN environment BEFORE the irreversible upload.** Build the wheel, install it in a brand-new virtualenv (as a stranger would, with NO access to the repo), and confirm `pip install <wheel>` → `tes serve` works end-to-end — BEFORE `twine upload`. We test against TestPyPI first (the sandbox index) and only then push to real PyPI. The first public version must work on a stranger's fresh machine.

## Current state

See CURRENT_STATE.md. P1 + P2 complete:
- `tes/` SDK package (adapt, classify, baselines, waste, judge, score, report, store, watcher, web).
- `tes score` CLI + `tes serve` (watcher + localhost dashboard).
- 158 tests green. Behavior-preservation, moat (localhost-only), judge-off-in-background, output-honesty all verified.
- Currently installable only via repo clone + `pip install -e .`.
- Bundled artifact: `tes/data/cc_baselines.json`.
- Reports 01-11 immutable.

## Naming / identity (locked)
- PyPI package name: **tracegauge** (verified FREE on PyPI as of this spec).
- CLI command: **tes** (kept — already built, muscle-memory; the package name is the brand, the command is the tool). Optionally also register `tracegauge` as a console-script alias to `tes` for discoverability — decide in decision 3.
- License: **AGPL-3.0** (free use + self-host; protects against closed-source commercial competitors; conventional for open-core commercial intent).

## Scope

### In scope (full release, all of it)
1. **pyproject.toml as the single source of truth**: name=tracegauge, version (semver, start 0.1.0 — see decision 1), description, long_description (the README), author, license=AGPL-3.0, classifiers (Development Status, License :: OSI Approved :: GNU Affero..., Python versions, Topic), `requires-python`, dependencies pinned to safe ranges, `console_scripts` entry point(s) so `tes` (and optionally `tracegauge`) work after a clean install.
2. **Package data**: ensure `tes/data/cc_baselines.json` is included in the wheel (package-data / include declaration) so baselines ship and load after install — NOT left behind as a repo-only file. A test confirms the installed package can load baselines.
3. **LICENSE file**: full AGPL-3.0 text at repo root; SPDX identifier in pyproject; license referenced in README.
4. **README as the PyPI landing page**: the public face. Must carry the domain-of-validity honesty LOUDER than the private docs (strangers rely on it without this project's context): what the three axes mean, the high-waste-infra-outlier corpus caveat (report 11), the tiered judge (token+waste local/free; trajectory needs a local judge), the moat (data never leaves your machine), AGPL terms, and an honest "what this does NOT claim" section (no human-accuracy validation, waste = observable-invariant only). Quickstart: `pip install tracegauge` → `tes serve`.
4. **Versioning**: `__version__` in the package, single-sourced with pyproject (e.g. importlib.metadata or a `_version.py`). Semver. A `tes --version` flag.
5. **Build + clean-room validation**: `python -m build` produces wheel + sdist; install the WHEEL in a fresh venv with no repo access; confirm `tes --version`, `tes score <sample>`, `tes serve --help`, and baseline-loading all work. This is the pre-publish gate.
6. **TestPyPI dry run**: upload to TestPyPI (sandbox), `pip install -i test.pypi.org ... tracegauge` in a fresh venv, confirm it works — BEFORE real PyPI.
7. **Real PyPI publish**: `twine upload` to production PyPI (requires the user's PyPI account + API token — a USER action, like the HF login). Tag the release in git (`v0.1.0`).
8. **Post-publish smoke**: `pip install tracegauge` from REAL PyPI in a fresh venv, `tes serve`, confirm end-to-end.

### Out of scope
- Changing any scoring logic / baselines / detectors / judge config (P3 is packaging + publishing only — behavior unchanged; a test confirms scores identical to P2).
- The corpus-contribution upload pipeline (still design-only).
- Hosted judge / any data-off-machine path (moat).
- A `conda` / `brew` / `docker` distribution (PyPI only this phase; note others as future).
- Domain registration / website / marketing site (note `tracegauge.dev` as a recommended user-action, don't build).
- Relicensing analysis beyond choosing AGPL-3.0 (lawyer review is a user-action before the raise; AGPL is the publish-now default).
- Modifying reports 01-11.

## Tech stack
- Standard Python packaging: `pyproject.toml` (PEP 621), `build`, `twine`.
- `importlib.metadata` for `__version__` single-sourcing (no duplicate version strings).
- A fresh-venv clean-room test (the executor creates a throwaway venv, installs the built wheel, runs the CLI — proving no hidden repo dependency).
- No new runtime deps unless required for packaging; keep the install lean.

## Architecture (changes/additions)
```
pyproject.toml          # rewritten: full metadata, AGPL, entry points, package-data, deps
LICENSE                 # NEW: full AGPL-3.0 text
README.md               # rewritten as PyPI long-description + honesty front-and-center
tes/_version.py or
  importlib.metadata     # single-sourced __version__; `tes --version`
MANIFEST.in (if needed) # ensure cc_baselines.json + LICENSE + README in sdist/wheel
tests/
├── test_packaging.py          # NEW: installed package loads baselines; __version__ present; entry point resolves
└── (all P1+P2 tests still green)
scripts/release/        # NEW (optional): build + testpypi + pypi helper scripts (documented, run by user for the token steps)
```

## Key design decisions (resolve early, escalate)
1. **Starting version**: 0.1.0 (signals "real but early; API may evolve") vs 1.0.0 (signals "stable, committed"). Recommendation: **0.1.0** — it's an honest first public release, sets expectations that it's early, and lets you iterate (0.x) without implying API stability you haven't promised. A pre-raise product publishing 1.0.0 over-claims maturity. Decide.
2. **CLI command**: keep `tes` only, or also register `tracegauge` as an alias? Recommendation: register BOTH (`tes` for brevity, `tracegauge` for discoverability/brand) pointing at the same entry point — cheap, helps a new user who installed `tracegauge` intuitively try `tracegauge ...`. Decide.
3. **Dependency pinning**: pin deps to compatible ranges (e.g. `flask>=3,<4`) — loose enough to coexist in a user's env, tight enough to avoid breakage. Audit the actual runtime deps (flask, httpx, etc.) and declare them precisely. No dev/test deps in the runtime requires.
4. **Baseline data shipping**: confirm the mechanism (package-data in pyproject `[tool.setuptools.package-data]` or equivalent for the build backend) actually lands `cc_baselines.json` in the wheel. The clean-room test must load it from the INSTALLED location, not a repo path.
5. **README honesty scope**: the public README must include a "Scope & Limitations" section that states the corpus caveat (high-waste infra outlier; ~1.4% generalizable), the no-human-accuracy-validation limit, the tiered judge, and the moat — prominently, not buried. A stranger installing this must understand what it does and doesn't claim WITHOUT reading reports 01-11.

## Verification commands
```yaml
- name: behavior-unchanged
  cmd: python -m pytest -q   # all 158 P1+P2 tests still green; scores unchanged
  required: true
- name: builds-clean
  cmd: python -m build && ls dist/*.whl dist/*.tar.gz
  required: true
- name: clean-room-install
  cmd: |
    python -m venv /tmp/tg_clean && /tmp/tg_clean/bin/pip install dist/*.whl && \
    /tmp/tg_clean/bin/tes --version && /tmp/tg_clean/bin/tes serve --help && \
    /tmp/tg_clean/bin/python -c "import tes; from tes.baselines import load_baselines; load_baselines(); print('baselines load from installed pkg OK')"
  required: true
- name: packaging-tests
  cmd: python -m pytest tests/test_packaging.py -v
  required: true
- name: metadata-sane
  cmd: python -m twine check dist/*   # PyPI metadata/long-description renders
  required: true
```

## Escalation rules
- BEFORE any `twine upload` to REAL PyPI: the clean-room install + TestPyPI dry-run must both pass, AND the consultant must confirm. Real-PyPI upload is the irreversible step — it happens only after explicit go.
- The PyPI/TestPyPI account + API token are USER actions — surface the exact steps for the user; the orchestrator does not invent credentials.
- If the clean-room install fails (missing package-data, unresolved entry point, hidden repo dependency): STOP — fix and rebuild before any upload. A broken wheel must never reach even TestPyPI's namespace under a real version.
- BEFORE changing any scoring behavior: out of scope — packaging only.
- Version numbers: never re-use; if a build is bad, bump the version, never re-upload the same one.

## Hard rules
- BEHAVIOR UNCHANGED: P3 is packaging + publishing. Scores/baselines/detectors/judge identical to P2. The 158 tests stay green.
- CLEAN-ROOM BEFORE PUBLISH: validated in a fresh venv with no repo access before any upload; TestPyPI before real PyPI; real PyPI only on explicit consultant + user go.
- HONESTY ON THE PUBLIC FACE: README carries the corpus caveat, the no-accuracy-claim, the tiered judge, the moat — loudly. Publishing amplifies the honesty obligation; strangers rely on it without context.
- MOAT UNCHANGED: still localhost-only, no telemetry, no phone-home; publishing the package does not add any data egress.
- LICENSE: AGPL-3.0, full text in LICENSE, SPDX in pyproject, referenced in README.
- Reports 01-11 immutable. No human labels. Version numbers immutable once used.

## Budget
- Soft: 2-3 CC sessions. All local/$0 (build + clean-room venv are local; PyPI/TestPyPI are free).
- No GCP, no API spend.

## Success criteria (verify ALL before the irreversible publish)
- pyproject.toml complete: tracegauge, 0.1.0 (or chosen), AGPL-3.0, classifiers, requires-python, pinned deps, console_scripts (`tes` [+ `tracegauge`]), package-data including cc_baselines.json.
- LICENSE (full AGPL-3.0) + README (honesty-forward, PyPI long-description) present.
- `__version__` single-sourced; `tes --version` works.
- `python -m build` produces wheel + sdist; `twine check` passes.
- CLEAN-ROOM: fresh venv installs the wheel, `tes serve`/`tes score`/baseline-load all work with NO repo access. (The gate.)
- All 158 P1+P2 tests + new packaging tests green; behavior unchanged.
- TestPyPI dry-run install works in a fresh venv.
- THEN (explicit go only): real PyPI publish; `pip install tracegauge` from production works in a fresh venv; git tagged `v0.1.0`.
- README notes recommended user follow-ups: register `tracegauge.dev`, lawyer review of AGPL before raise (as notes, not built).

## Build order (orchestrator may adjust)
1. Read CURRENT_STATE.md + report 11 + spec.md + current pyproject/README. Internalize: irreversible-publish discipline, clean-room-before-upload, honesty-on-the-public-face.
2. Resolve decisions 1-2 (version 0.1.0, CLI alias) with consultant. HOLD.
3. Rewrite pyproject.toml (full metadata, AGPL, entry points, package-data, pinned deps). Add LICENSE (AGPL-3.0 full text). Single-source `__version__` + `tes --version`.
4. Rewrite README as the honesty-forward PyPI landing page (Scope & Limitations prominent).
5. Packaging tests: installed-pkg baseline load, version present, entry point resolves.
6. `python -m build`; `twine check`; CLEAN-ROOM install in a fresh venv (the gate) — confirm everything works with no repo access. HOLD for consultant read of the clean-room result.
7. TestPyPI upload + fresh-venv install from TestPyPI. Report. HOLD.
8. ON EXPLICIT GO ONLY: real PyPI `twine upload` (user provides token), tag `v0.1.0`, post-publish fresh-venv smoke from production PyPI. Report.
9. CURRENT_STATE.md → P3 done / published. Reports 01-11 untouched.
