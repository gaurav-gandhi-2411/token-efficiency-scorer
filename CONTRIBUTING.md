# Contributing

`tracegauge` (import name `tes`) is a solo-maintained portfolio project —
contributions are welcome, but the bar is the same one the maintainer
holds their own commits to: real tests, honest documentation, no
fabricated numbers.

## Dev setup

```bash
git clone https://github.com/gaurav-gandhi-2411/token-efficiency-scorer.git
cd token-efficiency-scorer
uv sync --frozen   # installs the exact locked dependency set, incl. dev deps
```

`uv` is the only supported package manager for this repo. `uv.lock` is
committed; never hand-edit it, and never run `uv sync` without `--frozen`
in CI or when verifying a change.

## Running tests and lint

```bash
uv run pytest tests/ -v
uv run ruff check .
uv run ruff format --check .
```

**`ruff` is currently informational-only in CI** (`.github/workflows/ci.yml`
— this repo never had it enforced before that workflow existed, and a large
share of `scripts/` predates it; see the workflow's own comments). It is
NOT yet a merge-blocking gate for the whole tree. This is a known,
deliberate, temporary state, not an oversight.

**Set up the pre-commit hook anyway, so new/touched files stay clean going
forward, even though CI doesn't enforce it repo-wide yet:**

```bash
uv run pre-commit install
```

After that, every `git commit` runs `ruff check --fix` and `ruff format`
against the files you're actually committing (not the whole repo) via
`uv run ruff` (`language: system` in `.pre-commit-config.yaml`,
deliberately not the `astral-sh/ruff-pre-commit` mirror repo — that mirror
pins its own `ruff` version separately from `uv.lock`, a second source of
truth that can silently drift; `uv run ruff` always resolves to the exact
version this repo has locked). Because it only checks staged files, it
will not block you on pre-existing debt elsewhere in the repo — only on
files your own commit touches. If you touch an already-non-compliant file,
you'll be asked to bring it into compliance as part of your change,
consistent with this project's "fix debt in code you're already touching"
convention.

**Windows note**: if `pre-commit install`/`pre-commit run` fails with a
traceback mentioning `pip._vendor.rich.markup` or similar during "Installing
environment," that's a corrupted `virtualenv` seed-wheel cache, not a
problem with this repo's config — clear
`%LOCALAPPDATA%\pypa\virtualenv\Cache` and `%USERPROFILE%\.cache\pre-commit`
and retry.

## Releasing

See `RELEASING.md` for the full tag-triggered release flow.
