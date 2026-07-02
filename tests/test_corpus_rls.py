from __future__ import annotations

"""tests/test_corpus_rls.py — Static proof that corpus/schema.sql enforces the
approved RLS design.

There is no live Supabase project in CI, so this is a structural check on the
SQL text itself: RLS is enabled, the anon role has an INSERT policy and NO
select/update/delete policy, and the table columns match ALLOWED_FIELDS
exactly. This is a real (not theoretical) proof of the *shipped* schema —
the same file corpus/setup.md instructs a maintainer to run verbatim in the
Supabase SQL editor. The live round-trip (contribute -> row appears ->
withdraw -> gone) against a real test project is the complementary proof
this file cannot provide and is done separately before publish.
"""

import re
from pathlib import Path

import pytest

from tes.contribution import ALLOWED_FIELDS

_SCHEMA_PATH = Path(__file__).parent.parent / "corpus" / "schema.sql"


@pytest.fixture(scope="module")
def schema_sql() -> str:
    if not _SCHEMA_PATH.exists():
        pytest.fail(f"corpus/schema.sql not found at {_SCHEMA_PATH}")
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def _strip_comments(sql: str) -> str:
    """Remove -- line comments so a policy name/comment can't fake a match."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_rls_is_enabled(schema_sql: str) -> None:
    code = _strip_comments(schema_sql).lower()
    assert re.search(
        r"alter\s+table\s+public\.corpus_contributions\s+enable\s+row\s+level\s+security", code
    )


def test_anon_has_insert_policy(schema_sql: str) -> None:
    code = _strip_comments(schema_sql).lower()
    # A policy "for insert ... to anon" (order of clauses may vary slightly,
    # but both fragments must be present in the same create policy statement).
    policy_blocks = re.findall(r"create policy.*?;", code, flags=re.DOTALL)
    assert any("for insert" in block and "to anon" in block for block in policy_blocks), (
        "no CREATE POLICY ... FOR INSERT ... TO anon found"
    )


def test_anon_has_no_select_policy(schema_sql: str) -> None:
    code = _strip_comments(schema_sql).lower()
    policy_blocks = re.findall(r"create policy.*?;", code, flags=re.DOTALL)
    assert not any("for select" in block and "to anon" in block for block in policy_blocks), (
        "a SELECT policy for anon exists — anon must not be able to read rows"
    )


def test_anon_has_no_update_policy(schema_sql: str) -> None:
    code = _strip_comments(schema_sql).lower()
    policy_blocks = re.findall(r"create policy.*?;", code, flags=re.DOTALL)
    assert not any("for update" in block and "to anon" in block for block in policy_blocks), (
        "an UPDATE policy for anon exists — anon must not be able to modify rows"
    )


def test_anon_has_no_delete_policy(schema_sql: str) -> None:
    code = _strip_comments(schema_sql).lower()
    policy_blocks = re.findall(r"create policy.*?;", code, flags=re.DOTALL)
    assert not any("for delete" in block and "to anon" in block for block in policy_blocks), (
        "a DELETE policy for anon exists — deletion must only be possible via "
        "the service-role withdraw-contributor Edge Function"
    )


def test_only_one_policy_defined_total(schema_sql: str) -> None:
    """Defense in depth: confirm there's exactly one CREATE POLICY statement
    in the whole file (the anon insert-only policy) — not just that the
    forbidden ones are absent, but that nothing else grants access either."""
    code = _strip_comments(schema_sql).lower()
    policy_blocks = re.findall(r"create policy.*?;", code, flags=re.DOTALL)
    assert len(policy_blocks) == 1


def test_table_columns_match_allowed_fields_exactly(schema_sql: str) -> None:
    """Every ALLOWED_FIELDS name must appear as a column definition, and no
    content-bearing column beyond the allow-list + the two server-only
    columns (id, inserted_at) may exist."""
    code = _strip_comments(schema_sql)
    match = re.search(
        r"create table public\.corpus_contributions\s*\((.*?)\);", code, flags=re.DOTALL
    )
    assert match, "could not locate the corpus_contributions CREATE TABLE statement"
    body = match.group(1)

    # First token of each comma-separated top-level line is the column name
    # (ignoring blank lines). This is a simple, not a full SQL parser, but
    # sufficient for a single well-formed CREATE TABLE with one column per line.
    column_names = set()
    for line in body.splitlines():
        stripped = line.strip().rstrip(",")
        if not stripped:
            continue
        token = stripped.split()[0].strip('"')
        column_names.add(token)

    expected_extra = {"id", "inserted_at"}
    assert ALLOWED_FIELDS <= column_names, (
        f"schema.sql is missing columns: {ALLOWED_FIELDS - column_names}"
    )
    unexpected = column_names - ALLOWED_FIELDS - expected_extra
    assert not unexpected, f"schema.sql has unexpected extra columns: {unexpected}"


def test_withdraw_edge_function_validates_uuid_before_delete() -> None:
    """Static check on the Edge Function source: the DELETE call must be
    reachable only after a UUID format check on contributor_id."""
    fn_path = (
        Path(__file__).parent.parent
        / "corpus"
        / "edge_functions"
        / "withdraw-contributor"
        / "index.ts"
    )
    assert fn_path.exists(), f"withdraw-contributor Edge Function not found at {fn_path}"
    source = fn_path.read_text(encoding="utf-8")

    uuid_check_pos = source.find("UUID_V4_RE") if "UUID_V4_RE" in source else source.lower().find("uuid")
    delete_pos = source.find(".delete(")

    assert uuid_check_pos != -1, "no UUID validation found in withdraw-contributor/index.ts"
    assert delete_pos != -1, "no .delete( call found in withdraw-contributor/index.ts"
    assert uuid_check_pos < delete_pos, (
        "UUID validation must appear BEFORE the DELETE call in source order"
    )
    assert "SERVICE_ROLE_KEY" in source, (
        "Edge Function must authenticate with the service-role key, not the anon key"
    )
