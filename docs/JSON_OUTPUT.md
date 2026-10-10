# `--json` output

Every reporting command takes `--json`: `score`, `cost`, `budget`, `monitor`, `impact`, `patterns`.
The contract, pinned by `tests/test_cli_json.py` (exact key sets) and implemented in
`tes/json_out.py`:

* **stdout is exactly one JSON document** (`score` on a directory prints one document per session,
  each complete). Warnings, hints and errors go to stderr; on an error stdout is empty and the exit
  code is non-zero (see [EXIT_CODES.md](EXIT_CODES.md)).
* Every document starts with `schema_version` (int, currently `1`) and `command`.
* **The key set is fixed per command.** A field that does not apply is `null` (or `[]`), never
  absent, so consumers can index without guards. Adding a key is backwards compatible; renaming or
  removing one bumps `schema_version`.
* **Money is never silently partial.** Where a USD figure can be incomplete the document carries
  `priced` (bool) and `unpriced_models` (list of model ids missing from the price table). When
  `priced` is `false` the figure covers the priced turns only: it is a floor, never a true total.
  **Unpriced rows keep a numeric USD of `0.0`** (changing it to `null` would break consumers that
  sum it, and bump `schema_version`), so the number alone cannot tell "free" from "unknown". The
  additive boolean `cost_known` does: it is `false` exactly when the figure is a placeholder (models
  are unpriced and nothing at all was priced, or no cost was computed) and `true` otherwise,
  including for a partly priced figure (known, but a floor: see `priced`). It appears on `score`
  (`session_cost_usd`), `cost` (`total_usd` and each `by_project` row), `budget`
  (`total_usd_so_far`, `projected_usd_for_window`; `null` when `available` is false) and `monitor`
  (`live_cost_usd`; `null` unless `status` is `ok`). Consumers must read `cost_known` (or `priced`)
  before treating a `0.0` as a price. This is a new key, so `schema_version` stays `1`.
  `cost` also reports `sessions_missing_cost` (sessions in the window with no cost stored yet; they
  are not in `total_usd` and are not counted as $0).

## `cost --week|--month|--since D [--roi]`

`period{label,start,end}`, `total_usd`, `priced`, `unpriced_models`, `unpriced_models_incomplete`,
`session_count`, `sessions_missing_cost`, `session_coverage_pct`, `token_coverage_pct`,
`token_total`, `token_priced`, `tokens_unpriced`, `sessions_unpriced`,
`by_project[{project,total_usd,session_count,priced,unpriced_models}]`, `roi`.

Coverage counts a session as priced only when none of its turns used an unpriced model.
`sessions_unpriced` is the number of sessions in the window that are not fully priced (an unpriced
model among their turns, or no cost stored); `token_priced` is the tokens of fully priced sessions
and `tokens_unpriced = token_total - token_priced`. The store keeps no per-model token split, so a
session with one unpriced turn moves all its tokens to `tokens_unpriced` (an upper bound on the
tokens that could not be priced). `session_coverage_pct` and `token_coverage_pct` follow the same
rule, so they are below 100 whenever `priced` is `false`.

`roi` is `null` unless `--roi` is passed; otherwise `{status, plan_names, plan_cost_usd,
api_equivalent_usd, multiple, is_floor, error}` where `status` is `ok`, `no_plan`,
`no_priced_sessions` or `plan_config_error` (numbers are `null` unless `ok`; `is_floor` is true when
unpriced turns were excluded from `api_equivalent_usd`).

## `budget [--window-days N]`

`available` (false when the window has no cost data; the numeric fields are then `null`),
`window_days`, `session_count`, `days_observed`, `total_usd_so_far`, `projected_usd_for_window`,
`priced`, `unpriced_models`, `message`.

## `monitor`

`status` is `no_active_session`, `insufficient_data` or `ok`; `active` is false only for the first.
The session fields (`session_id`, `task_type`, `live_cost_usd`, `priced`, `unpriced_models`,
`live_context_tokens`, `live_resend_ratio`, `context_resend_dominant`, `ai_turn_count`,
`domain_of_validity`) are `null` unless `status` is `ok`; `cc_path` and `source_path` are always
present. `alarm` is `null`, or `{message, resend_pct, baseline_p75_tokens, plan_type, threshold_tokens,
baseline_tier, baseline_n, baseline_percentile}`, and is non-null exactly when the process exits 3
(`baseline_p75_tokens` is the legacy name of `threshold_tokens`; it holds the threshold at whatever
percentile is configured). `alarm_baseline` (non-null whenever `status` is `ok`) says what the alarm compared
against, or why there is nothing to compare: `{status ("active" | "disabled"), tier, threshold_tokens (null
when disabled), n, percentile, window_days, era, task_type, reason}`; the tiers and the one-line reasons are
in `docs/ALARM.md`. Live figures are estimates for an in-progress session.

## `impact [--top N]`

`top_n`, `sessions_with_data`, `sessions_legacy`, `total_operations`, `total_additions`,
`total_deletions`, `prior_content_unknown_additions`, `prior_content_unknown_pct`,
`untested_tool_shape_operations`, `untested_tool_shape_pct`, `top_files` and `top_directories`
(each `[{path,edits,additions,deletions,sessions_touched}]`). The two `_pct` fields qualify the
totals (a full-file rewrite looks like a new file; MultiEdit/NotebookEdit extraction is unverified
on real data) and are `null` when their denominator is zero.

## `patterns [--recompute]`

`valid`, `status`, `n_sessions`, `domain_of_validity`, `analysis`. `analysis` is `null` when
`valid` is false (for example, not enough content sessions yet); otherwise it is the cached
analysis as `tes patterns` reads it (`k`, `silhouette`, `archetypes`, `anomaly_count`,
`anomaly_pct`, ...). If the pattern-analysis dependencies are not installed the command prints a
one-line hint to stderr (`pip install "tracegauge[patterns]"`) and exits 1; `require_patterns_extra()`
in `tes/patterns_extra.py` is the single check.

## `score`

The full result (see `tes/score.py::ThreeAxisResult`), now with `schema_version` as the first key and
`lever_hint` (a string when a data-gated cost lever fires, else `null`; the same function and
thresholds as the dashboard's takeaway). `priced` / `unpriced_models` are as above.
`cost_models` (additive) lists the price-table keys of the priced turns; the human cost note uses it to name
the cache-read multiplier each model was billed at.
