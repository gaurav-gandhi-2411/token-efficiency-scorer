# Pricing fields: what Claude Code transcripts carry vs what tracegauge prices

Every key path under `message.usage` of an assistant record, mapped to the dimension on Anthropic's
pricing page, whether `tracegauge.cost` prices it, and the evidence. The machine-readable twin is
`config/usage_field_rules.json`; `scripts/check_usage_fields_priced.py` fails when a transcript (or the
committed fixture `tests/fixtures/usage_fields/`) carries a key path or a value that this registry does
not list (see "The gate" below).

**Source of the rules.** `https://platform.claude.com/docs/en/about-claude/pricing.md`, retrieved
2026-10-10T11:34:51Z (HTTP 200, 50,272 bytes), sha256
`67904e280b595d31f373c857844ed380072ae79d280d8f385640b5992d337ca9`. The table in `tracegauge/data/prices.json`
(token rates, cache columns, fast-mode rates, the `inference_geo` multiplier and the web-search price) is
verified against that page by `scripts/check_price_table_vs_vendor.py`; the rules below describe the same
page. Page sections used: "Model pricing", "Prompt caching", "Data residency pricing", "Fast mode pricing",
"Batch processing" and "Web search tool".

**Evidence population.** The maintainer's own 50-session set: 50 main sessions; 32,761 main-chain and 82,425
subagent assistant records with a `usage` object; 571 subagent files. Counts below are over those records,
measured by a read-only, content-free scan (key paths and counts only). **NOT MEASURED over every transcript
on the machine**: that census was not run; run `python scripts/check_usage_fields_priced.py` (default:
`~/.claude/projects`) to extend it, the command prints only key paths, counts and file paths.

**Dollar effect of the three newly priced dimensions on that set: $0.** In the maintainer's 50-session set, 0
records carried any of them (0 non-zero `web_search_requests`; `speed` was `standard`, null or absent on every
record; `inference_geo` was `not_available` or null on every record). They are priced so that the first
record that does carry one is billed correctly, not because they moved a total.

| usage key path | records (main / sub) | non-zero (main / sub) | pricing page rule | implemented? | evidence |
|---|---|---|---|---|---|
| `input_tokens` | 32,761 / 82,425 | 32,692 / 82,385 | base input rate | yes | `tracegauge.cost.compute_turn_cost` |
| `output_tokens` | 32,761 / 82,425 | 32,692 / 82,385 | output rate (thinking tokens are output) | yes | same |
| `cache_read_input_tokens` | 32,761 / 82,425 | 32,692 / 81,517 | input x 0.1 (0.05 Opus/Sonnet 5.5, 0.025 Fable/Mythos 5.1) | yes (per-model `cache_read_multiplier`) | `tests/test_cost_math.py`, `prices.json` |
| `cache_creation_input_tokens` | 32,761 / 82,425 | 32,683 / 82,378 | = 5m + 1h writes | yes (as the sum of the two rows below) | 5m + 1h == total on 57,651 of 57,651 deduped messages with the breakdown (100.0%) |
| `cache_creation.ephemeral_5m_input_tokens` | 32,761 / 82,425 | 0 / 82,370 | 5-minute write: 1.25x input | yes | `tests/test_cost_cache_tiers.py` |
| `cache_creation.ephemeral_1h_input_tokens` | 32,761 / 82,425 | 32,683 / 8 | 1-hour write: **2x input** | yes | 57,052,751 deduped 1h write tokens in the set (56,811,511 main, 241,240 subagent) |
| `server_tool_use.web_search_requests` | 32,761 / 40,814 | 0 / 0 | $10 per 1,000 searches, on top of tokens | **yes** (`server_tool_rates.web_search_usd_per_1000`, added to the turn's `total_usd`) | page, "Web search tool"; `tests/test_cost_billing_modifiers.py` |
| `server_tool_use.web_fetch_requests` | 32,761 / 40,814 | 0 / 0 | no additional charge | n/a (free) | page, "Web fetch tool" |
| `server_tool_use.code_execution_requests` | not present | - | container time ($0.05/h beyond 1,550 free h; free with web search/fetch) | no (warned if it appears) | page, "Code execution tool"; time is not in the transcript |
| `speed` | 32,761 / 40,814 | 'standard' on 32,692 / 40,774; null 69 / 40; **absent on 41,611 subagent records** | fast mode: premium rates on Opus 5.5 / 5 / 4.8 | **yes** (`fast_*_usd_per_mtok`) | page, "Fast mode pricing"; no record in the set had `fast` |
| `inference_geo` | 32,761 / 82,425 | 'not_available' 32,692 / 82,385; null 69 / 40 | `us` = 1.1x on Claude 4.6 and later | **yes** (`geo_us_multiplier`) | page, "Data residency pricing"; no record in the set had `us` |
| `service_tier` | 32,761 / 82,425 | 'standard' 32,692 / 82,385; null 69 / 40 | no price on the page for any tier | ignored; non-`standard` fails the gate | page has no `service_tier`/priority entry |
| `iterations[]` (+ `.type`, token fields, `.cache_creation.*`) | 32,761 / 40,814 | list on 32,691 main records | none: a per-iteration breakdown of the same usage | ignored (pricing both would double count) | sum of iterations == top-level usage on 32,691 of 32,691 main records checked (input, output, cache read, cache creation) |
| `output_tokens_details.thinking_tokens` | 32,692 / 40,774 | 26,182 / 25,751 | none: already inside `output_tokens` | ignored | thinking <= output on 32,692 of 32,692 records |
| `fallback_credit` | 13,434 / 16,764 | all null | not on the page | ignored while null; a non-null value fails the gate | 0 non-null |

## What is priced for the three dimensions, and the decisions behind it

Rates live in `tracegauge/data/prices.json`; the formula is `tracegauge.cost.compute_turn_cost`; `COST_VERSION` is 3.

**Web search.** `usage.server_tool_use.web_search_requests` x $10 / 1,000 = $0.01 per search, added to the turn
on top of the token cost. The page: "Web search is available on the Claude API for $10 per 1,000 searches, plus
standard token costs"; "If an error occurs during web search, the web search will not be billed." A transcript
does not say which searches errored, so **every recorded search is charged**: when a search errored the real bill
is lower than the figure shown (a ceiling for that part). The charge is flat, so neither fast mode nor the geo
multiplier scales it. A price table with no `server_tool_rates` entry (an older override) still warns instead of
silently leaving searches out. Web fetch is free (no warning). Code execution stays unmodelled: it is billed by
container time, which a transcript does not carry, so it keeps its `server_tool_warning`.

**Fast mode.** `usage.speed == "fast"` replaces the input/output rates of the whole request with the model's
`fast_input_usd_per_mtok` / `fast_output_usd_per_mtok`: Opus 5.5 $8 / $40, Opus 5 and Opus 4.8 $10 / $50 per
MTok, across the full context window. The prompt-caching multipliers and the geo multiplier stack on top of the
fast rates: a cache read is the fast input rate x that model's `cache_read_multiplier` (0.05 on Opus 5.5, 0.1 on
Opus 5 and 4.8), a 5-minute write 1.25x and a 1-hour write 2x the fast input rate. Only those three models carry
the fields. The page says fast mode "is not available on Claude Opus 4.7 (requests with `speed: "fast"` return an
error) or Claude Opus 4.6 (requests run at standard speed and are billed at standard rates)", so a record with
`speed == "fast"` on any model without fast rates is **priced at the standard rate and counted** in
`SessionCost.fast_unsupported_requests`; the cost note says so.

