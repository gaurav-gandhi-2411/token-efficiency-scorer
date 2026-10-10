# Exit codes

Every `tracegauge` / `tes` command exits with one of these codes. They are a contract for scripts,
Claude Code hooks and CI: new codes may be added, existing ones are never renumbered. The same
table is in each command's `--help` epilog. The constants live in `tes/exit_codes.py`.

| Code | Meaning | Commands |
|---:|---|---|
| 0 | OK. Includes "scored, nothing notable", "no alarm", and `monitor` with no active session. | all |
| 1 | Bad usage or nothing to do: path not found, no `.jsonl` files / no sessions found, `score --judge --no-judge` (checked by our own code), a bad `--since` value, or the store (`tes.db`) cannot be opened. | all |
| 2 | Command-line usage error reported by argparse: unknown flag, missing argument or value, a missing required choice (`cost` with none of `--week`/`--month`/`--since`), or flags argparse itself declares mutually exclusive (`cost --week --since ...`). We never use 2 ourselves. | all |
| 3 | The `monitor` alarm fired for the active session. | `monitor` |
| 4 | At least one session could not be read or parsed (for example a file that is not valid UTF-8). | `score` |

## Behaviour that scripts rely on

* **`score` with several sessions** (a directory, or several files) keeps scoring the rest after a
  failure, then exits 4 and lists every failed file on stderr
  (`[ERROR] 1 of 3 session(s) could not be parsed: <paths>`). Successful sessions are still printed
  (and stored). With `--json`, stdout holds only the JSON documents of the sessions that scored;
  errors go to stderr.
* **A file that parses but has nothing to score** (empty, or only unreadable lines) is not an error:
  it scores as `unavailable` and exits 0. Exit 4 is for files the adapter cannot read at all.
* **`monitor` exits 3 only when the alarm fires.** "No active session" and "not enough data to
  score yet" exit 0 on purpose: a SessionEnd hook or cron job that runs `tes monitor` between
  sessions must not read an idle machine as a failure, and a distinct "nothing to monitor" code
  would make `tes monitor && ...` chains fail on every idle run. With `--json`, the document says which case it was
  (`"status": "no_active_session"`, `"active": false`, `"alarm": null`). There is no `--fail-on-alarm` flag:
  the alarm already is the failure condition, and it is itself data-gated (it stays silent until
  your own baseline for that task type is built).
* **Store errors** (`cost`, `budget`, `impact`: cannot open `~/.tes/tes.db`) used to print
  `[ERROR]` and exit 0. They now exit 1.
* **"Contradictory flags" is two different codes.** Flags argparse declares mutually exclusive
  (`cost --week --since D`) exit 2; `score --judge --no-judge` is rejected by our own check and
  exits 1. Both print the reason on stderr. `tests/test_cli_exit_codes.py` runs every case in this
  document.
* Success paths are unchanged: every command that exited 0 on success still does.

## Examples

```bash
# CI step: score everything, tolerate unparseable files but say so
tracegauge score ./transcripts --no-judge --json > scores.json
case $? in 0) ;; 4) echo "some transcripts did not parse" ;; *) exit 1 ;; esac

# act on the alarm in a hook or cron job (monitor.json then holds the alarm object)
tracegauge monitor --json > monitor.json; [ $? -eq 3 ] && notify-send "tracegauge: session alarm"
```

Machine-readable output of every command: [JSON_OUTPUT.md](JSON_OUTPUT.md).
