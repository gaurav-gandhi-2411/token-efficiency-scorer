from __future__ import annotations

"""One-shot script: run attribution on the two heaviest accessible sessions."""

import json
import sqlite3
from pathlib import Path

from tes._digest import reconstruct_digest
from tes.adapt import adapt_session
from tes.attribution import compute_attribution
from tes.waste import build_waste_entry

db = Path.home() / ".tes" / "tes.db"
conn = sqlite3.connect(str(db))
conn.row_factory = sqlite3.Row


def show_attribution(session_id_prefix: str, label: str) -> None:
    row = conn.execute(
        "SELECT session_id, task_type, real_tokens, source_path, waste_events "
        "FROM sessions WHERE session_id LIKE ? LIMIT 1",
        (session_id_prefix + "%",),
    ).fetchone()
    if not row:
        print(f"NOT FOUND: {session_id_prefix}")
        return

    src = Path(row["source_path"])
    if not src.exists():
        print(f"FILE MISSING: {src}")
        return

    record = adapt_session(src)
    if record is None:
        print("ADAPT FAILED")
        return

    digest_turns = record.get("digest", {}).get("turns", [])
    waste_entry = build_waste_entry(record["session_id"], digest_turns)

    digest_obj = record.get("digest")
    if digest_obj is None:
        print("No digest on record")
        return

    digest = reconstruct_digest(digest_obj)
    attr = compute_attribution(digest, waste_entry)

    tb = attr.total_billed_tokens
    rt = attr.real_tokens

    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"  task_type: {row['task_type']}   session: {row['session_id']}")
    print(f"{'=' * 70}")
    print(f"  real_tokens (verdict basis):         {rt:>12,}")
    print(f"  total_billed_tokens (attribution):   {tb:>12,}")
    print(f"  cache_carry ratio (cache/billed):    {(tb - rt) / tb * 100 if tb else 0:.1f}%")
    print(f"  total_usd:                           ${attr.total_usd:>10.4f}")
    print()
    print(f"  {'Bucket':<43} {'tokens':>10}  {'%billed':>7}  {'usd':>9}  {'%cost':>7}")
    print(f"  {'-' * 83}")

    buckets = [
        ("B1 Redundant-read waste", attr.rr_waste_tokens, attr.rr_waste_usd),
        ("B2 Retry-loop waste", attr.rfr_waste_tokens, attr.rfr_waste_usd),
        ("B3 Context re-send (cache reads)", attr.context_resend_tokens, attr.context_resend_usd),
        ("B4 Output", attr.output_tokens, attr.output_usd),
        ("B5 Fresh input (residual)", attr.fresh_input_tokens, attr.fresh_input_usd),
        ("B6 Context growth (cache writes)", attr.context_growth_tokens, attr.context_growth_usd),
    ]
    for name, tok, usd in buckets:
        pct_tok = tok / tb * 100 if tb else 0
        pct_usd = usd / attr.total_usd * 100 if attr.total_usd else 0
        print(f"  {name:<43} {tok:>10,}  {pct_tok:>6.1f}%  ${usd:>8.4f}  {pct_usd:>6.1f}%")

    print(f"  {'-' * 83}")
    total_check = sum(t for _, t, _ in buckets)
    print(f"  {'TOTAL':<43} {total_check:>10,}  100.0%  ${attr.total_usd:>8.4f}  100.0%")
    print(f"  reconciles_to_total_billed: {total_check == tb}")

    we_stored = json.loads(row["waste_events"])
    if we_stored:
        print(f"\n  Stored waste events: {len(we_stored)}")
        for e in we_stored[:4]:
            print(
                f"    {e['detector']:<28}  turns={e['turns']}  "
                f"wasted_cost=${e.get('wasted_cost_usd', 0):.4f}"
            )
    print()


show_attribution("adc16a28", "INFRA-DEPLOY  6.3M real_tokens  6 waste events")
show_attribution("a56fd010", "ML-EVAL  7.4M real_tokens  0 waste events")
conn.close()
