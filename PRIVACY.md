# Privacy

tracegauge is local by default. The watcher, scorer, and dashboard run on your
machine. **Nothing is transmitted anywhere by default.** tracegauge includes
the *code* for an opt-in community corpus contribution (`tes corpus
contribute`, described below), but that capability is **not active** — no
public tracegauge corpus is currently operated, and the command has nowhere
configured to send anything. Scoring, the dashboard, `tes patterns`, and local
`tes ask` stay on-device with no server involved, unconditionally.

## Contribution export (optional, off by default)

`tracegauge export-contribution` writes a redacted, content-free summary of your
scored sessions to a local file (`~/.tes/contribution-<date>.jsonl` by default).
You inspect the file before deciding what to do with it. This command itself
still only ever writes a local file — it never transmits anything, and
tracegauge never reads the file back.

The export exists so contributors can inspect exactly what a contribution would
contain before opting in to the separate `tes corpus contribute` command
(below), which is the ONLY tracegauge command that actually sends this data
anywhere.

### Fields included (14)

| Field | Type | Notes |
|---|---|---|
| `task_type` | categorical | one of 5 known types; any other value → `"other"` |
| `real_tokens` | integer | net token count; cache-read inflation removed |
| `token_count_input` | integer or null | session-level sum; null when source file inaccessible |
| `token_count_output` | integer or null | session-level sum; null when source file inaccessible |
| `cache_creation` | integer or null | session-level sum; null when source file inaccessible |
| `cache_read` | integer or null | session-level sum; null when source file inaccessible |
| `waste_event_count` | integer | count of detected waste events |
| `waste_detectors_fired` | list of strings | detector names only; clamped to known set |
| `model` | categorical | allow-listed; unrecognized strings → `"other"` |
| `turn_count` | integer or null | AI + substantive user turns |
| `week_bucket` | string | ISO year-week, e.g. `"2026-W24"` — not a precise timestamp |
| `tracegauge_version` | string | version provenance |
| `schema_version` | string | payload schema version |
| `contributor_id` | string or null | random opaque per-install UUID; omit with `--anonymous` |

### Fields never included

`session_id`, `source_path`, prompts, code, error text, evidence snippets,
proof-turn content, `judge_reasoning`, `interpretation`, precise timestamps,
any free-text field.

### Safety: allow-list by construction, re-checked again at send time

Each row is built field-by-field from the 14 fields above. No session object is
serialized and filtered — a new field added to the store in a future release
cannot appear in the export by construction.

When you run `tes corpus contribute` (below), the exact bytes about to be sent
are checked a second time, independently of how they were built, immediately
before the network call:

1. **Key check.** Every row's keys must be exactly the 14 fields above — an
   extra or missing key aborts the send.
2. **Value check.** Every value is checked against what's actually legitimate
   for its field: `task_type` and `model` against their known-value lists (or
   `"other"`), `waste_detectors_fired` items against the known detector-name
   list, `week_bucket`/`schema_version`/`tracegauge_version`/`contributor_id`
   against format patterns, and the numeric fields (`real_tokens`,
   `turn_count`, the token/cache counts) against being an actual number, not a
   string. Any other string value is capped at 30 characters — longer than
   any legitimate field needs, so anything that long is rejected rather than
   sent. (This is an allow-list-and-length check, not a scan for
   code/path-shaped patterns — it doesn't try to recognize what a leaked
   secret or file path looks like; it simply refuses anything that isn't
   already the value type a legitimate field expects.)

If either check fails, the send is aborted before any network call is made, a
local-only record of what tripped it is written to
`~/.tes/contribution_blocked.log` (so you can see why), and nothing is sent.

### `contributor_id`

A random UUID generated on first export, stored in `~/.tes/contributor_id.txt`.
Not derived from hostname, username, file paths, or any identifying information.
Regenerate with `tes corpus reset-id` (or by deleting the file) — prior rows
under the old ID become unlinked from future contributions. Omit entirely with
`--anonymous`.

## Community corpus contribution (opt-in capability — NOT currently active)

> **This capability is dormant.** `tes corpus contribute` is fully built and
> tested, but tracegauge does not currently operate a public corpus, and no
> `TES_CORPUS_URL` / `TES_CORPUS_ANON_KEY` / `TES_CORPUS_WITHDRAW_URL` are
> configured in any released build. Running `tes corpus contribute` today —
> even if you type `y` at the consent prompt — prints `[NOT SENT] the
> community corpus is not configured on this install` and makes no network
> call. This section describes what the feature would do *if and when* a
> corpus is provisioned and activated (tracked in `CURRENT_STATE.md` and
> `corpus/setup.md`) — not something currently happening to your data. If
> that ever changes, this section will say so, and the version that ships it
> will call it out prominently in its release notes.

`tes corpus contribute`, once activated, would be the only tracegauge command
that sends data off your machine without a per-call API key you provide
yourself. It would send the same content-free rows described above — never
the local export file itself, never anything beyond the 14 allow-listed
fields — to a database tracegauge operates, so that percentile baselines
could be computed across contributing developers and shown back to you
alongside (never replacing) your own local self-baseline.

**What would be sent.** Exactly the 14 fields listed above, and nothing else,
re-verified at send time as described above.

