from __future__ import annotations

"""Measure how much spend subagent transcripts add on top of their parent sessions.

Before W1A the adapter ignored ``<project>/<sid>/subagents/agent-*.jsonl`` entirely (every
record is ``isSidechain: true``), so this spend was in no total. This script reports it, per
session set, in tokens and USD, with prices as they stand in the bundled table, and flags
models that are missing from it.

Read-only: it only READS session files and prints aggregates (counts, tokens, USD, model ids)
-- never message content. Run with an explicit --cc-path so the redirected-HOME test setup
cannot silently point it elsewhere:

    python scripts/measure_subagent_spend.py \\
        --cc-path C:/Users/<you>/.claude/projects \\
        --selection D:/tracegauge-audit/sections/C-raw/c2-selection.json \\
        --out D:/tracegauge-w1a/w1a/item1_invisible_spend.md

Two bases are reported because the main-chain adapter counts every assistant *record* while
Claude Code writes one record per content block of the same API response (identical usage
repeated): "adapter" = what ``tes`` totals today for the parent; "deduped" = each API
response (``message.id``) counted once, which is also how subagents are counted.
"""

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tes._digest import TurnDigest, reconstruct_digest
from tes.adapt import _message_dedup_key, _parse_usage, _read_jsonl, adapt_session
from tes.cost import compute_turn_cost, load_price_table
from tes.discovery import SUBAGENTS_DIRNAME, iter_session_files


@dataclass
class Bucket:
    """Token and USD totals for one population."""

    sessions: int = 0
    files: int = 0
    billed: int = 0  # input + cache creation + cache read + output
    real: int = 0  # billed - cache reads (the verdict axis' unit)
    output: int = 0
    usd: float = 0.0  # priced turns only
    unpriced_billed: int = 0
    unpriced_models: dict[str, int] = field(default_factory=dict)  # model -> billed tokens

    def add_turn(self, turn: TurnDigest, prices: dict[str, Any]) -> None:
        billed = turn.token_count_input + turn.token_count_output
        self.billed += billed
        self.real += turn.token_count_input - turn.cache_read + turn.token_count_output
        self.output += turn.token_count_output
        tc = compute_turn_cost(turn, prices)
        if tc.priced:
            self.usd += tc.total_usd
        elif billed:
            self.unpriced_billed += billed
            self.unpriced_models[tc.model_key] = self.unpriced_models.get(tc.model_key, 0) + billed


def _deduped_main_turns(path: Path) -> list[TurnDigest]:
    """Main-chain assistant turns with each API response (message.id) counted once."""
    per: dict[str, tuple[str, list[int]]] = {}
    for rec in _read_jsonl(path):
        if rec.get("type") != "assistant" or rec.get("isSidechain"):
            continue
        message = rec.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            continue
        counts = list(_parse_usage(message["usage"]))
        key = _message_dedup_key(rec)
        if key in per:
            model, prev = per[key]
            counts = [max(a, b) for a, b in zip(prev, counts, strict=True)]
            per[key] = (model, counts)
        else:
            per[key] = (str(message.get("model", "")), counts)
    turns: list[TurnDigest] = []
    for i, (model, (inp, cc, cr, out)) in enumerate(per.values()):
        if not (inp or cc or cr or out):
            continue
        turns.append(
            TurnDigest(
                turn_index=i,
                role="ai",
                tool_names=[],
                content_snippet="",
                token_count_input=inp + cc + cr,
                token_count_output=out,
                cache_read=cr,
                h2_duplicate=False,
                cache_creation=cc,
                model=model,
            )
        )
    return turns


@dataclass
class Population:
    """Aggregates for a set of main sessions and their subagent transcripts."""

    name: str
    main_adapter: Bucket = field(default_factory=Bucket)
    main_deduped: Bucket = field(default_factory=Bucket)
    sub: Bucket = field(default_factory=Bucket)
    sessions_with_subagents: int = 0


def measure(paths: list[Path], name: str, prices: dict[str, Any]) -> Population:
    pop = Population(name=name)
    for p in paths:
        rec = adapt_session(p)
        digest = reconstruct_digest(rec["digest"])
        pop.main_adapter.sessions += 1
        pop.main_deduped.sessions += 1
        for t in digest.turns:
            if t.role == "ai":
                pop.main_adapter.add_turn(t, prices)
        for t in _deduped_main_turns(p):
            pop.main_deduped.add_turn(t, prices)
        if digest.subagent_turns:
            pop.sessions_with_subagents += 1
            pop.sub.sessions += 1
            pop.sub.files += digest.subagent_count
            for t in digest.subagent_turns:
                pop.sub.add_turn(t, prices)
    return pop


def _pct(part: float, whole: float) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


def _fmt_models(b: Bucket) -> str:
    if not b.unpriced_models:
        return "none"
    return ", ".join(f"`{m}` ({n:,} tok)" for m, n in sorted(b.unpriced_models.items()))


