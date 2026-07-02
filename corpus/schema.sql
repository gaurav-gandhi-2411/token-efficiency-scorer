-- corpus/schema.sql — tracegauge community corpus table (Supabase / Postgres)
--
-- TWO INDEPENDENT SAFETY LAYERS. Both are required; neither alone is sufficient.
--
--   1. Content-free by construction (client side): tes/contribution.py builds
--      each row field-by-field from an allow-list of 14 names (ALLOWED_FIELDS).
--      tes/corpus_client.py re-verifies the actual serialized POST body against
--      that same allow-list immediately before every send. No session_id,
--      source_path, prompt/code text, evidence snippet, or free-text field is
--      ever assembled into a row, let alone transmitted. The columns below are
--      exactly those 14 fields — nothing more.
--
--   2. Row Level Security (this file): even though the client only ever SENDS
--      content-free rows, RLS independently constrains what the `anon` role
--      (the credential embedded in the tracegauge client) is allowed to do to
--      this table at the database layer. The only anon policy defined below is
--      INSERT. There is no SELECT, UPDATE, or DELETE policy for anon anywhere
--      in this file. Postgres RLS defaults to deny for any operation without a
--      matching policy, so — independent of anything the client code does or
--      doesn't do — the anon role can add rows but can never read, modify, or
--      delete a row it (or anyone else) has contributed. The only way to
--      remove rows is the `withdraw-contributor` Edge Function, which runs
--      with the service-role key (server-side only, never shipped to the
--      client) and therefore bypasses RLS entirely for its single DELETE.
--
-- Run this file once in the Supabase SQL editor when setting up a new
-- project. See corpus/setup.md for the full setup walkthrough.

create table public.corpus_contributions (
    -- Server-assigned primary key. Never sent by the client, never used to
    -- link rows to a person — contributor_id (below) is the only linkage key,
    -- and it is optional (null for anonymous contributions).
    id bigint generated always as identity primary key,

    -- The 14 ALLOWED_FIELDS from tes/contribution.py, in the same order as
    -- the frozenset is documented there. Types match the payload shapes
    -- produced by build_contribution_payload() and re-checked by
    -- verify_payload_content_free() in tes/corpus_client.py.
    task_type text not null,
    real_tokens integer not null,
    token_count_input integer,
    token_count_output integer,
    cache_creation integer,
    cache_read integer,
    waste_event_count integer not null,
    waste_detectors_fired text[] not null default '{}',
    model text,
    turn_count integer,
    week_bucket text,
    tracegauge_version text not null,
    schema_version text not null,
    contributor_id uuid,

    -- Server-side only. Not part of the client payload (the client never
    -- sends a timestamp field at all — see week_bucket, which is a coarse
    -- ISO-week bucket, not a precise timestamp, per the P7 content-free
    -- design). Populated automatically at insert time.
    inserted_at timestamptz not null default now()
);

-- Enable Row Level Security. Until at least one policy grants access, ALL
-- access (including to the table owner's other roles, but not the owner
-- itself / service_role, which bypasses RLS by default) is denied.
alter table public.corpus_contributions enable row level security;

-- The ONLY policy defined for the anon role: insert-only.
-- No select/update/delete policy for anon exists anywhere in this file —
-- by Postgres RLS default-deny, this means the anon role (the credential
-- embedded in the tracegauge client) can add rows but can NEVER read,
-- modify, or delete any row, including its own. Deletion is only possible
-- via the withdraw-contributor Edge Function, which authenticates with the
-- service_role key (bypasses RLS, never exposed to the client) and performs
-- a single scoped DELETE ... WHERE contributor_id = $1.
create policy "anon can insert contributions"
    on public.corpus_contributions
    for insert
    to anon
    with check (true);
