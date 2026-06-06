from __future__ import annotations

"""tes/report.py — Output formatter for ThreeAxisResult.

format_human(result) -> str   Human-readable three-axis report with caveats inline.
format_json(result) -> str    Full JSON serialization including domain-of-validity.
"""

import dataclasses
import json
import textwrap
from typing import Any

from tes.score import ThreeAxisResult

_WIDTH = 76
_BORDER = "═" * _WIDTH
_DIVIDER_WIDTH = _WIDTH


def _section_divider(title: str) -> str:
    """Build e.g. '── TOKEN ECONOMY ─────...' at _WIDTH chars."""
    prefix = f"── {title} "
    fill = "─" * max(0, _WIDTH - len(prefix))
    return prefix + fill


def _wrap(text: str, indent: int = 2) -> str:
    pad = " " * indent
    return textwrap.fill(
        text,
        width=_WIDTH,
        initial_indent=pad,
        subsequent_indent=pad,
    )


def _format_turns(turns: list[int]) -> str:
    """Format [14,15,18,19] -> '14→15, 18→19'."""
    pairs = []
    for i in range(0, len(turns) - 1, 2):
        pairs.append(f"{turns[i]}→{turns[i+1]}")
    return ", ".join(pairs) if pairs else str(turns)


def format_human(result: ThreeAxisResult) -> str:
    lines: list[str] = []

    # Header
    lines.append(_BORDER)
    lines.append("  TOKEN-EFFICIENCY SCORER")
    lines.append(f"  Session:   {result.session_id}")
    lines.append(f"  Task type: {result.task_type}")
    lines.append(_BORDER)
    lines.append("")

    # ── TOKEN ECONOMY ──
    lines.append(_section_divider("TOKEN ECONOMY"))
    lines.append(f"  Real tokens: {result.real_tokens:,}")
    if result.band_verdict == "unavailable":
        lines.append("  Verdict:     UNAVAILABLE")
        lines.append(_wrap(result.interpretation))
    else:
        lines.append(f"  Verdict:     {result.band_verdict}")
        if result.p25 is not None:
            lines.append(
                f"  Baseline:    {result.task_type} — "
                f"p25: {result.p25:,} / median: {result.median:,} / p75: {result.p75:,}"
            )
        lines.append(_wrap(result.interpretation))
    lines.append("")
    lines.append(_wrap(result.token_domain_of_validity))
    lines.append("")

    # ── TRAJECTORY QUALITY ──
    lines.append(_section_divider("TRAJECTORY QUALITY"))
    if result.judge_verdict is not None:
        lines.append(f"  Verdict:     {result.judge_verdict} (score: {result.judge_score})")
        if result.judge_reasoning:
            lines.append(_wrap(f"Reasoning:   {result.judge_reasoning}", indent=2))
    else:
        lines.append("  Verdict:     UNAVAILABLE (no local judge configured)")
        lines.append("  To enable:   ollama pull qwen3:30b-a3b  (~18GB VRAM required)")
        lines.append("               Token and waste axes still run fully without the judge.")
    lines.append("")
    lines.append(_wrap(result.trajectory_domain_of_validity))
    lines.append("")

    # ── DETERMINISTIC WASTE ──
    lines.append(_section_divider("DETERMINISTIC WASTE"))
    if result.waste_event_count == 0:
        lines.append("  Events:  0 detected")
    else:
        lines.append(f"  Events:  {result.waste_event_count} event(s)")
        for idx, ev in enumerate(result.waste_events, 1):
            det = ev.get("detector", "?")
            ev_turns = ev.get("turns", [])
            evi = ev.get("evidence", {})
            turns_str = _format_turns(ev_turns)
            if det == "REPEATED-FAILED-RETRY":
                rc = ev.get("repeat_count", "?")
                snip = evi.get("error_snippet", "")[:60]
                lines.append(f"  [{idx}] {det} — {rc} retries, turns: {turns_str}")
                lines.append(f"      Evidence: {snip!r}")
            elif det == "REDUNDANT-READ":
                path_label = evi.get("path", "?")
                gap = evi.get("gap", "?")
                snip = evi.get("content_snippet", "")[:60]
                lines.append(
                    f"  [{idx}] {det} (PATH-{path_label}) — gap={gap}, turns: {turns_str}"
                )
                lines.append(f"      Evidence: {snip!r}")
    lines.append("")
    lines.append(_wrap(result.waste_domain_of_validity))
    lines.append(_BORDER)

    return "\n".join(lines)


def format_json(result: ThreeAxisResult) -> str:
    """Serialize the full ThreeAxisResult to indented JSON."""
    d: dict[str, Any] = dataclasses.asdict(result)
    return json.dumps(d, indent=2, ensure_ascii=False)
