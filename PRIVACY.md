# Privacy

tracegauge is local by default. The watcher, scorer, and dashboard run on your
machine. No data is transmitted anywhere — tracegauge has no server.

## Contribution export (optional, off by default)

`tracegauge export-contribution` writes a redacted, content-free summary of your
scored sessions to a local file (`~/.tes/contribution-<date>.jsonl` by default).
You inspect the file before deciding what to do with it. Nothing is transmitted —
the command writes a local file only, and tracegauge never reads it back.

The export exists so that, in the future, contributors could optionally help build
broader calibration baselines — improving tracegauge for new users who otherwise
start cold. That future program, and any transmission, does not exist yet and would
require separate explicit consent and legal review.

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

### Safety: allow-list by construction

Each row is built field-by-field from the 14 fields above. No session object is
serialized and filtered — a new field added to the store in a future release
cannot appear in the export by construction.

### `contributor_id`

A random UUID generated on first export, stored in `~/.tes/contributor_id.txt`.
Not derived from hostname, username, file paths, or any identifying information.
Regenerate by deleting the file. Omit entirely with `--anonymous`.

### Transmission

**Nothing is transmitted in this version.** `tracegauge export-contribution`
writes a local file. There is no upload, no server endpoint, no daemon that
would pick up the file. Any future transmission step would be separate,
explicitly opt-in, with a new consent flow and independent legal review first.

---

## API judge (opt-in, explicit consent per session)

`tes score <path> --api-judge` scores trajectory quality using an API-hosted model
(default: `claude-haiku-4-5-20251001`) with your own API key. **This is the one
feature in tracegauge that sends data off-machine.**

This is distinct from the contribution export: the API judge sends raw session
content (snippets) to a third-party model provider; the contribution export is
content-free (numeric counts only) and is never transmitted by tracegauge.

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
