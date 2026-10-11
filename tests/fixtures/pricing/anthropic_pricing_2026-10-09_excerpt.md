---
title: Pricing
url: https://platform.claude.com/docs/en/about-claude/pricing
description: Learn about Anthropic's pricing structure for models and features
---

This page provides detailed pricing information for Anthropic's models and features. All prices are in USD.

For the most current pricing information, visit [claude.com/pricing](https://claude.com/pricing).

## Model pricing

The following table shows pricing for all Claude models:

| Model                                                                                                                                | Base input tokens     | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens          |
| :----------------------------------------------------------------------------------------------------------------------------------- | :-------------------- | :-------------- | :-------------- | :----------------------- | :--------------------- |
| Claude Fable 5.1                                                                                                                     | $10 / MTok            | $12.50 / MTok   | $20 / MTok      | $0.25 / MTok<sup>1</sup> | $50 / MTok             |
| Claude Mythos 5.1 ([limited availability](https://support.claude.com/en/articles/14604842))                                          | $10 / MTok            | $12.50 / MTok   | $20 / MTok      | $0.25 / MTok<sup>1</sup> | $50 / MTok             |
| Claude Fable 5                                                                                                                       | $10 / MTok            | $12.50 / MTok   | $20 / MTok      | $1 / MTok                | $50 / MTok             |
| Claude Mythos 5 ([limited availability](https://support.claude.com/en/articles/14604842))                                            | $10 / MTok            | $12.50 / MTok   | $20 / MTok      | $1 / MTok                | $50 / MTok             |
| Claude Opus 5.5                                                                                                                      | $4 / MTok             | $5 / MTok       | $8 / MTok       | $0.20 / MTok<sup>2</sup> | $20 / MTok             |
| Claude Opus 5                                                                                                                        | $5 / MTok             | $6.25 / MTok    | $10 / MTok      | $0.50 / MTok             | $25 / MTok             |
| Claude Opus 4.8                                                                                                                      | $5 / MTok             | $6.25 / MTok    | $10 / MTok      | $0.50 / MTok             | $25 / MTok             |
| Claude Opus 4.7                                                                                                                      | $5 / MTok             | $6.25 / MTok    | $10 / MTok      | $0.50 / MTok             | $25 / MTok             |
| Claude Opus 4.6                                                                                                                      | $5 / MTok             | $6.25 / MTok    | $10 / MTok      | $0.50 / MTok             | $25 / MTok             |
| Claude Opus 4.5                                                                                                                      | $5 / MTok             | $6.25 / MTok    | $10 / MTok      | $0.50 / MTok             | $25 / MTok             |
| Claude Opus 4.1 ([retired, except on Bedrock and Google Cloud](https://platform.claude.com/docs/en/about-claude/model-deprecations)) | $15 / MTok            | $18.75 / MTok   | $30 / MTok      | $1.50 / MTok             | $75 / MTok             |
| Claude Opus 4 ([retired, except on Google Cloud](https://platform.claude.com/docs/en/about-claude/model-deprecations))               | $15 / MTok            | $18.75 / MTok   | $30 / MTok      | $1.50 / MTok             | $75 / MTok             |
| Claude Sonnet 5.5                                                                                                                    | $2 / MTok             | $2.50 / MTok    | $4 / MTok       | $0.10 / MTok<sup>2</sup> | $10 / MTok             |
| Claude Sonnet 5                                                                                                                      | $2 / MTok<sup>3</sup> | $2.50 / MTok    | $4 / MTok       | $0.20 / MTok             | $10 / MTok<sup>3</sup> |
| Claude Sonnet 4.6                                                                                                                    | $3 / MTok             | $3.75 / MTok    | $6 / MTok       | $0.30 / MTok             | $15 / MTok             |
| Claude Sonnet 4.5                                                                                                                    | $3 / MTok             | $3.75 / MTok    | $6 / MTok       | $0.30 / MTok             | $15 / MTok             |
| Claude Sonnet 4 ([retired, except on Bedrock and Google Cloud](https://platform.claude.com/docs/en/about-claude/model-deprecations)) | $3 / MTok             | $3.75 / MTok    | $6 / MTok       | $0.30 / MTok             | $15 / MTok             |
| Claude Haiku 5.5 (for prompts up to 100,000 tokens)                                                                                  | $0.10 / MTok          | $0.125 / MTok   | $0.20 / MTok    | $0.01 / MTok             | $0.50 / MTok           |
| Claude Haiku 5.5 (for prompts over 100,000 tokens)                                                                                   | $0.50 / MTok          | $0.625 / MTok   | $1 / MTok       | $0.05 / MTok             | $2.50 / MTok           |
| Claude Haiku 4.5                                                                                                                     | $1 / MTok             | $1.25 / MTok    | $2 / MTok       | $0.10 / MTok             | $5 / MTok              |
| Claude Haiku 3.5 ([retired, except on Google Cloud](https://platform.claude.com/docs/en/about-claude/model-deprecations))            | $0.80 / MTok          | $1 / MTok       | $1.60 / MTok    | $0.08 / MTok             | $4 / MTok              |

*<sup>1 Cache hits and refreshes on Claude Fable 5.1 and Claude Mythos 5.1 are priced at 0.025x the base input price.</sup>*

*<sup>2 Cache hits and refreshes on Claude Opus 5.5 and Claude Sonnet 5.5 are priced at 0.05x the base input price.</sup>*

*<sup>All other models use the standard 0.1x multiplier.</sup>*

*<sup>3 The $2/$10 per million input/output token pricing for Claude Sonnet 5, announced at launch as introductory pricing through August 31, 2026, is now the standard price. The previously scheduled increase to $3/$15 per million input/output tokens on September 1, 2026 will not occur.</sup>*

* **MTok:** Million tokens. $5 / MTok is $5 for every million tokens.
* **5m cache writes:** Writing a prompt prefix to the 5-minute prompt cache.
* **1h cache writes:** Writing a prompt prefix to the 1-hour prompt cache.
* **Cache hits and refreshes:** Reading a prompt prefix from the prompt cache, which also refreshes it.
* **Limited access:** Available only to organizations verified through Anthropic’s verification programs, such as the [Cyber Verification Program](https://support.claude.com/en/articles/14604842). To request access, apply to the program that covers your use case.
* **Retired:** May still be available on other cloud platforms. See [Model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations) for more.

Claude 4.7 and later models and Claude Mythos Preview use a newer tokenizer that contributes to their improved performance on a wide range of tasks. This tokenizer produces approximately 30% more tokens for the same text. The exact increase depends on the content and workload shape. Claude Sonnet 4.6 and earlier models use the previous tokenizer.

<!-- Appended verbatim from the saved official page (sha256 67904e280b595d31f373c857844ed380072ae79d280d8f385640b5992d337ca9,
     retrieved 2026-10-10): the sections check_price_table_vs_vendor.py reads for web
     search, fast mode and data residency. -->

### Data residency pricing

For Claude 4.6 and later models, specifying US-only inference through the `inference_geo` parameter incurs a 1.1x multiplier on all token pricing categories, including input tokens, output tokens, cache writes, and cache reads. On Claude Haiku 5.5, the multiplier also applies to the higher prices for prompts over 100,000 tokens (see [Long context pricing](https://platform.claude.com/docs/en/about-claude/pricing#long-context-pricing)). Global routing (the default) uses standard pricing.

This applies to the Claude API (first-party) and Claude Platform on AWS. On Claude in Microsoft Foundry, the same 1.1x multiplier applies to deployments that use the US Data Zone Standard deployment type (see [Inference geography](https://platform.claude.com/docs/en/about-claude/pricing#foundry-inference-geography)). Partner-operated platforms (Bedrock and Google Cloud) have independent regional pricing. See [Bedrock](https://aws.amazon.com/bedrock/pricing/) and [Google Cloud](https://cloud.google.com/vertex-ai/generative-ai/pricing#claude-models) for details. Earlier models do not support the `inference_geo` parameter and always use standard pricing; requests that include the parameter on these models return a 400 error.

For more information, see [Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency).

### Fast mode pricing

[Fast mode](https://platform.claude.com/docs/en/build-with-claude/fast-mode), in research preview, provides significantly faster output for Claude Opus 5.5, Claude Opus 5, and Claude Opus 4.8 at premium pricing. Fast mode pricing applies across the full context window, including requests over 200k input tokens. Fast mode is available on the Claude API (first-party) only; it is not available on Claude Platform on AWS or partner-operated cloud platforms.

| Model                           | Input      | Output     |
| ------------------------------- | ---------- | ---------- |
| Claude Opus 5.5                 | $8 / MTok  | $40 / MTok |
| Claude Opus 5 / Claude Opus 4.8 | $10 / MTok | $50 / MTok |

Fast mode is not available on Claude Opus 4.7 (requests with `speed: "fast"` return an error) or Claude Opus 4.6 (requests run at standard speed and are billed at standard rates). See [Fast mode](https://platform.claude.com/docs/en/build-with-claude/fast-mode#supported-models).

Fast mode pricing stacks with other pricing modifiers:

* [Prompt caching multipliers](https://platform.claude.com/docs/en/about-claude/pricing#prompt-caching) apply on top of fast mode pricing
* [Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency) multipliers apply on top of fast mode pricing

Fast mode is not available with the [Batch API](https://platform.claude.com/docs/en/about-claude/pricing#batch-processing).

For more information, see [Fast mode](https://platform.claude.com/docs/en/build-with-claude/fast-mode).


#### Web search tool

Web search usage is charged in addition to token usage:

```json
{
  "usage": {
    "input_tokens": 105,
    "output_tokens": 6039,
    "cache_read_input_tokens": 7123,
    "cache_creation_input_tokens": 7345,
    "server_tool_use": {
      "web_search_requests": 1
    }
  }
}
```

Web search is available on the Claude API for **$10 per 1,000 searches**, plus standard token costs for search-generated content. Web search results retrieved throughout a conversation are counted as input tokens, in search iterations executed during a single turn and in subsequent conversation turns.

Each web search counts as one use, regardless of the number of results returned. If an error occurs during web search, the web search will not be billed.
