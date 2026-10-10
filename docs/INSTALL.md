# Installing tracegauge

The package is `tracegauge` on PyPI. It installs two equivalent commands, `tracegauge` and `tes`.
Python 3.10 or newer. Nothing leaves your machine unless you opt in to something that says so.

## Fastest: run it without installing (uvx)

```bash
uvx tracegauge quickstart            # scores a bundled synthetic session, prints a verdict + a finding
uvx tracegauge score                 # scores your most recent real Claude Code session
```

`uvx` (from [uv](https://docs.astral.sh/uv/)) builds a cached throwaway environment for the core
install, so the second run is fast. (Pre-publish note: the timings below were measured by running
`uvx --from <local wheel> tracegauge ...`; the same command against the published package can only be
measured after publish.)

## Persistent install

```bash
pipx install tracegauge              # isolated, on PATH
uv tool install tracegauge           # same idea with uv
pip install tracegauge               # into the active environment (use a venv)
```

## Extras

| Install | Adds | Needed for |
|---|---|---|
| `tracegauge` (core) | flask, httpx (and their dependencies) | `quickstart`, `score`, `cost`, `budget`, `monitor`, `impact`, `serve` (dashboard), waste detection, lever hint |
| `tracegauge[patterns]` | numpy, scikit-learn (scipy comes with it) | `tes patterns`, `tes ask`, the dashboard `/patterns` page and `/ask` box |

Without the extra, those commands exit 1 with one line on stderr:

```text
[ERROR] Pattern analysis needs the patterns extra: pip install "tracegauge[patterns]" (adds numpy, scikit-learn, scipy).
```

and the dashboard shows an "extra not installed" page for `/patterns` and a JSON `501`
(`{"error": ..., "needs_extra": "patterns"}`) for `/ask`; every other page keeps working.
Nothing at import time or at CLI start-up needs the extra (pinned by `tests/test_core_without_extras.py`,
which blocks numpy, scipy and sklearn in a subprocess and runs the core commands).

## Measured facts

Local wheel `tracegauge-0.14.0-py3-none-any.whl` (292,384 bytes) built from branch `w1a-first-run`
at the commit that introduced the extra; fresh `python -m venv` (CPython 3.13.5, Windows 11),
`pip install <wheel>` with a warm pip cache unless stated. Raw commands and outputs are in the W1A-f report.

| | core | core + `[patterns]` |
|---|---|---|
| site-packages size (sum of file sizes) | 20.0 MiB (1,585 files) | 246.2 MiB (7,774 files) |
| of which pip itself (present in every venv) | 10.4 MiB | 10.4 MiB |
| sum of the sizes listed in the installed RECORD files | 11.0 MiB | 182.7 MiB |
| installed distributions (`pip list`, incl. pip) | 16 | 23 |
| install wall time (`pip install`) | 10.6 s, 12.0 s, 14.0 s (14.0 s was the cold pip cache) | 60.1 s, 69.5 s, 154.9 s (cold) |
| `import tes` (median of 5) | 0.34 s | 0.34 s |
| `tracegauge --version` (median of 5) | 0.43 s | 0.43 s |

The core install is about half of the 40 MB budget with pip counted and about a quarter without it. Most of the
non-pip weight is werkzeug (1.6 MiB), anyio (1.2 MiB), tracegauge itself (1.2 MiB), jinja2 (1.1 MiB), click, flask,
httpx. Moving numpy and scikit-learn to the extra removed about 226 MiB from the default install.

`uvx --from <local wheel> tracegauge ...` wall time, n=5 each, cold = a brand-new empty uv cache (downloads
flask, httpx and the rest from PyPI), warm = populated uv cache:

| command | cold: mean / median / min / max (s) | warm: mean / median / min / max (s) |
|---|---|---|
| `quickstart` | 4.53 / 4.96 / 3.06 / 5.07 | 1.49 / 1.49 / 1.06 / 2.00 |
| `score <sample session>` | 5.47 / 5.05 / 4.54 / 7.06 | 2.14 / 1.16 / 1.02 / 6.29 |

`quickstart` prints its first finding (a REPEATED-FAILED-RETRY with proof turns) in all of these runs, so
time-to-first-finding is the `quickstart` row. These are single-machine, single-run-set numbers with network and
antivirus variance (note the 6.29 s warm outlier); treat them as an order of magnitude, not a guarantee.
