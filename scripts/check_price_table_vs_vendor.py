"""scripts/check_price_table_vs_vendor.py — GG1: fetches Anthropic's own
published pricing page and compares it against every non-retired entry in
this repo's own price table (tes/data/prices.json). Replaces the FF4.3
cross-repo comparison plan (never implemented, see adk-tracegauge's
docs/audit/PHASE8_PLAN.md FF2.2) -- that plan compared this table against
adk-tracegauge's SIBLING table instead of against the vendor, which stays
green even if both drift into staleness together (the failure that
actually occurred: this table itself went 67 days stale with zero CI
signal before scripts/check_price_freshness.py existed -- see that
script's own docstring and CHANGELOG.md 0.10.2).

TWO DISTINCT FAILURE MODES, never conflated (GG1.3):
1. FETCH/PARSE FAILURE -- the vendor page couldn't be reached, or its
   structure has changed enough that this script's parser can't find the
   expected table. This means "we don't know if our price is right", NOT
   "our price is right" -- fails the run loudly, distinct from a mismatch.
2. MISMATCH -- the page was fetched and parsed successfully, and a rate we
   found disagrees with our table.

VENDOR FEASIBILITY (verified live this session, VERIFIED not assumed):
Anthropic publishes a real, purpose-built markdown export at
https://platform.claude.com/docs/en/about-claude/pricing.md -- a clean
"| Model | Base Input Tokens | 5m Cache Writes | 1h Cache Writes | Cache
Hits & Refreshes | Output Tokens |" table, distinct from the HTML page
(which also works but needs real HTML parsing -- the .md export is
strictly simpler and was chosen for that reason). This is the ONLY vendor
this script covers, since this repo's own price table is Claude-only.

Only non-retired entries (``"retired": true`` in the JSON) are checked --
Anthropic's live page does not list every historically-retired model this
table still carries for pricing old sessions, so a retired entry cannot be
verified against a page that doesn't show it; this mirrors
check_price_freshness.py's own existing exemption for retired entries.

Zero-cost, no paid API calls -- plain HTTP GET via stdlib `urllib.request`
only, no new dependency added for this.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tes.cost import load_price_table  # noqa: E402

_USER_AGENT = "Mozilla/5.0 (compatible; tracegauge-price-vendor-check/1.0)"
_TIMEOUT_SECONDS = 20

ANTHROPIC_MD_URL = "https://platform.claude.com/docs/en/about-claude/pricing.md"

#: Anthropic's page displays "Claude Opus 5"; this table's key is
#: "claude-opus-5" -- mapped explicitly rather than algorithmically
#: normalized, one entry per non-retired model this table currently
#: prices, verified live this session against the real fetched page (all
#: 13 confirmed present, see PR description).
ANTHROPIC_MODEL_NAMES: dict[str, str] = {
    "claude-fable-5": "Claude Fable 5",
    "claude-mythos-5": "Claude Mythos 5",
    "claude-opus-5": "Claude Opus 5",
    "claude-opus-4-8": "Claude Opus 4.8",
    "claude-opus-4-7": "Claude Opus 4.7",
    "claude-opus-4-6": "Claude Opus 4.6",
    "claude-opus-4-5": "Claude Opus 4.5",
    "claude-opus-4-1": "Claude Opus 4.1",
    "claude-opus-4": "Claude Opus 4",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "claude-sonnet-4-5": "Claude Sonnet 4.5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
}


class FetchError(Exception):
    """The vendor page could not be fetched or parsed as expected -- see
    module docstring's "TWO DISTINCT FAILURE MODES"."""


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
            status = getattr(resp, "status", 200)
            if status != 200:
                raise FetchError(f"{url}: HTTP {status}")
            body: bytes = resp.read()
            return body.decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise FetchError(f"{url}: {e}") from e
    except TimeoutError as e:
        raise FetchError(f"{url}: timed out after {_TIMEOUT_SECONDS}s") from e


