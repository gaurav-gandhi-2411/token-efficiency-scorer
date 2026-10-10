# Exit codes

Every `tracegauge` / `tes` command exits with one of these codes. They are a contract for scripts,
Claude Code hooks and CI: new codes may be added, existing ones are never renumbered. The same
table is in each command's `--help` epilog. The constants live in `tes/exit_codes.py`.

| Code | Meaning | Commands |
|---:|---|---|
| 0 | OK. Includes "scored, nothing notable", "no alarm", and `monitor` with no active session. | all |
| 1 | Bad usage or nothing to do: path not found, no `.jsonl` files / no sessions found, contradictory flags, a bad `--since`, or the store (`tes.db`) cannot be opened. | all |
| 2 | Command-line usage error (unknown flag, missing argument). Reported by argparse; we never use 2 ourselves. | all |
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
  would make `tes monitor && ...` chains fail on every idle run. There is no `--fail-on-alarm` flag:
  the alarm already is the failure condition, and it is itself data-gated (it stays silent until
  your own baseline for that task type is built).
* **Store errors** (`cost`, `budget`, `impact`: cannot open `~/.tes/tes.db`) used to print
  `[ERROR]` and exit 0. They now exit 1.
* Success paths are unchanged: every command that exited 0 on success still does.

## Examples

```bash
# CI step: score everything, tolerate unparseable files but say so
tracegauge score ./transcripts --no-judge --json > scores.json
case $? in 0) ;; 4) echo "some transcripts did not parse" ;; *) exit 1 ;; esac

# act on the alarm in a hook or cron job
tracegauge monitor; [ $? -eq 3 ] && notify-send "tracegauge: session alarm"
```