**What would never be sent.** `session_id`, `source_path`, prompts, code,
error text, evidence snippets, proof-turn content, `judge_reasoning`,
`interpretation`, precise timestamps, or any free-text field. In plain words:
numbers and categories only — no text, no code, no content from your
sessions.

**Where it would be stored.** A Postgres database hosted on
[Supabase](https://supabase.com) (a third-party cloud infrastructure
provider), in Supabase's **eu-west-1** region — chosen because it falls under
GDPR, the strictest data-protection regime tracegauge's contributors are
likely to be covered by, and is the most defensible default for an anonymous,
EU-compliant dataset regardless of where a contributor is located. As of this
writing, no such project has been provisioned.

**Who would be able to access it.** Supabase, as the infrastructure host, and
the tracegauge project, as the corpus operator, would have the same technical
access to stored rows that any hosted-database operator and application owner
have — this is inherent to using a hosted database, not something specific to
tracegauge. See [Supabase's own privacy
policy](https://supabase.com/privacy) for how Supabase itself handles hosted
data. Rows would be content-free aggregates and would never be individually
returned to other contributors or users — only pooled percentile statistics
would be published back (see "Community baseline" below).

**Consent.** Before anything would be sent, you would be shown: the exact row
that would be sent (built from your real data, not a schematic), the full
field list, the "what is never sent" list, the destination (named above),
what it's used for, your `contributor_id` (or that you're contributing
anonymously), and how to withdraw. You would have to type `y` to proceed.
Without that explicit confirmation, no network call is made — the gate is
unconditional in code, checked before anything else happens, the same pattern
already used for the API judge below. Today, even typing `y` produces no
network call at all, because there is nowhere configured to send to (see the
dormancy notice above).

**Retention.** Rows would be retained until withdrawn. There would be no
automatic expiration — deletion would be a contributor-initiated action
(below), not a time-based policy.

**Withdrawal / deletion.** `tes corpus withdraw` is built and would, once a
corpus exists, show what will be deleted, require confirmation, and delete
every row tied to your `contributor_id` via a server-side function that
validates the ID before deleting — your local client would have no ability to
read, modify, or delete any row directly (including your own) once sent;
deletion would only happen through that validated path. After a confirmed
deletion, tracegauge would also delete your local
`~/.tes/contributor_id.txt`, so a future contribution would start fresh under
a new, unlinked ID rather than silently reusing the one you just asked to be
forgotten.

> **If you ever contribute and later lose or delete `~/.tes/contributor_id.txt`
> yourself (outside of `tes corpus withdraw`), your past rows can no longer be
> linked back to you and cannot be withdrawn.** This file would be the only
> key tying your contributions together — treat losing it as losing the
> ability to withdraw them.

**Anonymity and its limits.** `contributor_id` is a random UUID generated
locally (Python's `uuid.uuid4()`, not derived from your hostname, username,
file paths, or any identifying information) — there is no email, account, or
authentication involved. That said, no anonymization is absolute: a
distinctive enough pattern across your aggregate statistics (an unusual
session cadence or token profile, for instance) would theoretically be a
basis for statistical re-identification, even though no individual field is
identifying. This is a general limitation of any aggregate-data contribution,
not something specific to tracegauge's implementation, and it's disclosed here
so you can weigh it before ever opting in.

**Community baseline.** Once contributions exist, rows would be periodically
pooled (offline, in batches — not live) into per-task-type percentile
statistics, published as a data file tracegauge fetches, the same way the
bundled self-baseline data ships today. Wherever a community percentile would
be shown, it would be shown alongside your self-baseline (never replacing it)
and would always carry its own domain-of-validity: how many contributing
developers it's drawn from, that those contributors are a self-selected group
(not a random sample of developers), and that the comparison is
content-free-coarse — it compares raw token counts only, without adjusting
for task complexity, model choice, or session goals the way your self-baseline
implicitly does by comparing you to yourself. Until a corpus is activated,
there is no community baseline to show, and none is shown.

---

## API judge (opt-in, explicit consent per session)

`tes score <path> --api-judge` scores trajectory quality using an API-hosted model
(default: `claude-haiku-4-5-20251001`) with your own API key. **This is the
one feature in tracegauge that sends raw session content off-machine.**

This is distinct from the community corpus contribution above: the API judge
sends raw session content (snippets) to a third-party model provider using
your own API key; the corpus contribution sends content-free numeric/
categorical aggregates only, to the tracegauge community corpus, using no key
of yours at all.

**What is sent.** A session digest containing 300-character conversation snippets,
task type, turn-level structure, and token counts. Sent from your machine directly
to the model provider's API (e.g., `api.anthropic.com`). Tracegauge servers are
not involved.

**Secrets.** Redacted at ingestion using the standard secret redactor (API keys,
tokens, private key headers, etc.). Other content — code, prompts, file paths —
is NOT filtered. The digest is not content-safe beyond secret removal.

**Consent.** A consent notice is shown before any data is sent, naming what will
be sent and explicitly stating that snippets may contain code or file content.
You must type `y` to proceed. Without explicit consent, no network call is made —
the gate is unconditional in code.

**Domain of validity.** The API judge uses the same validated v3 rubric as the
local judge. The API model was not part of the B3 cross-model corroboration
(Qwen3 + Gemma3) — the verdict is indicative, not equivalent to the validated
local judge.