def render(pops: list[Population], prices: dict[str, Any], extra: list[str]) -> str:
    lines = [
        "# Item 1d: spend invisible before subagent roll-up",
        "",
        f"Price table: bundled `tes/data/prices.json`, as_of `{prices.get('as_of')}`. USD counts "
        "priced turns only; tokens of models missing from the table are listed separately and "
        "are NOT in any USD figure (so USD is a floor wherever a model is flagged).",
        "",
        "Tokens: **billed** = input + cache creation + cache reads + output; **real** = billed "
        "minus cache reads (the verdict axis' unit).",
        "",
    ]
    for pop in pops:
        ma, md, sb = pop.main_adapter, pop.main_deduped, pop.sub
        lines += [
            f"## {pop.name}",
            "",
            f"{ma.sessions} main sessions, {pop.sessions_with_subagents} of them with subagents "
            f"({sb.files} subagent transcripts).",
            "",
            "| | billed tokens | real tokens | output tokens | USD (priced) |",
            "|---|---:|---:|---:|---:|",
            f"| main, adapter basis (what `tes` showed) | {ma.billed:,} | {ma.real:,} | "
            f"{ma.output:,} | ${ma.usd:,.2f} |",
            f"| main, deduped basis | {md.billed:,} | {md.real:,} | {md.output:,} | "
            f"${md.usd:,.2f} |",
            f"| **subagents (previously invisible)** | {sb.billed:,} | {sb.real:,} | "
            f"{sb.output:,} | ${sb.usd:,.2f} |",
            "",
            "Subagent share of the parent total:",
            "",
            "| basis | billed tokens | real tokens | USD |",
            "|---|---:|---:|---:|",
            f"| vs main adapter basis | {_pct(sb.billed, ma.billed)} | {_pct(sb.real, ma.real)} "
            f"| {_pct(sb.usd, ma.usd)} |",
            f"| vs main deduped basis | {_pct(sb.billed, md.billed)} | {_pct(sb.real, md.real)} "
            f"| {_pct(sb.usd, md.usd)} |",
            f"| subagents as % of (main deduped + subagents) | "
            f"{_pct(sb.billed, md.billed + sb.billed)} | {_pct(sb.real, md.real + sb.real)} | "
            f"{_pct(sb.usd, md.usd + sb.usd)} |",
            "",
            f"Unpriced models, main (adapter basis): {_fmt_models(ma)}  "
            f"-> {ma.unpriced_billed:,} billed tokens ({_pct(ma.unpriced_billed, ma.billed)} "
            "of main).",
            "",
            f"Unpriced models, subagents: {_fmt_models(sb)}  "
            f"-> {sb.unpriced_billed:,} billed tokens ({_pct(sb.unpriced_billed, sb.billed)} "
            "of subagent tokens).",
            "",
        ]
    lines += extra
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Measure subagent spend on top of parent sessions.")
    ap.add_argument("--cc-path", required=True, type=Path)
    ap.add_argument("--selection", type=Path, help="c2-selection.json (frozen set B paths).")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    prices = load_price_table()
    all_main = sorted(iter_session_files(args.cc_path))
    pops: list[Population] = []
    extra: list[str] = []
    if args.selection:
        sel = json.loads(args.selection.read_text(encoding="utf-8"))
        set_b = [Path(p) for p in sel["sel"]["B"]]
        missing = [p for p in set_b if not p.exists()]
        if missing:
            raise SystemExit(f"{len(missing)} frozen set-B files no longer exist")
        pops.append(measure(set_b, f"Phase 0 set B ({len(set_b)} frozen main sessions)", prices))
        # Set A's 46 subagent files: which parents do they belong to, how big are they.
        set_a_sub = [Path(p) for p in sel["sel"]["A"] if SUBAGENTS_DIRNAME in Path(p).parts]
        parents = defaultdict(int)
        sub_billed = 0
        for p in set_a_sub:
            parents[p.parent.parent.name] += 1
            recs = [r for r in _read_jsonl(p) if r.get("type") == "assistant"]
            seen: dict[str, list[int]] = {}
            for r in recs:
                u = (r.get("message") or {}).get("usage")
                if isinstance(u, dict):
                    c = list(_parse_usage(u))
                    k = _message_dedup_key(r)
                    seen[k] = [max(a, b) for a, b in zip(seen.get(k, [0] * 4), c, strict=True)]
            sub_billed += sum(sum(v) for v in seen.values())
        extra += [
            "## Phase 0 set A subagent files",
            "",
            f"{len(set_a_sub)} subagent files (the ones `tes score` picked in Phase 0), belonging "
            f"to {len(parents)} parent sessions; {sub_billed:,} billed tokens in total that "
            "scored as 0 tokens / UNAVAILABLE.",
            "",
        ]
    pops.append(measure(all_main, f"All main sessions found ({len(all_main)})", prices))

    # Orphans: subagent files whose parent main file is missing (cannot be rolled up).
    all_sub = [p for p in args.cc_path.rglob("*.jsonl") if SUBAGENTS_DIRNAME in p.parts]
    orphans = [p for p in all_sub if not (p.parent.parent.with_suffix(".jsonl")).exists()]
    extra += [
        "## Coverage",
        "",
        f"{len(all_sub)} subagent files on disk; {len(orphans)} have no parent session file "
        "next to their directory and cannot be rolled up.",
        "",
    ]
    text = render(pops, prices, extra)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