def parse_anthropic_markdown(md: str) -> dict[str, tuple[float, float]]:
    """{display_name: (input_usd_per_mtok, output_usd_per_mtok)} from the
    '## Model pricing' table's 'Base Input Tokens'/'Output Tokens' columns.
    Returns an empty dict (never raises) if the table header isn't found --
    the caller (main()) treats that as a total parse failure."""
    # Columns: Model | Base Input Tokens | 5m Cache Writes | 1h Cache Writes
    # | Cache Hits & Refreshes | Output Tokens -- Output is cells[5], NOT
    # cells[4] (that's the cache-hit column) -- confirmed by live column
    # count this session, not assumed from the header text alone.
    result: dict[str, tuple[float, float]] = {}
    idx = md.find("## Model pricing")
    if idx == -1:
        return result
    for line in md[idx:].splitlines():
        if line.startswith("#") and "Model pricing" not in line:
            break  # next section -- stop, do not silently read past our table
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6:
            continue
        raw_name = cells[0]
        if raw_name.lower() == "model" or set(raw_name) <= {"-", " ", ":"}:
            continue
        # Strip a trailing markdown link, e.g. "Claude Opus 4.1 ([retired,
        # ...](url))" -> "Claude Opus 4.1" -- only the display name matters.
        display_name = re.sub(r"\s*\(\[.*", "", raw_name).strip()
        input_match = re.search(r"\$([\d.]+)\s*/\s*MTok", cells[1])
        output_match = re.search(r"\$([\d.]+)\s*/\s*MTok", cells[5])
        if not input_match or not output_match:
            continue
        result[display_name] = (float(input_match.group(1)), float(output_match.group(1)))
    return result


def main() -> int:
    prices = load_price_table()
    models: dict[str, dict[str, object]] = prices.get("models", {})

    retryable_errors: list[str] = []
    unmapped: list[str] = []
    mismatches: list[tuple[str, float, float, float, float]] = []
    verified = 0

    try:
        anthropic_md = _fetch(ANTHROPIC_MD_URL)
        anthropic_table = parse_anthropic_markdown(anthropic_md)
        if not anthropic_table:
            raise FetchError(f"{ANTHROPIC_MD_URL}: '## Model pricing' table not found")
    except FetchError as e:
        retryable_errors.append(str(e))
        anthropic_table = None

    for model_key, entry in models.items():
        if entry.get("retired"):
            continue
        if model_key not in ANTHROPIC_MODEL_NAMES:
            continue  # not yet mapped -- see ANTHROPIC_MODEL_NAMES's own note
        our_input = entry.get("input_usd_per_mtok")
        our_output = entry.get("output_usd_per_mtok")
        if not isinstance(our_input, (int, float)) or not isinstance(our_output, (int, float)):
            continue
        if anthropic_table is None:
            continue  # already counted as a retryable_error above
        display_name = ANTHROPIC_MODEL_NAMES[model_key]
        fetched = anthropic_table.get(display_name)
        if fetched is None:
            unmapped.append(f"{model_key}: '{display_name}' not found on Anthropic's page")
            continue
        verified += 1
        if (float(our_input), float(our_output)) != fetched:
            mismatches.append((model_key, float(our_input), float(our_output), *fetched))

    if not retryable_errors and not unmapped and not mismatches:
        print(f"OK: {verified} price entries verified against Anthropic's own current page.")
        return 0

    if retryable_errors:
        print(
            "COULD NOT VERIFY (fetch/parse failure -- distinct from a mismatch, see module docstring):",
            file=sys.stderr,
        )
        for msg in retryable_errors:
            print(f"  - {msg}", file=sys.stderr)
    if unmapped:
        if retryable_errors:
            print(file=sys.stderr)
        print(
            "COULD NOT VERIFY (entry not found on Anthropic's page -- needs manual review):",
            file=sys.stderr,
        )
        for msg in unmapped:
            print(f"  - {msg}", file=sys.stderr)
    if mismatches:
        if retryable_errors or unmapped:
            print(file=sys.stderr)
        print(
            "PRICE MISMATCH (our table disagrees with Anthropic's own current page):",
            file=sys.stderr,
        )
        for model_key, our_in, our_out, vendor_in, vendor_out in mismatches:
            print(
                f"  - {model_key}: ours=${our_in}/${our_out} per MTok, "
                f"vendor=${vendor_in}/${vendor_out} per MTok",
                file=sys.stderr,
            )

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
