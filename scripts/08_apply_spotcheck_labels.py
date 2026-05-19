"""Write manual Sonnet spot-check labels to the sample file."""
from __future__ import annotations
import json, pathlib

SAMPLE_PATH = pathlib.Path(
    "data/validation-corpus/annotations/spotcheck_sample.json"
)

# Manual labels by claude-sonnet-4-6, applied by reading each turn's full context.
# Format: keyed by (session_id, turn_index).
LABELS: dict[tuple[str, int], dict] = {
    # mypy-10308 t42: runs mypy after edit — standard verify, result used in next turn
    ("46d38fecb5547fbe", 42): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "standard verify after edit; result used in next turn (syntax error noticed)"},
    # pydantic-8316 t22: grep after reading main.py — result cited in next turn
    ("e90930394a49009f", 22): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "follows up prior read with targeted search; result directly used"},
    # pydantic-8316 t10: creates repro script after reading alias_generators.py
    ("46779ec4ac7ad7f2", 10): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "str_replace_editor create = write; prior file content used in script design"},
    # fffw-100 t132: finish tool — terminal summarization
    ("6a573854825819fd", 132): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "finish call; all context used by definition"},
    # textual-3459 t126: runs reproduce_issue.py --help after test run
    ("ac4e96238d14d4a0", 126): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "post-test verification command; result would inform completion"},
    # faker-652 t104: 'still issues' — reads automotive/__init__.py to investigate format bug
    ("6c1a9f8d5157ee22", 104): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "first read of automotive/__init__.py based on grep hit; result used in next turn"},
    # crate-python-474 t100: Phase 8 FINAL REVIEW — creates verification file (write op)
    ("8fe3ae31394917df", 100): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "create = write; long verification script; session ultimately fails"},
    # networkx-7024 t6 (swe_agent): runs reproduce.py — no structured tool_uses
    ("b7d0e2e71d5abf03", 6): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "swe_agent text-embedded command; result cited in next turn ('returned True')"},
    # cognitive_complexity-15 t4 (swe_agent): opens test file — no structured tool_uses
    ("34449e9f89dcc992", 4): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "swe_agent open command; no prior open of same file"},
    # stix2-369 t36: reads course_of_action test file after grep surprise
    ("efcfd7ab6dba01e6", 36): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "first read of this file; result used in next turn ('Wait, this seems wrong')"},
    # asv-702 t42 (swe_agent): retries asv run with different flags after same error
    ("1bfb853597e0fec2", 42): {"is_retry": True, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "explicit retry of asv run; text says 'trying explicitly again'; same error from prior attempt visible in prev_content"},
    # intervals-54 t26: grep for NumberInterval class def
    ("0d6ab8c934f0de65", 26): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "targeted grep; not a file re-read"},
    # beets-3863 t2: think tool on first analytical turn
    ("225c0fb6b5bfcee6", 2): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "think tool is reasoning, not file read; result used in next turn's plan"},
    # hydra-2189 t36: str_replace where old_str == new_str — failed edit retried
    ("7f8015799eab88ec", 36): {"is_retry": True, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "old_str identical to new_str; meaningless no-op edit retried after same import error"},
    # psf__black-3451 t186: switches approach after '--no-preview' option not found
    ("4ca31e6363afe7f8", 186): {"is_retry": False, "is_backtrack": True, "tool_result_used": True, "redundant_read": False, "notes": "changes from CLI flag to pyproject.toml after option-not-found error; clear strategy switch"},
    # dask-9378 t94: 'previous attempts unsuccessful, try different approach' + str_replace
    ("31bf68168acc8def", 94): {"is_retry": False, "is_backtrack": True, "tool_result_used": True, "redundant_read": False, "notes": "explicit 'previous attempts... unsuccessful. Let's try a different approach' text; textbook backtrack"},
    # dask-9378 t92: 'still failing, alternative approach' + undo_edit
    ("31bf68168acc8def", 92): {"is_retry": False, "is_backtrack": True, "tool_result_used": True, "redundant_read": False, "notes": "undo_edit + 'Alternative Approach' text; explicit undo of prior edit = backtrack"},
    # pydantic-8316 t32: re-reads _fields.py lines 195-205; same file read at line 125 two turns prior, no write between
    ("e90930394a49009f", 32): {"is_retry": False, "is_backtrack": False, "tool_result_used": True, "redundant_read": True, "notes": "same file (_fields.py) read at t22 (line 125) and now t32 (line 195); no write between; same session, same agent"},
    # hydra-2189 t58: identical str_replace to t36 — same failed no-op repeated
    ("7f8015799eab88ec", 58): {"is_retry": True, "is_backtrack": False, "tool_result_used": True, "redundant_read": False, "notes": "exact same old_str/new_str/path as t36; agent making same non-edit repeatedly"},
}


def main() -> None:
    samples = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    applied = 0
    for s in samples:
        key = (s["session_id"], s["turn_index"])
        if key in LABELS:
            s["spotcheck_labels"] = {
                **LABELS[key],
                "labeler": "claude-sonnet-4-6",
            }
            applied += 1

    SAMPLE_PATH.write_text(json.dumps(samples, indent=2), encoding="utf-8")
    print(f"Applied {applied}/{len(samples)} labels.")

    # Summary statistics
    pos_retry = sum(1 for s in samples if s["spotcheck_labels"]["is_retry"])
    pos_bt = sum(1 for s in samples if s["spotcheck_labels"]["is_backtrack"])
    pos_ru = sum(1 for s in samples if s["spotcheck_labels"]["tool_result_used"])
    pos_rr = sum(1 for s in samples if s["spotcheck_labels"]["redundant_read"])
    n = len(samples)
    print(f"\nSpot-check label distribution (n={n}):")
    print(f"  is_retry:          {pos_retry}/{n} positive ({100*pos_retry//n}%)")
    print(f"  is_backtrack:      {pos_bt}/{n} positive ({100*pos_bt//n}%)")
    print(f"  tool_result_used:  {pos_ru}/{n} positive ({100*pos_ru//n}%)")
    print(f"  redundant_read:    {pos_rr}/{n} positive ({100*pos_rr//n}%)")


if __name__ == "__main__":
    main()