**US-only inference.** `usage.inference_geo == "us"` multiplies *every* token category (input, output, 5-minute
and 1-hour cache writes, cache reads, and Haiku 5.5's long-context rates) by 1.1 for "Claude 4.6 and later
models". Which models: each eligible model entry carries `geo_us_multiplier: 1.1`, an explicit list, not a
version comparison at run time. Decisions: eligible are Opus 4.6, 4.7, 4.8, 5, 5.5; Sonnet 4.6, 5, 5.5; Haiku
5.5; Fable 5, 5.1; Mythos 5, 5.1. **Not eligible** (priced standard, counted in
`geo_us_unsupported_requests`, and the page says earlier models reject the parameter): Opus 4.5, 4.1, 4;
Sonnet 4.5, 4; Haiku 4.5 and 3.5; every retired model. Haiku 4.5 and Sonnet 4.5 are 4.5, which is before 4.6, so
they are not eligible. `global`, `not_available`, null and an absent field are 1.0. The vendor check derives
eligibility from each model key's version and the page's "Claude X.Y and later", and fails if the table's list
disagrees.

**Batch does not apply to Claude Code transcripts.** No usage field carries it, Claude Code does not use the
Batch API, and the page says fast mode is not available with Batch, so the 0.5x discount is never part of a
Claude Code bill. It is stated that way in `tracegauge.cost.UNMODELLED_BILLING` ("does not apply to Claude Code
transcripts") rather than listed as a gap. Managed-Agents session runtime and the Bedrock/Vertex regional prices
are likewise not derivable from a Claude Code transcript.

### Hand-computed examples (the tests check these)

All use the 1,000,000 fresh / 1,000,000 written (400,000 of them 1-hour) / 2,000,000 read / 100,000 output token
turn of `tests/test_cost_billing_modifiers.py`, in USD.

| case | arithmetic | total |
|---|---|---|
| Opus 5.5, standard | 4.00 + read 2M x 0.20 = 0.40 + writes (0.6M x 5 + 0.4M x 8 = 6.20) + out 0.1M x 20 = 2.00 | 12.60 |
| Opus 5.5, fast | 8.00 + read 2M x (8 x 0.05) = 0.80 + writes (0.6M x 10 + 0.4M x 16 = 12.40) + out 0.1M x 40 = 4.00 | 25.20 |
| Opus 5.5, fast + `us` | 25.20 x 1.1 | 27.72 |
| Opus 4.8, `us` | (5.00 + 1.00 + 7.75 + 2.50 = 16.25) x 1.1 | 17.875 |
| Opus 4.8, fast + `us` | (10.00 + 2.00 + 15.50 + 5.00 = 32.50) x 1.1 | 35.75 |
| Sonnet 4.6, 20 searches, 1,000 in / 100 out | 0.003 + 0.0015 + 20 x 0.01 | 0.2045 |
| Opus 4.6, `fast` | not a fast model: 1M x 5 + 0.1M x 25 | 7.50 (standard, counted) |
| Haiku 4.5, `us` | not eligible: 1M x 1 + 0.1M x 5 | 1.50 (standard, counted) |

## How speed and geo travel through the adapter

Fast mode and the US-only multiplier scale a whole API request, so they are carried per request.
`usage` is de-duplicated per `message.id` (Claude Code writes one assistant record per content block, each
repeating the response's `usage`); `speed` and `inference_geo` are properties of the response and identical on
every record of it, and the value of the usage-carrying record is taken. A main-chain turn is one request and
carries its own `TurnDigest.speed` ("fast" or "") and `TurnDigest.inference_geo` ("us" or ""); any other value
(`standard`, `global`, `not_available`, null, absent, or one the page does not know) is carried as "" and priced
standard.

Subagent usage is summed per subagent file so a long agent run is one digest rather than thousands. That sum is
now keyed by **(model, speed, inference_geo)**: requests that bill differently are never added into the same
turn, so no request is priced with another's multiplier. A subagent file with no fast and no US request yields
exactly the turns it always did (one per model); with them it yields one extra turn per distinct combination.
The aggregate already carried `request_count` and the per-request web-search counts add linearly, so nothing
else is lost. `tests/test_adapt_billing_modifiers.py` and the last test of `tests/test_cost_billing_modifiers.py`
check the split and its price (standard 0.024 + fast 0.048 + fast-and-US 0.0528 = 0.1248 for the subagent part).

## What the cost note now says

The `domain_of_validity` sentence of a session that used any of the three dimensions says how many searches were
added (and their dollars), how many requests were priced at fast rates, how many at 1.1x, and how many asked for
fast or `us` on a model without it and were priced standard. A session that used none carries no such sentence
and its dollar figures are unchanged. (`UNMODELLED_BILLING` was reworded, so the `domain_of_validity` text of
every session changed; its numbers did not.) Persisted rows carry `cost_version`; it moved from 2 to 3 because
the formula changed for rows that carry these fields.

### Cache writes

Cache writes are split by `usage.cache_creation`: 1-hour writes at 2x input, 5-minute at 1.25x. A record
without the breakdown (an older Claude Code, or a hand-built fixture) has its writes counted as **unspecified
tier and priced at 5 minutes, the conservative default** (a floor). `TurnDigest.cache_creation_tier_known`
carries how many write tokens the breakdown covered, `SessionCost.cache_write_unspecified_tokens` how many were
assumed, and the `domain_of_validity` sentence says "assumed 5-minute (N of M cache-write tokens in this
session)". In the maintainer's set that number is 0 of 269,434,674 write tokens (every deduped message carried
the breakdown).

### Long context

Only Haiku 5.5 is tiered (prompts over 100,000 tokens, counting input + cache reads + cache writes, bill at the
higher rates; each request is priced alone). A main-chain turn is exactly one request, so its tier is exact. A
subagent turn aggregates many requests per (file, model, speed, geo); when the summed prompt exceeds the
threshold the per-request size is not recoverable and the turn is priced at the base tier with a
`pricing_caveat` ("this part of the total is a floor"). In the maintainer's set, and in all 81 main sessions on
the maintainer's disk when this was measured, the number of requests over the Haiku threshold is 0, so this floor
has a $0 effect today. The US-only multiplier applies to the long-context rates too. Fast mode applies across the
full context window and no fast model is tiered.

## The gate

`scripts/check_usage_fields_priced.py` flattens every `message.usage` object (lists as `[]`) and exits 1 when
a key path is not in `config/usage_field_rules.json`, or when a guarded field takes a value outside the registry's
allowed set: `speed` in {`standard`, `fast`}, `inference_geo` in {`not_available`, `global`, `us`},
`service_tier` = `standard`, `fallback_credit` null. A made-up `speed` such as `turbo`, another region, or a
non-standard tier fails because `tracegauge.cost` would price it as standard without knowing. Exit 2 means nothing
could be scanned ("could not check" is never a pass). CI runs it over the committed fixture
(`tests/test_usage_fields_priced.py`); the real-transcript scan is a pre-release step (RELEASING.md).
When it fails: price the field in `tracegauge/cost.py`, or register it as `free` / `ignored` with a reason, and add the
row above in the same change.
