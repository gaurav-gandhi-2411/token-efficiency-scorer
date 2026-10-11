from __future__ import annotations

"""usage.speed / usage.inference_geo reach TurnDigest, and a subagent aggregate never mixes
requests that bill differently.

Fast mode and the US-only multiplier scale a WHOLE request, so a per-(file, model) sum of
requests with different (speed, geo) would be priced with the wrong multiplier. The adapter
therefore splits an aggregate by (model, speed, inference_geo). Synthetic records only.
"""

import json
from pathlib import Path
from typing import Any

from tes.adapt import _parse_billing_modifiers, adapt_session, collect_subagent_usage

MODEL = "claude-opus-5-5"


def _assistant(
    msg_id: str, usage: dict[str, Any], *, sidechain: bool, block: int = 0
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "uuid": f"{msg_id}-{block}",
        "message": {
            "id": msg_id,
            "role": "assistant",
            "model": MODEL,
            "content": [{"type": "text", "text": "x"}],
            "usage": usage,
        },
    }


def _usage(out: int, **extra: Any) -> dict[str, Any]:
    return {
        "input_tokens": 10,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": out,
        **extra,
    }


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_parse_billing_modifiers_normalises_to_what_changes_the_bill() -> None:
    assert _parse_billing_modifiers({"speed": "fast", "inference_geo": "us"}) == ("fast", "us")
    for usage in (
        {},
        {"speed": "standard", "inference_geo": "global"},
        {"speed": None, "inference_geo": "not_available"},
        {"speed": "turbo", "inference_geo": "eu"},  # unknown values price standard
    ):
        assert _parse_billing_modifiers(usage) == ("", "")


def test_main_chain_turn_carries_speed_and_geo_of_its_usage_record(tmp_path: Path) -> None:
    session = tmp_path / "p" / "s1.jsonl"
    _write(
        session,
        [
            _assistant("m1", _usage(5, speed="fast", inference_geo="us"), sidechain=False),
            _assistant("m2", _usage(5, speed="standard", inference_geo="global"), sidechain=False),
            _assistant("m3", _usage(5), sidechain=False),
        ],
    )
    turns = adapt_session(session)["digest"]["turns"]
    assert [(t["speed"], t["inference_geo"]) for t in turns] == [("fast", "us"), ("", ""), ("", "")]


def test_content_block_records_of_one_response_take_the_usage_records_value(
    tmp_path: Path,
) -> None:
    # message.id m1 spans 3 records; speed/geo repeat on each. The response is one turn that
    # carries usage once and is fast + us; the sibling content-block turns carry zero tokens.
    session = tmp_path / "p" / "s1.jsonl"
    usage = _usage(5, speed="fast", inference_geo="us")
    _write(session, [_assistant("m1", usage, sidechain=False, block=b) for b in range(3)])
    ai = [t for t in adapt_session(session)["digest"]["turns"] if t["role"] == "ai"]
    carrier = [t for t in ai if t["token_count_output"]]
    assert len(carrier) == 1 and (carrier[0]["speed"], carrier[0]["inference_geo"]) == (
        "fast",
        "us",
    )


def test_subagent_aggregate_is_split_by_speed_and_geo(tmp_path: Path) -> None:
    parent = tmp_path / "proj" / "sid.jsonl"
    _write(parent, [_assistant("p1", _usage(1), sidechain=False)])
    sub = tmp_path / "proj" / "sid" / "subagents" / "agent-a.jsonl"
    _write(
        sub,
        [
            # two standard requests -> one aggregate (request_count 2)
            _assistant("s1", _usage(100), sidechain=True),
            _assistant("s2", _usage(200), sidechain=True),
            # one fast request, one fast+us request, written as 2 records each (dedupe)
            _assistant("s3", _usage(300, speed="fast"), sidechain=True, block=0),
            _assistant("s3", _usage(300, speed="fast"), sidechain=True, block=1),
            _assistant("s4", _usage(400, speed="fast", inference_geo="us"), sidechain=True),
            _assistant("s5", _usage(500, inference_geo="us"), sidechain=True),
        ],
    )
    rolled = collect_subagent_usage(parent)
    assert rolled is not None
    by_key = {(t["speed"], t["inference_geo"]): t for t in rolled["turns"]}
    assert set(by_key) == {("", ""), ("fast", ""), ("fast", "us"), ("", "us")}
    assert (by_key[("", "")]["token_count_output"], by_key[("", "")]["request_count"]) == (300, 2)
    assert (by_key[("fast", "")]["token_count_output"], by_key[("fast", "")]["request_count"]) == (
        300,
        1,
    )
    assert by_key[("fast", "us")]["token_count_output"] == 400
    assert by_key[("", "us")]["token_count_output"] == 500
    # no token is lost or double counted by the split
    assert sum(t["token_count_output"] for t in rolled["turns"]) == 1500
    assert rolled["message_count"] == 5


def test_subagent_file_without_modifiers_still_yields_one_turn_per_model(tmp_path: Path) -> None:
    parent = tmp_path / "proj" / "sid.jsonl"
    _write(parent, [_assistant("p1", _usage(1), sidechain=False)])
    sub = tmp_path / "proj" / "sid" / "subagents" / "agent-a.jsonl"
    _write(sub, [_assistant(f"s{i}", _usage(10), sidechain=True) for i in range(4)])
    rolled = collect_subagent_usage(parent)
    assert rolled is not None
    (only,) = rolled["turns"]
    assert (only["request_count"], only["speed"], only["inference_geo"]) == (4, "", "")
