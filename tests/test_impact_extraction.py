from __future__ import annotations

"""tests/test_impact_extraction.py — XX2: Edit/Write/MultiEdit/NotebookEdit
payload extraction and corpus-wide aggregation.

Covers: line-count-based additions/deletions for Edit, the Write
prior_content_unknown ambiguity (XX2.3), MultiEdit/NotebookEdit's
untested_tool_shape flag (XX2.1), fail-closed behavior on missing fields
(never guesses), and compute_impact_report's aggregation (legacy-row
counting, churn ranking, inline fraction reporting per AB3.2).
"""

import json

from tes.impact import compute_impact_report, extract_edit_operations


# ---------------------------------------------------------------------------
# extract_edit_operations
# ---------------------------------------------------------------------------


def test_edit_computes_additions_and_deletions_from_line_counts():
    ops = extract_edit_operations(
        "Edit",
        {"file_path": "/repo/foo.py", "old_string": "line1\nline2\n", "new_string": "line1\nline2\nline3\n"},
    )
    assert len(ops) == 1
    op = ops[0]
    assert op.path == "/repo/foo.py"
    assert op.deletions == 2  # old_string has 2 lines
    assert op.additions == 3  # new_string has 3 lines
    assert op.prior_content_unknown is False
    assert op.untested_tool_shape is False


def test_edit_missing_file_path_returns_nothing_not_a_guess():
    ops = extract_edit_operations("Edit", {"old_string": "a", "new_string": "b"})
    assert ops == []


def test_write_additions_only_prior_content_unknown():
    ops = extract_edit_operations("Write", {"file_path": "/repo/new.py", "content": "a\nb\nc\n"})
    assert len(ops) == 1
    op = ops[0]
    assert op.additions == 3
    assert op.deletions == 0
    assert op.prior_content_unknown is True
    assert op.untested_tool_shape is False


def test_multiedit_sums_across_sub_edits_and_flags_untested():
    ops = extract_edit_operations(
        "MultiEdit",
        {
            "file_path": "/repo/multi.py",
            "edits": [
                {"old_string": "x\n", "new_string": "x\ny\n"},
                {"old_string": "z\n", "new_string": ""},
            ],
        },
    )
    assert len(ops) == 2
    assert all(op.path == "/repo/multi.py" for op in ops)
    assert all(op.untested_tool_shape is True for op in ops)
    assert all(op.prior_content_unknown is False for op in ops)  # has both old and new
    assert ops[0].additions == 2 and ops[0].deletions == 1
    assert ops[1].additions == 0 and ops[1].deletions == 1


def test_multiedit_missing_edits_list_returns_nothing():
    assert extract_edit_operations("MultiEdit", {"file_path": "/repo/x.py"}) == []


def test_notebookedit_best_effort_flags_untested_and_unknown_prior():
    ops = extract_edit_operations(
        "NotebookEdit",
        {"notebook_path": "/repo/nb.ipynb", "new_source": "print(1)\nprint(2)\n"},
    )
    assert len(ops) == 1
    op = ops[0]
    assert op.path == "/repo/nb.ipynb"
    assert op.additions == 2
    assert op.prior_content_unknown is True
    assert op.untested_tool_shape is True


def test_notebookedit_missing_new_source_returns_nothing():
    assert extract_edit_operations("NotebookEdit", {"notebook_path": "/repo/nb.ipynb"}) == []


def test_unknown_tool_returns_nothing():
    assert extract_edit_operations("Read", {"file_path": "/repo/foo.py"}) == []
    assert extract_edit_operations("Bash", {"command": "ls"}) == []


def test_apply_patch_and_str_replace_editor_out_of_scope():
    """XX2.1: explicitly not attempted -- Codex/computer-use tool names,
    not Claude Code ones."""
    assert extract_edit_operations("apply_patch", {"patch": "*** Begin Patch"}) == []
    assert extract_edit_operations("str_replace_editor", {"command": "create"}) == []


def test_non_dict_input_returns_nothing():
    assert extract_edit_operations("Edit", None) == []  # type: ignore[arg-type]
    assert extract_edit_operations("Edit", "not a dict") == []  # type: ignore[arg-type]


def test_trailing_newline_does_not_count_as_an_extra_line():
    """_line_count: a trailing newline ends the last line, doesn't start
    an empty one -- 'a\\nb\\n' is 2 lines, not 3."""
    ops = extract_edit_operations(
        "Write", {"file_path": "/repo/f.py", "content": "a\nb\n"}
    )
    assert ops[0].additions == 2


