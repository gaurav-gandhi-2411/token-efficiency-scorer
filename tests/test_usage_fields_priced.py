"""Always-on CI guard (and unit tests) for scripts/check_usage_fields_priced.py.

CI has no real transcripts, so tests/fixtures/usage_fields/ is a committed, content-free file of
real-shaped ``message.usage`` objects (every key path seen in real Claude Code transcripts as of
2026-10). When Claude Code starts writing a new usage key, add it to that fixture AND to
config/usage_field_rules.json (priced / free / ignored-with-reason / unmodelled) in the same
change -- an unregistered key fails here before release. The real-transcript scan is a pre-release
step (RELEASING.md).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.check_usage_fields_priced import (
    REGISTRY_PATH,
    RULES,
    load_registry,
    main,
    scan_usage_fields,
    violations,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "usage_fields"


def _write(path: Path, usages: list[dict[str, Any]]) -> Path:
    lines = [
        json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5", "usage": u}})
        for u in usages
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_every_key_path_in_the_committed_fixture_has_a_rule() -> None:
    registry = load_registry()
    scan = scan_usage_fields([_FIXTURE_DIR], registry["value_guards"])
    assert scan.records == 3
    # the fields that move the bill must be in the fixture, so dropping them there would fail
    assert {
        "usage.cache_creation.ephemeral_1h_input_tokens",
        "usage.cache_creation.ephemeral_5m_input_tokens",
        "usage.server_tool_use.web_search_requests",
        "usage.speed",
        "usage.inference_geo",
    } <= set(scan.paths)
    assert violations(scan, registry) == ([], [])
    assert main([str(_FIXTURE_DIR)]) == 0


def test_registry_is_well_formed_and_value_guards_target_registered_fields() -> None:
    registry = load_registry()
    for entry in registry["fields"].values():
        assert entry["rule"] in RULES and entry["reason"].strip()
    assert set(registry["value_guards"]) <= set(registry["fields"])
    assert REGISTRY_PATH.is_file()


def test_the_priced_cache_fields_are_marked_priced() -> None:
    fields = load_registry()["fields"]
    for key in (
        "usage.cache_creation.ephemeral_1h_input_tokens",
        "usage.cache_creation.ephemeral_5m_input_tokens",
        "usage.cache_read_input_tokens",
        "usage.input_tokens",
        "usage.output_tokens",
    ):
        assert fields[key]["rule"] == "priced"


def _first_usage() -> dict[str, Any]:
    line = (
        (_FIXTURE_DIR / "usage_records_2026-10.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    usage: dict[str, Any] = json.loads(line)["message"]["usage"]
    return copy.deepcopy(usage)


def test_a_new_unknown_usage_key_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    usage = _first_usage()
    usage["cache_creation"]["ephemeral_24h_input_tokens"] = 7  # hypothetical new write tier
    usage["brand_new_charge"] = 1
    f = _write(tmp_path / "t.jsonl", [usage])
    assert main([str(f)]) == 1
    err = capsys.readouterr().err
    assert "usage.cache_creation.ephemeral_24h_input_tokens" in err
    assert "usage.brand_new_charge" in err


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("speed", "fast"),  # fast mode: premium price, not modelled
        ("inference_geo", "us"),  # data residency 1.1x, not modelled
        ("service_tier", "priority"),  # no price on the page
        ("fallback_credit", {"amount": 1}),  # non-null: cannot be priced from here
    ],
)
def test_billing_changing_values_fail_even_though_the_key_is_known(
    tmp_path: Path, key: str, value: Any
) -> None:
    usage = _first_usage()
    usage[key] = value
    f = _write(tmp_path / "t.jsonl", [usage])
    registry = load_registry()
    if isinstance(value, dict):
        # a dict has no leaf at the guarded path; its child key is unregistered -> still fails
        assert main([str(f)]) == 1
        return
    unknown, bad = violations(scan_usage_fields([f], registry["value_guards"]), registry)
    assert unknown == []
    assert len(bad) == 1 and key in bad[0]
    assert main([str(f)]) == 1


def test_a_hostile_transcript_with_junk_lines_does_not_crash_or_pass(tmp_path: Path) -> None:
    f = tmp_path / "junk.jsonl"
    f.write_text('not json "usage"\n{"type": "assistant", "message": {"usage": 5}}\n', "utf-8")
    assert main([str(f)]) == 2  # nothing scannable is "could not check", never a pass


def test_missing_path_is_could_not_check(tmp_path: Path) -> None:
    assert main([str(tmp_path / "nope")]) == 2
