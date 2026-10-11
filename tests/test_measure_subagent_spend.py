from __future__ import annotations

"""scripts/measure_subagent_spend.py: dedupe basis and subagent roll-up on a synthetic tree."""

import json
from pathlib import Path

from measure_subagent_spend import _deduped_main_turns, measure
from tes.cost import load_price_table


def _rec(mid: str, out: int, *, sidechain: bool = False) -> dict:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "uuid": f"{mid}-{out}",
        "message": {
            "id": mid,
            "model": "claude-sonnet-4-6",
            "content": [{"type": "text", "text": "x"}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": out},
        },
    }


def _write(path: Path, recs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def test_dedupe_counts_each_message_once_and_measure_separates_subagents(tmp_path: Path) -> None:
    parent = tmp_path / "proj" / "s.jsonl"
    # one API response written as 3 identical per-block records, plus a second response
    _write(parent, [_rec("a", 5), _rec("a", 5), _rec("a", 5), _rec("b", 7)])
    _write(
        tmp_path / "proj" / "s" / "subagents" / "agent-1.jsonl",
        [_rec("x", 20, sidechain=True), _rec("x", 20, sidechain=True)],
    )
    assert len(_deduped_main_turns(parent)) == 2

    pop = measure([parent], "t", load_price_table())
    # the adapter dedupes per message.id too now (it used to total 5 * 3 + 7 here)
    assert pop.main_adapter.output == 5 + 7
    assert pop.main_deduped.output == 5 + 7
    assert pop.sub.output == 20  # counted once
    assert pop.sub.files == 1
    assert pop.sessions_with_subagents == 1
    assert pop.sub.usd > 0
