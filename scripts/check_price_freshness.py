from __future__ import annotations

"""scripts/check_price_freshness.py — CI freshness gate for tracegauge's price table.

Fails (exit 1) when any non-retired entry in tes/data/prices.json has an
``as_of`` date (or, if the entry has none, the table-level ``as_of``) older
than ``tes.cost.STALE_THRESHOLD_DAYS`` (90 days), measured against the date
this script actually runs.

Minimum-viable port of adk-tracegauge's staleness-guard CONCEPT
(scripts/check_price_freshness.py there checks a Gemini/Claude/GPT table's
per-model ``fetched_on`` against the same 90-day threshold) — adapted here
to tracegauge's own per-model ``as_of``/``source_url``/``retired`` fields
via ``tes.cost.check_price_table_staleness``, which does the actual date
arithmetic (see that function's docstring for what's deliberately NOT
ported: promo-expiry handling, tiering, multi-provider support, the
regression gate — Phase 7 full-engine-move scope).

Pure date arithmetic against the bundled JSON file — no network calls, no
paid API calls, zero cost. This is exactly the mechanism the S1 audit found
missing entirely: prices.json went 67 days stale with zero CI signal ever
(see CHANGELOG.md 0.10.2).
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tes.cost import (  # noqa: E402
    STALE_THRESHOLD_DAYS,
    check_price_table_staleness,
    load_price_table,
)


def main() -> int:
    prices = load_price_table()
    models = prices.get("models", {})

    stale = check_price_table_staleness(prices)

    if not stale:
        checked = sum(1 for entry in models.values() if not entry.get("retired"))
        print(
            f"OK: all {checked} non-retired price entries are within "
            f"{STALE_THRESHOLD_DAYS} days of today."
        )
        return 0

    print(
        f"STALE PRICE ENTRIES (threshold {STALE_THRESHOLD_DAYS} days):",
        file=sys.stderr,
    )
    for model_key, as_of, age_days, source_url in sorted(stale):
        age_desc = "unparseable/missing date" if age_days < 0 else f"{age_days} days old"
        print(
            f"  - {model_key}: as_of={as_of} ({age_desc}) -- re-verify against {source_url}",
            file=sys.stderr,
        )
    print(
        "\nUpdate as_of + source_url (and the price itself, if it changed) for each "
        'flagged entry in tes/data/prices.json. Retired entries ("retired": true) are '
        "exempt from this check by design -- see prices.json's top-level note.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
