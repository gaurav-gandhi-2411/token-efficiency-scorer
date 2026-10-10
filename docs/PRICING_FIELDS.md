# Pricing fields: what Claude Code transcripts carry vs what tracegauge prices

Every key path under `message.usage` of an assistant record, mapped to the dimension on Anthropic's
pricing page, whether `tes.cost` prices it, and the evidence. The machine-readable twin is
`config/usage_field_rules.json`; `scripts/check_usage_fields_priced.py` fails when a transcript (or the
committed fixture `tests/fixtures/usage_fields/`) carries a key path or a bill-changing value that this
registry does not list (see "The gate" below).

**Source of the rules.** `https://platform.claude.com/docs/en/about-claude/pricing.md`, retrieved
2026-10-10T11:34:51Z (HTTP 200, 50,272 bytes), sha256
`67904e280b595d31f373c857844ed380072ae79d280d8f385640b5992d337ca9`. A re-fetch on that date was
byte-identical to the copy saved earlier in the same Wave (`R0-raw/pricing.md`), so the table in
`tes/data/prices.json` (verified against the page by `scripts/check_price_table_vs_vendor.py`) and the rules
below describe the same page.

**Evidence population.** The frozen Phase 0 "set B" (50 main sessions; 32,761 main-chain and 82,425 subagent
assistant records with a `usage` object; 571 subagent files). Counts below are over those records, measured
by a read-only, content-free scan (key paths and counts only). **NOT MEASURED over every transcript on the
machine**: that census was not run (the environment declined a whole-disk scan); run
`python scripts/check_usage_fields_priced.py` (default: `~/.claude/projects`) to extend it, the command
prints only key paths, counts and file paths.

| usage key path | records (main / sub) | non-zero (main / sub) | pricing page rule | implemented? | evidence |
|---|---|---|---|---|---|
| `input_tokens` | 32,761 / 82,425 | 32,692 / 82,385 | base input rate | yes | `tes.cost.compute_turn_cost` |
| `output_tokens` | 32,761 / 82,425 | 32,692 / 82,385 | output rate (thinking tokens are output) | yes | same |
| `cache_read_input_tokens` | 32,761 / 82,425 | 32,692 / 81,517 | input x 0.1 (0.05 Opus/Sonnet 5.5, 0.025 Fable/Mythos 5.1) | yes (per-model `cache_read_multiplier`) | `tests/test_cost_math.py`, `prices.json` |
| `cache_creation_input_tokens` | 32,761 / 82,425 | 32,683 / 82,378 | = 5m + 1h writes | yes (as the sum of the two rows below) | 5m + 1h == total on 57,651 of 57,651 deduped messages with the breakdown (100.0%) |
| `cache_creation.ephemeral_5m_input_tokens` | 32,761 / 82,425 | 0 / 82,370 | 5-minute write: 1.25x input | yes | `tests/test_cost_cache_tiers.py` |
| `cache_creation.ephemeral_1h_input_tokens` | 32,761 / 82,425 | 32,683 / 8 | 1-hour write: **2x input** | **yes (this change)**; before it every write was priced at 1.25x | 57,052,751 deduped 1h write tokens in set B (56,811,511 main, 241,240 subagent) |
| `server_tool_use.web_search_requests` | 32,761 / 40,814 | 0 / 0 | $10 per 1,000 searches | **no** (detected, `server_tool_warning`, excluded from `total_usd`) | 0 non-zero records, so the dollar effect on set B is $0; pricing it is a follow-up for when one appears |
| `server_tool_use.web_fetch_requests` | 32,761 / 40,814 | 0 / 0 | no additional charge | n/a (free) | page, "Web fetch tool" |
| `server_tool_use.code_execution_requests` | not present | - | container time ($0.05/h beyond 1,550 free h; free with web search/fetch) | no (warned if it appears) | page, "Code execution tool"; time is not in the transcript |
| `speed` | 32,761 / 40,814 | 'standard' on 32,692 / 40,774; null 69 / 40; **absent on 41,611 subagent records** | fast mode: premium rate (Opus 5.5 $8/$40) | **no**: only `standard` seen, so nothing to price; any other value fails the gate | `unknown` indicator count = 109 null + 41,611 absent records |
| `inference_geo` | 32,761 / 82,425 | 'not_available' 32,692 / 82,385; null 69 / 40 | `us` = 1.1x on 4.6+ models | **no**: only `not_available` seen; `us` fails the gate | - |
| `service_tier` | 32,761 / 82,425 | 'standard' 32,692 / 82,385; null 69 / 40 | no price on the page for any tier | ignored; non-`standard` fails the gate | page has no `service_tier`/priority entry |
| `iterations[]` (+ `.type`, token fields, `.cache_creation.*`) | 32,761 / 40,814 | list on 32,691 main records | none: a per-iteration breakdown of the same usage | ignored (pricing both would double count) | sum of iterations == top-level usage on 32,691 of 32,691 main records checked (input, output, cache read, cache creation) |
| `output_tokens_details.thinking_tokens` | 32,692 / 40,774 | 26,182 / 25,751 | none: already inside `output_tokens` | ignored | thinking <= output on 32,692 of 32,692 records |
| `fallback_credit` | 13,434 / 16,764 | all null | not on the page | ignored while null; a non-null value fails the gate | 0 non-null |

Batch (0.5x), Managed-Agents session runtime and the Bedrock/Vertex regional prices are not derivable from a
Claude Code transcript (no usage field carries them) and are listed as unmodelled in
`tes.cost.UNMODELLED_BILLING`.

## What the cost note now says about cache writes

Cache writes are split by `usage.cache_creation`: 1-hour writes at 2x input, 5-minute at 1.25x. A record
without the breakdown (an older Claude Code, or a hand-built fixture) has its writes counted as **unspecified
tier and priced at 5 minutes, the conservative default** (a floor). `TurnDigest.cache_creation_tier_known`
carries how many write tokens the breakdown covered, `SessionCost.cache_write_unspecified_tokens` how many were
assumed, and the `domain_of_validity` sentence says "assumed 5-minute (N of M cache-write tokens in this
session)". In set B that number is 0 of 269,434,674 write tokens (every deduped message carried the breakdown).

## Long context

Only Haiku 5.5 is tiered (prompts over 100,000 tokens, counting input + cache reads + cache writes, bill at the
higher rates; each request is priced alone). A main-chain turn is exactly one request, so its tier is exact. A
subagent turn aggregates many requests per (file, model); when the summed prompt exceeds the threshold the
per-request size is not recoverable and the turn is priced at the base tier with a `pricing_caveat` ("this part
of the total is a floor"). In set B and in all 81 main sessions on disk the number of requests over the Haiku
threshold is 0 (`lc_requests` in `w1a/m/subagent_share_per_session.json`), so this floor has a $0 effect today.

## The gate

`scripts/check_usage_fields_priced.py` flattens every `message.usage` object (lists as `[]`) and exits 1 when
a key path is not in `config/usage_field_rules.json`, or when a guarded field (`speed`, `inference_geo`,
`service_tier`, `fallback_credit`) takes a value outside the registry's allowed set. Exit 2 means nothing
could be scanned ("could not check" is never a pass). CI runs it over the committed fixture
(`tests/test_usage_fields_priced.py`); the real-transcript scan is a pre-release step (RELEASING.md).
When it fails: price the field in `tes/cost.py`, or register it as `free` / `ignored` with a reason, and add the
row above in the same change.
