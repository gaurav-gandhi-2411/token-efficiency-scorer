# Migrating from `tes` to `tracegauge`

The tool has one name now: **tracegauge**. Before 0.16 the PyPI distribution was already called
`tracegauge`, but the import package, the command and the environment variables were `tes`. The old
names keep working for two minor versions and are removed in 0.18.

| | Before | Now | Old name works until |
|---|---|---|---|
| Python import | `import tes` | `import tracegauge` | 0.17 (removed in 0.18) |
| Command | `tes score` | `tracegauge score` (short alias: `tgauge score`) | 0.17 (removed in 0.18) |
| Module run | `python -m tes` | `python -m tracegauge` | 0.17 (removed in 0.18) |
| Environment variables | `TES_DB_PATH`, ... | `TRACEGAUGE_DB_PATH`, ... | 0.17 (removed in 0.18) |
| Store directory | `~/.tes/` | `~/.tes/` (unchanged, see below) | not removed |

## Python imports

```python
# before
import tes
from tes.adapt import adapt_session

# after
import tracegauge
from tracegauge.adapt import adapt_session
```

`import tes` still works and emits one `DeprecationWarning` per process ("tes is deprecated, import
tracegauge; removed in 0.18."), pointing at the line that imported it. It is an alias, not a copy:
`tes.cost is tracegauge.cost`, so monkeypatching and `isinstance` checks behave the same through
either name. Python hides `DeprecationWarning` outside `__main__` by default; run with
`python -W error::DeprecationWarning` (or `-W default`) to find every import that still says `tes`.
`import tracegauge` never warns.

## Command line

`tracegauge` is the command and `tgauge` is a short alias for it; both behave identically and
`--version` prints `tracegauge <version>`. The `tes` command still runs but prints one line to
**stderr** first:

```
tes is deprecated; use tracegauge (or tgauge). It will be removed in 0.18.
```

The line goes to stderr only, so `--json` output and pipes are unaffected, and it is printed once per
run, not once per subcommand. Set `TRACEGAUGE_NO_DEPRECATION_NOTICE=1` (or `true` / `yes`) to
silence it, for example in a CI job you have not migrated yet.

## Environment variables

Each `TES_*` variable has a `TRACEGAUGE_*` replacement with the same meaning:

| Old | New |
|---|---|
| `TES_DB_PATH` | `TRACEGAUGE_DB_PATH` |
| `TES_PLAN_PATH` | `TRACEGAUGE_PLAN_PATH` |
| `TES_PRICE_TABLE` | `TRACEGAUGE_PRICE_TABLE` |
| `TES_NO_NOTICE` | `TRACEGAUGE_NO_NOTICE` |
| `TES_CORPUS_URL` | `TRACEGAUGE_CORPUS_URL` |
| `TES_CORPUS_ANON_KEY` | `TRACEGAUGE_CORPUS_ANON_KEY` |
| `TES_CORPUS_WITHDRAW_URL` | `TRACEGAUGE_CORPUS_WITHDRAW_URL` |

Rules, so a half-migrated setup does what you expect:

- If both are set, `TRACEGAUGE_*` wins.
- An empty value counts as unset, in both spellings. An empty `TRACEGAUGE_DB_PATH` therefore falls
  through to `TES_DB_PATH`; it does not mask it.
- If a `TES_*` variable is set without its `TRACEGAUGE_*` twin, the command line prints one stderr
  line naming it ("`TES_DB_PATH` is deprecated; use `TRACEGAUGE_DB_PATH`. ..."). The same
  `TRACEGAUGE_NO_DEPRECATION_NOTICE=1` silences it. Library calls never print.

Scripts that isolate the store for a test run (`TES_DB_PATH=/tmp/x.db`) keep working unchanged;
export both spellings while you support old and new versions side by side.

## Where your data lives: `~/.tes/` does not move

The store, the cached intelligence results, the optional `prices.json` and `plan.json`, and the
contribution files all stay under `~/.tes/` (on Windows `%USERPROFILE%\.tes\`). The directory name
is a storage detail, not a product name, and moving it would orphan every user's scored history
(or force a silent copy of session-derived data) for no functional gain. Nothing in tracegauge
writes anywhere else; `tests/test_store_location.py` checks that every default path resolves under
`~/.tes/`. If you want the database somewhere else, set `TRACEGAUGE_DB_PATH` or pass `--db-path`.

## Timeline

| Version | `tes` import / command / `TES_*` |
|---|---|
| 0.16 | work; warn |
| 0.17 | work; warn |
| 0.18 | removed |

Nothing else changes in the tool's output: scores, JSON schemas and exit codes are the same.
