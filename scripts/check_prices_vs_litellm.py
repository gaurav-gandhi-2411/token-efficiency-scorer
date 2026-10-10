"""scripts/check_prices_vs_litellm.py — OPTIONAL, NON-FATAL cross-check of the price table
against LiteLLM's community price file.

The official Anthropic page (scripts/check_price_table_vs_vendor.py) is the source of truth and
is the only thing allowed to put a number in tes/data/prices.json. LiteLLM is a second,
independent pair of eyes: it tends to carry a new Claude model within hours, so a first-party
anthropic key it has that prices.json lacks is an early "go and read the official page" signal.
It mixes providers, lags on retired models and can be wrong, so every finding here is a
WARNING; the script exits 0 on findings (``--strict`` makes findings exit 1) and also on a
fetch failure (stated loudly, never as a pass).

Licensing: the file is MIT (BerriAI/litellm, everything outside enterprise/). It is fetched at
runtime from
https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json,
never vendored into this repo and never redistributed.

What it checks, for entries with ``litellm_provider == "anthropic"`` and a bare ``claude-*``
key (LiteLLM also lists bedrock/vertex/azure/aihubmix copies under other key shapes, some with
regional markups; those are not first-party prices): (1) the key (date suffix stripped) exists
in prices.json; (2) input, output, 5m cache write, 1h cache write and cache read agree with
the table's rates. Tier fields (``*_above_200k_tokens`` etc.) are not compared.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tes.cost import cache_read_multiplier, load_price_table  # noqa: E402

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
_BUNDLED_PRICES = _ROOT / "tes" / "data" / "prices.json"
_TIMEOUT_SECONDS = 30
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")
_EPS = 1e-9


def first_party_claude_entries(litellm: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Bare ``claude-*`` keys whose ``litellm_provider`` is ``anthropic``."""
    return {
        key: val
        for key, val in litellm.items()
        if isinstance(val, dict)
        and val.get("litellm_provider") == "anthropic"
        and key.startswith("claude-")
        and "/" not in key
    }


def crosscheck(prices: dict[str, Any], litellm: dict[str, Any]) -> list[str]:
    """Return one warning string per disagreement or missing first-party key."""
    models: dict[str, dict[str, Any]] = prices.get("models", {})
    mults = prices["cache_multipliers"]
    warnings: list[str] = []
    for lkey, val in sorted(first_party_claude_entries(litellm).items()):
        key = _DATE_SUFFIX_RE.sub("", lkey)
        entry = models.get(key)
        if entry is None:
            warnings.append(
                f"{lkey}: LiteLLM has a first-party anthropic entry but prices.json has no "
                f"'{key}' -- check the official pricing page"
            )
            continue
        in_rate = float(entry["input_usd_per_mtok"])
        expected = {
            "input_cost_per_token": in_rate,
            "output_cost_per_token": float(entry["output_usd_per_mtok"]),
            "cache_creation_input_token_cost": in_rate * float(mults["write_5min"]),
            "cache_creation_input_token_cost_above_1hr": in_rate * float(mults["write_1hr"]),
            "cache_read_input_token_cost": in_rate * cache_read_multiplier(entry, prices),
        }
        for field_name, ours in expected.items():
            raw = val.get(field_name)
            if not isinstance(raw, int | float):
                continue
            theirs = float(raw) * 1_000_000
            if abs(ours - theirs) > _EPS:
                warnings.append(
                    f"{lkey}: {field_name} ours=${ours:g} vs LiteLLM=${theirs:g} per MTok"
                )
    return warnings


def _fetch_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "tracegauge-litellm-crosscheck/1.0"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("LiteLLM price file is not a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Non-fatal LiteLLM price cross-check.")
    parser.add_argument("--litellm", type=Path, help="use a saved LiteLLM JSON instead of fetching")
    parser.add_argument("--strict", action="store_true", help="exit 1 when there are warnings")
    args = parser.parse_args(argv)
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"

    try:
        if args.litellm:
            litellm = json.loads(args.litellm.read_text(encoding="utf-8"))
        else:
            litellm = _fetch_json(LITELLM_URL)
        if not isinstance(litellm, dict) or not first_party_claude_entries(litellm):
            raise ValueError("no first-party anthropic claude-* entries found (format changed?)")
    except (OSError, ValueError, urllib.error.URLError) as e:
        msg = f"LiteLLM cross-check NOT RUN (this is not a pass): {e}"
        print(f"::warning::{msg}" if annotate else f"WARNING: {msg}", file=sys.stderr)
        return 1 if args.strict else 0

    warnings = crosscheck(load_price_table(_BUNDLED_PRICES), litellm)
    if not warnings:
        print(
            f"OK: {len(first_party_claude_entries(litellm))} LiteLLM first-party anthropic "
            "entries all present in prices.json with matching rates."
        )
        return 0
    for w in warnings:
        print(f"::warning::{w}" if annotate else f"WARNING: {w}", file=sys.stderr)
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
