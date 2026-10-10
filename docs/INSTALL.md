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

Local wheel `tracegauge-0.14.0-py3-none-any.whl`: 292,384 bytes at the commit that introduced the extra (the
size, venv and `pip install` figures below were measured with that wheel), 299,510 bytes when rebuilt at `ef1de9f`
(`uv build --wheel` from a `git archive` of that commit; it grows by a few hundred bytes per source commit). Venv:
fresh `python -m venv` (CPython 3.13.5, Windows 11), `pip install <wheel>` with a warm pip cache unless stated.
Raw commands and outputs are in the W1A-f report.

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

`uvx --from <local wheel> tracegauge quickstart` wall time, n=5 each, cold = a brand-new empty uv cache per run
(downloads flask, httpx and the rest from PyPI), warm = populated uv cache after one uncounted priming run.
Every run printed the REPEATED-FAILED-RETRY finding, so this is time-to-first-finding.

Re-measured 2026-10-10 on the wheel built at `ef1de9f`, **on a contended host**: the machine's CPU load
(`Win32_Processor.LoadPercentage`) was 90 to 100% in 40 of 45 samples taken over about half an hour (lowest 48%),
from other jobs that were not part of this measurement, and 90 to 100% immediately before each timed run. Three
waits of several minutes did not bring it down, so these are not idle-machine numbers.

| command | cold: mean / median / min / max (s) | warm: mean / median / min / max (s) |
|---|---|---|
| `quickstart`, contended (above) | 15.78 / 7.92 / 5.36 / 33.33 (runs 33.33, 24.61, 5.36, 7.71, 7.92) | 1.97 / 1.71 / 1.40 / 2.99 (runs 2.99, 2.28, 1.71, 1.45, 1.40) |

Earlier run on the same machine, wheel from the commit that introduced the extra, **load not recorded** (so it
cannot be called uncontended): `quickstart` cold 4.53 / 4.96 / 3.06 / 5.07 s and warm 1.49 / 1.49 / 1.06 / 2.00 s;
`score <sample session>` cold 5.47 / 5.05 / 4.54 / 7.06 s and warm 2.14 / 1.16 / 1.02 / 6.29 s. An independent
verifier measured 6 to 27 s for uvx runs while the machine was also at 99 to 100% CPU, and 0.9 to 1.3 s for the
installed `tracegauge quickstart`. The honest reading: warm uvx is about 1 to 3 s, cold depends on the network and on
machine load and ranged from 3 to 33 s here, and a cold-run claim of "under 10 s" has not been shown on an idle
machine. These are single-machine measurements with network and antivirus variance; treat them as an order of
magnitude, not a guarantee.
