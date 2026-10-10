# Upgrading to 0.15 (legacy rows)

0.15 counts each API response's usage once and prices 1-hour cache writes at their own rate.
Sessions your store scored with an older version (0.14 and earlier) carry numbers that are now
known to be wrong: tokens and dollars ~2x too high (usage was added once per content block), and
1-hour cache writes under-priced. Those rows are **legacy rows**.

**What happens to them.** Nothing is deleted and no stored number is rewritten by the upgrade. A
legacy row is *derived*: `adapter_version` or `cost_version` is empty or older than the running
version (every row of a 0.14-or-earlier store, since those columns did not exist). `tes rescore`
clears it by writing the current versions. The columns are added by the first command that opens
the store; adding them is idempotent, safe to run twice and safe if the watcher and a CLI command
open the store at the same moment.

## What each command does with legacy rows

| Command | Legacy rows |
|---|---|
| self-baselines (verdict bands, `tes score`, dashboard) | excluded. A task type with no current rows is `building` again; its DOV text says how many legacy rows were left out. |
| live alarm (`monitor`, `serve --alarm`) | excluded from the pool the threshold is computed from. |
| `budget` | excluded from the pace and projection. `--json` carries `legacy_rows_excluded` (legacy sessions with a cost in the window); text says how many were left out. If every session in the window is legacy there is no projection, not a legacy one. |
| `cost` (periods, per-project, coverage) | excluded from `total_usd`, `session_count`, `by_project` and token coverage. They appear once, on a separate line labelled `legacy (pre-0.15 accounting, overcounted ~2x)`, never added to the corrected total. `--json`: `legacy_rows_excluded` and a `legacy` object `{label, session_count, total_usd, included_in_total_usd: false}`. |
| `cost --roi` | built on the corrected total only, so the multiple is a floor; it says so when legacy rows were excluded. |
| `impact`, `patterns`, `ask`, dashboard session list, `export-contribution` | **not filtered in this release.** They read per-session fields (edit operations, attribution fractions, the list itself) and still show legacy rows with their original numbers. |

Only `schema_version` 1 keys were added; none were removed or renamed.

## The one-time notice

The first command that opens an existing store (`score`, `cost`, `budget`, `monitor`, `serve`,
`impact`, `patterns`, `ask`, `export-contribution`, or bare `tes`) prints this to **stderr**
(never into `--json` stdout):

```
tracegauge: 1452 of 1452 stored session(s) were scored by an older version ...
  rescorable (transcript still on disk): 0
  unrecoverable (transcript gone):       1452
```

It is shown once per store per tes version, and again only if more legacy rows appear later.
Silence it with `TES_NO_NOTICE=1` or `tes --quiet <command>` (the flag goes before the command).
It never creates a store, never raises, waits at most 0.25 s on a locked store, and is skipped by
`rescore` and `backfill-waste`.

## `tes rescore`

```
tes rescore [--dry-run] [--json] [--db-path PATH] [--limit N]
```

Re-scores every legacy row whose transcript is still readable with the current adapter and price
table (real tokens, cost, subagent split, verdict band against the bundled baseline, attribution
fractions, waste events). Judge verdicts are kept. It reports four counts:

* **rescored**: legacy rows re-computed and now current.
* **skipped (source missing)**: the transcript is gone. The row is left exactly as it was and stays legacy.
* **failed (parse error)**: the transcript exists but yielded no usage records, or its cost could
  not be computed. The row is left exactly as it was and stays legacy (so a damaged file can never
  overwrite a stored number with zero).
* **not attempted (--limit)**: readable rows past `--limit` (most recently written first); run again.

`--dry-run` does the same computation and writes nothing: the store is opened read-only (immutable
when no `-wal` file exists), so it is not migrated and the file is byte-identical afterwards.

Idempotent: a second run rescores 0 rows and changes nothing. It never creates a store. Exit codes:
`0` ok, `1` no store / unopenable store / `--limit < 1`, `4` at least one readable row failed (the
rest were still rescored); see [EXIT_CODES.md](EXIT_CODES.md). `tes backfill-waste` still works as
before (it also refreshes stale rows, but without `rescore`'s zero-usage guard).

If the transcripts are gone (Claude Code deletes old ones), `rescore` cannot help: those rows stay
excluded and your self-baselines and alarm threshold rebuild from new sessions.
