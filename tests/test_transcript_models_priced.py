"""Always-on CI guard (and unit tests) for scripts/check_transcript_models_priced.py.

CI has no real transcripts, so the fixture tests/fixtures/transcript_models/ is a committed,
content-free list of the model ids seen in real Claude Code transcripts (plus the current ids
on the official pricing page). When a new id shows up in real transcripts, add a line there in
the same change that adds its price -- that is what makes this test fail BEFORE a release
instead of leaving the id silently unpriced. The real-transcript scan is a pre-release step
(RELEASING.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.check_transcript_models_priced import (
    main,
    scan_transcript_models,
    unresolvable_models,
)
from tes.cost import load_price_table

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "transcript_models"
_OLD_TABLE = Path(__file__).parent / "fixtures" / "pricing" / "prices_b261c4b.json"
_BUNDLED = Path(__file__).resolve().parent.parent / "tes" / "data" / "prices.json"


def _write_transcript(path: Path, models: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "assistant", "message": {"model": m}}) for m in models]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_every_model_id_in_the_committed_fixture_is_priced() -> None:
    result = scan_transcript_models([_FIXTURE_DIR])
    assert result.files_scanned == 1
    assert {"claude-opus-5-5", "claude-sonnet-5-5", "<synthetic>"} <= set(result.turns)
    assert unresolvable_models(result.turns, load_price_table(_BUNDLED)) == []
    assert main([str(_FIXTURE_DIR)]) == 0


def test_original_miss_reproduced_from_the_transcript_side() -> None:
    # The table as of b261c4b did not price the ids that were already in real transcripts.
    result = scan_transcript_models([_FIXTURE_DIR])
    bad = unresolvable_models(result.turns, load_price_table(_OLD_TABLE))
    assert bad == [
        "claude-fable-5-1",
        "claude-haiku-5-5",
        "claude-mythos-5-1",
        "claude-opus-5-5",
        "claude-sonnet-5-5",
    ]


def test_synthetic_is_excluded_not_reported(tmp_path: Path) -> None:
    _write_transcript(tmp_path / "a.jsonl", ["<synthetic>", "claude-sonnet-5-5"])
    assert main([str(tmp_path)]) == 0


def test_unknown_id_fails_and_is_listed_with_count_and_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_transcript(tmp_path / "proj" / "s.jsonl", ["claude-opus-9-9", "claude-opus-9-9"])
    _write_transcript(tmp_path / "proj" / "s" / "subagents" / "agent-1.jsonl", ["claude-opus-5-5"])
    assert main([str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "'claude-opus-9-9': 2 assistant record(s)" in err
    assert "claude-opus-5-5" not in err


def test_subagent_files_are_scanned_too(tmp_path: Path) -> None:
    _write_transcript(tmp_path / "p" / "s" / "subagents" / "agent-1.jsonl", ["claude-mystery-1"])
    assert scan_transcript_models([tmp_path]).turns["claude-mystery-1"] == 1


def test_non_assistant_and_malformed_lines_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "x.jsonl").write_text(
        '{"type":"user","message":{"model":"claude-user-model"}}\n'
        "not json but has assistant and model\n"
        '{"type":"assistant","message":"model"}\n'
        '{"type":"assistant","message":{"model":"claude-sonnet-5"}}\n',
        encoding="utf-8",
    )
    result = scan_transcript_models([tmp_path])
    assert dict(result.turns) == {"claude-sonnet-5": 1}


def test_fails_closed_when_nothing_could_be_scanned(tmp_path: Path) -> None:
    assert main([str(tmp_path / "does-not-exist")]) == 2
    assert main([str(tmp_path)]) == 2  # exists but holds no transcripts
    (tmp_path / "empty.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")
    assert main([str(tmp_path)]) == 2  # transcripts but no assistant model ids


def test_date_suffixed_and_pattern_ids_resolve() -> None:
    prices = load_price_table(_BUNDLED)
    assert (
        unresolvable_models(["claude-haiku-4-5-20251001", "claude-sonnet-4-20250514"], prices) == []
    )