def test_empty_content_is_zero_lines():
    ops = extract_edit_operations("Write", {"file_path": "/repo/f.py", "content": ""})
    assert ops[0].additions == 0


# ---------------------------------------------------------------------------
# compute_impact_report
# ---------------------------------------------------------------------------


def _row(session_id: str, edit_operations) -> dict:
    return {
        "session_id": session_id,
        "edit_operations": json.dumps(edit_operations) if edit_operations is not None else None,
    }


def test_legacy_rows_counted_separately_from_sessions_with_data():
    rows = [
        _row("legacy-1", None),
        _row("legacy-2", None),
        _row("has-data", []),
    ]
    report = compute_impact_report(rows)
    assert report.sessions_legacy == 2
    assert report.sessions_with_data == 1


def test_total_additions_deletions_and_operations_sum_correctly():
    rows = [
        _row("s1", [
            {"path": "a.py", "additions": 5, "deletions": 2, "tool": "Edit",
             "prior_content_unknown": False, "untested_tool_shape": False},
            {"path": "b.py", "additions": 3, "deletions": 0, "tool": "Write",
             "prior_content_unknown": True, "untested_tool_shape": False},
        ]),
    ]
    report = compute_impact_report(rows)
    assert report.total_operations == 2
    assert report.total_additions == 8
    assert report.total_deletions == 2


def test_prior_content_unknown_pct_only_counts_flagged_additions():
    rows = [
        _row("s1", [
            {"path": "a.py", "additions": 10, "deletions": 0, "tool": "Edit",
             "prior_content_unknown": False, "untested_tool_shape": False},
            {"path": "b.py", "additions": 10, "deletions": 0, "tool": "Write",
             "prior_content_unknown": True, "untested_tool_shape": False},
        ]),
    ]
    report = compute_impact_report(rows)
    assert report.prior_content_unknown_pct == 50.0  # 10 of 20 additions


def test_untested_tool_shape_pct_reported_inline():
    rows = [
        _row("s1", [
            {"path": "a.py", "additions": 1, "deletions": 0, "tool": "Edit",
             "prior_content_unknown": False, "untested_tool_shape": False},
            {"path": "b.py", "additions": 1, "deletions": 0, "tool": "MultiEdit",
             "prior_content_unknown": False, "untested_tool_shape": True},
        ]),
    ]
    report = compute_impact_report(rows)
    assert report.untested_tool_shape_pct == 50.0  # 1 of 2 operations


def test_churn_ranking_sorted_by_edit_count_descending():
    rows = [
        _row("s1", [
            {"path": "hot.py", "additions": 1, "deletions": 0, "tool": "Edit",
             "prior_content_unknown": False, "untested_tool_shape": False},
            {"path": "cold.py", "additions": 1, "deletions": 0, "tool": "Edit",
             "prior_content_unknown": False, "untested_tool_shape": False},
        ]),
        _row("s2", [
            {"path": "hot.py", "additions": 1, "deletions": 0, "tool": "Edit",
             "prior_content_unknown": False, "untested_tool_shape": False},
        ]),
    ]
    report = compute_impact_report(rows)
    assert report.top_files[0].path == "hot.py"
    assert report.top_files[0].edits == 2
    assert report.top_files[0].sessions_touched == 2
    assert report.top_files[1].path == "cold.py"
    assert report.top_files[1].edits == 1


def test_directory_aggregation_groups_files_by_parent():
    rows = [
        _row("s1", [
            {"path": "src/a.py", "additions": 1, "deletions": 0, "tool": "Edit",
             "prior_content_unknown": False, "untested_tool_shape": False},
            {"path": "src/b.py", "additions": 1, "deletions": 0, "tool": "Edit",
             "prior_content_unknown": False, "untested_tool_shape": False},
        ]),
    ]
    report = compute_impact_report(rows)
    assert len(report.top_directories) == 1
    assert report.top_directories[0].path == "src"
    assert report.top_directories[0].edits == 2


def test_empty_corpus_has_no_fractions_not_a_zero_division():
    report = compute_impact_report([])
    assert report.prior_content_unknown_pct is None
    assert report.untested_tool_shape_pct is None
    assert report.top_files == []


def test_malformed_json_in_a_row_is_skipped_not_fatal():
    rows = [{"session_id": "bad", "edit_operations": "{not valid json"}]
    report = compute_impact_report(rows)
    assert report.sessions_with_data == 1  # counted as "has data" (column non-NULL)
    assert report.total_operations == 0  # but contributes no operations
